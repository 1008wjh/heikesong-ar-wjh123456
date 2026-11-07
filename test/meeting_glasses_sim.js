"use strict";

/**
 * 多人会议眼镜端仿真：参考 test/glasses_sim.js 的替身方式，
 * 用 demo_meeting.wav 当麦克风，验证：
 *   模式选多人 → 第一片 15s/测试 5s 出 3 人 → 自动切片 → 返回归档。
 *
 * 跑法：node test/meeting_glasses_sim.js   （需要 8765 服务）
 */

const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");

const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
process.env.MEMORYLENS_BACKEND = BACKEND;
process.env.MEMORYLENS_ARCHIVE = process.env.MEMORYLENS_ARCHIVE || "1";
process.env.MEMORYLENS_FIRST_SLICE_MS = process.env.MEMORYLENS_FIRST_SLICE_MS || "5000";
process.env.MEMORYLENS_SLICE_MS = process.env.MEMORYLENS_SLICE_MS || "5000";

const texts = [];
let created = 0;
let failures = 0;

function check(name, cond, detail) {
  if (cond) console.log("  ok  " + name);
  else { failures++; console.log("  FAIL " + name + "  " + (detail || "")); }
}

function styleObject() {
  return { setBackgroundColor() {}, setTextColor() {}, setTextFont() {}, setTextAlign() {} };
}

const fusionui = {
  Color: { fromRGB8: (r, g, b) => ({ r, g, b }), fromRGBA8: (r, g, b, a) => ({ r, g, b, a }) },
  Font: { getCachedFont: () => ({}) },
  TextAlign: { AUTO: 0, TOP_LEFT: 1, LEFT_MID: 4, CENTER: 5, RIGHT_MID: 6 },
  Board: {
    create() {
      return { setParent() {}, setIgnoreLayout() {}, setSize() {}, getLocalStyle: styleObject,
        getRootWidget() { return {}; } };
    },
  },
  FusionRoot: { acquireFusionRootFromWindow: () => ({ getRootWidget: () => ({}) }) },
  Text: {
    create() {
      const node = {
        value: "", __w: 0, __h: 0,
        setParent() {}, setIgnoreLayout() {}, setPosition() {}, setSize() {},
        setAlignment() {}, resetAlignment() {}, updateLayout() {}, invalidate() {},
        getLocalStyle: styleObject,
        setText(v) { node.value = String(v); }, getText() { return node.value; },
        setLongMode() {}, resetLongMode() {}, setHeightByLineCount() {},
      };
      texts.push(node);
      created++;
      return node;
    },
  },
};

const navQueue = [];
let active = null;
let feedTimer = null;
const demo = fs.readFileSync(path.join(__dirname, "demo_meeting.wav"));
const pcm = demo.subarray(44);
const frames = [];
for (let i = 0; i + 640 <= pcm.length; i += 640) frames.push(pcm.subarray(i, i + 640));
let cursor = 0;

function nextFrame() {
  if (cursor >= frames.length) cursor = 0;
  return frames[cursor++];
}

const input = {
  getVoiceInputAvailability: () => ({ supported: true, declared: true, canRequest: true, ready: true, reason: "none" }),
  takeNavigationAction: () => (navQueue.length ? navQueue.shift() : null),
  takeTextInput: () => null,
  requestVoiceInput() {
    const session = {
      id: "meet-" + Date.now(), state: "recording", pending: [], ended: false,
      takeEvent() {
        const ev = session.pending.shift();
        if (ev && ev.type === "end") session.ended = true;
        return ev || null;
      },
      finish() {
        if (session.ended) return true;
        session.pending.push({ type: "end", status: "completed", reason: "none" });
        if (feedTimer) { clearInterval(feedTimer); feedTimer = null; }
        return true;
      },
      cancel() { return session.finish(); },
    };
    active = session;
    feedTimer = setInterval(() => {
      if (!active || active.ended || active !== session) return;
      for (let i = 0; i < 40 && active.pending.length < 64; i++) {
        const buf = nextFrame();
        active.pending.push({
          type: "audio", sampleRate: 16000, channels: 1, format: "s16le",
          sequence: 0, sampleOffset: 0, timeUs: 0, sampleCount: 320, data: buf,
        });
      }
    }, 40);
    return session;
  },
};

const os = {
  App: { createApp(cb) { setImmediate(() => cb("onCreate")); } },
  AppLifecycle: {
    ON_CREATE: "onCreate", ON_FOREGROUND: "onForeground", ON_REOPEN: "onReopen",
    ON_BACKGROUND: "onBackground", ON_DESTROY: "onDestroy",
  },
  WindowProperty: { create: () => ({ setTitle() {}, setSize() {} }) },
  Window: { create: () => ({}) },
  setDisplayKeepAwake: () => true,
};

const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "@irisview/osx/os") return os;
  if (request === "@irisview/osx/os/input") return input;
  if (request === "@irisview/osx/fusionui") return fusionui;
  return originalLoad.call(this, request, parent, isMain);
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitUntil(fn, ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (fn()) return true;
    await sleep(150);
  }
  return false;
}
function hud() { return texts.map((t) => t.value).filter((v) => v && v !== " "); }

(async () => {
  console.log("== 多人会议眼镜端仿真（" + BACKEND + "）");
  const health = await (await fetch(BACKEND + "/api/health")).json();
  check("后端在线", health.ok === true);

  require(path.join(__dirname, "..", "glasses", "src", "index.js"));
  await sleep(300);
  check("HUD 建好", created >= 12, "控件数=" + created);

  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("单人")), 5000);
  check("模式选择出现", hud().some((v) => v.includes("单人")), JSON.stringify(hud()));
  navQueue.push("right");           // 单人 → 多人
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("在听")), 6000);
  check("进入会议录音", hud().some((v) => v.includes("在听")), JSON.stringify(hud()));

  await waitUntil(() => hud().some((v) => v.includes("人") && v.includes("会议")), 15000);
  let lines = hud();
  check("首片出会议分人", lines.some((v) => v.includes("人") && (v.includes("王姐") || v.includes("会议"))),
    JSON.stringify(lines));

  navQueue.push("back");
  await waitUntil(() => hud().some((v) => v.includes("会议已保存")), 20000);
  lines = hud();
  check("返回后会议归档", lines.some((v) => v.includes("会议已保存")), JSON.stringify(lines));

  console.log(failures ? "\n" + failures + " 项失败" : "\n全部通过");
  process.exit(failures ? 1 : 0);
})().catch((e) => {
  console.error("崩了：" + (e && e.stack || e));
  process.exit(1);
});

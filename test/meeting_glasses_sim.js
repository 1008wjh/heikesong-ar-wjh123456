"use strict";

/**
 * 多人会议眼镜端仿真（不需要真模拟器）。
 *
 * 验证：
 *   选「多人」→ 会议主页（未录制）→ 确认开始录制 → 会议状态
 *   → 左键 AI 回答（小屏摘要）→ 右键关闭 → 上键文字键盘
 *   → 确认停止并保存（音频/总结）→ 返回退出到模式选择页
 *
 * 跑法：node test/meeting_glasses_sim.js   （需要 8765 后端在跑）
 */

const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");

const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
process.env.MEMORYLENS_BACKEND = BACKEND;
process.env.MEMORYLENS_MEETING_FIRST_SLICE_MS =
  process.env.MEMORYLENS_MEETING_FIRST_SLICE_MS || "5000";
process.env.MEMORYLENS_MEETING_SLICE_MS =
  process.env.MEMORYLENS_MEETING_SLICE_MS || "5000";

const texts = [];
let created = 0;
let failures = 0;

function check(name, cond, detail) {
  if (cond) console.log("  ok  " + name);
  else { failures++; console.log("  FAIL " + name + "  " + (detail || "")); }
}

function styleObject() {
  return {
    setBackgroundColor() {}, setTextColor() {}, setTextFont() {}, setTextAlign() {},
    setOpacity() {},
  };
}

const fusionui = {
  Color: { fromRGB8: (r, g, b) => ({ r, g, b }), fromRGBA8: (r, g, b, a) => ({ r, g, b, a }) },
  Font: { getCachedFont: () => ({}) },
  TextAlign: { AUTO: 0, TOP_LEFT: 1, LEFT_MID: 4, CENTER: 5, RIGHT_MID: 6 },
  Board: {
    create() {
      return {
        setParent() {}, setIgnoreLayout() {}, setSize() {}, setPosition() {},
        setHidden() {}, setOpacity() {}, getLocalStyle: styleObject,
        getRootWidget() { return {}; },
      };
    },
  },
  FusionRoot: { acquireFusionRootFromWindow: () => ({ getRootWidget: () => ({}) }) },
  Text: {
    create() {
      const node = {
        value: "", __w: 0, __h: 0,
        setParent() {}, setIgnoreLayout() {}, setPosition() {},
        setSize([w, h]) { node.__w = w; node.__h = h; },
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

// 真实按住键的顺序：队列里取
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
    // 2 帧 / 40ms = 1 倍速：和真实麦克风同节奏，切片时序才可信
    feedTimer = setInterval(() => {
      if (!active || active.ended || active !== session) return;
      for (let i = 0; i < 2 && active.pending.length < 64; i++) {
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
  requestAnimationFrame: (fn) => setTimeout(fn, 16),
  cancelAnimationFrame: (id) => clearTimeout(id),
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
    await sleep(200);
  }
  return false;
}
function hud() { return texts.map((t) => t.value).filter((v) => v && v !== " "); }
function has(re) { return hud().some((v) => re.test(v)); }

(async () => {
  console.log("== 多人会议 PRD 眼镜端仿真（" + BACKEND + "）");
  const health = await (await fetch(BACKEND + "/api/health")).json();
  check("后端在线", health.ok === true);

  require(path.join(__dirname, "..", "glasses", "src", "index.js"));
  await sleep(500);
  check("HUD 建好（含会议状态/浮窗控件）", created >= 18, "控件数=" + created);
  check("初始待机", has(/按一下/), JSON.stringify(hud().slice(0, 4)));

  // 1) 确认 → 模式选择 → 右 → 多人 → 确认
  navQueue.push("activate");
  await waitUntil(() => has(/单人/), 5000);
  check("模式选择出现", has(/单人/), JSON.stringify(hud()));
  navQueue.push("right");
  navQueue.push("activate");
  await waitUntil(() => has(/多人会议/), 8000);
  check("进入会议模块（未录制）", has(/未录制/), JSON.stringify(hud()));
  check("显示多人会议标题", has(/^多人会议$/), JSON.stringify(hud()));

  // 2) 确认开始录制
  navQueue.push("activate");
  await waitUntil(() => has(/录制中/), 8000);
  check("确认键开始录制", has(/录制中/), JSON.stringify(hud()));

  // 3) 等第一片转写回来（真实 ASR + 分人约 3-5s）；不再渲染底部字幕，只看会议状态/卡片
  const gotState = await waitUntil(() => has(/待确认|完成|处理中|会议中/), 40000);
  check("第一片处理后有会议状态", gotState, JSON.stringify(hud().slice(0, 12)));

  // 4) 左键 → AI 浮窗
  navQueue.push("left");
  const gotAi = await waitUntil(() => has(/^AI 回答/), 15000);
  check("左键弹出 AI 回答浮窗", gotAi, JSON.stringify(hud().slice(0, 12)));
  check("浮窗有要点内容", has(/核心问题|发言人|王姐|会议/), JSON.stringify(hud().slice(0, 16)));

  // 5) 右键关闭浮窗
  navQueue.push("right");
  await sleep(600);
  check("右键关闭浮窗", !has(/^AI 回答/), JSON.stringify(hud().slice(0, 12)));
  check("右键关浮窗不停止录音", has(/录制中/), JSON.stringify(hud().slice(0, 12)));

  // 6) 浮窗关闭时上键打开文字键盘（用文字提问/发指令）
  navQueue.push("up");
  const gotKeys = await waitUntil(() => has(/打字 · 方向键选/), 5000);
  check("上键打开文字键盘", gotKeys, JSON.stringify(hud().slice(0, 12)));
  navQueue.push("back");
  await sleep(400);
  check("返回关掉文字键盘", !has(/打字 · 方向键选/), JSON.stringify(hud().slice(0, 12)));

  // 7) 再左键刷新 + 确认停止并保存
  navQueue.push("left");
  await waitUntil(() => has(/^AI 回答/), 10000);
  navQueue.push("activate");
  const saved = await waitUntil(() => has(/会议已保存/), 60000);
  check("确认键停止并保存会议", saved, JSON.stringify(hud().slice(0, 12)));
  check("停止后状态回到未录制", has(/未录制/) || has(/再录/), JSON.stringify(hud().slice(0, 12)));
  check("停录后只提示保存，不在眼镜上显示总结",
    has(/会议已保存/) && !has(/总结/), JSON.stringify(hud().slice(0, 12)));

  // 8) 返回退出到模块选择页
  navQueue.push("back");
  await waitUntil(() => has(/单人/) && !has(/多人会议/), 8000);
  check("返回退出到模式选择页", has(/单人/) && !has(/多人会议/), JSON.stringify(hud().slice(0, 12)));

  // 9) 后端确实落了会议 + 转写 + AI 历史
  const listing = await (await fetch(BACKEND + "/api/meetings")).json();
  const latest = (listing.items || [])[0];
  check("会议列表里有这场会", !!latest, JSON.stringify(listing.items && listing.items.slice(0, 2)));
  if (latest) {
    check("会议有转写段", (latest.segment_count || 0) >= 1,
      "segment_count=" + latest.segment_count);
    check("会议有总结", !!latest.summary_text,
      JSON.stringify({ summary: latest.summary, summary_text: latest.summary_text }));
    check("会议音频已留存", !!latest.audio_file_path,
      String(latest.audio_file_path || ""));
    const detail = await (await fetch(BACKEND + "/api/meetings/" + latest.session_id)).json();
    check("会议详情有 AI 问答历史", (detail.answers || []).length >= 1,
      JSON.stringify((detail.answers || []).slice(-1)));
    check("会议详情有按段转写", (detail.segments || []).length >= 1,
      "segments=" + (detail.segments || []).length);
  }

  console.log(failures ? "\n" + failures + " 项失败" : "\n全部通过");
  process.exit(failures ? 1 : 0);
})().catch((e) => {
  console.error("崩了：" + (e && e.stack || e));
  process.exit(1);
});

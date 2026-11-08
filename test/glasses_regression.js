"use strict";
/*
 * Deterministic regression tests for glasses/src/index.js.
 *
 * No backend and no external API: global.fetch is a stub, the IrisView SDK is
 * replaced by an in-memory mock and animation frames are driven explicitly.
 *
 * Run: node test/glasses_regression.js
 *
 * Covered:
 *   1. long card auto-scroll actually changes the visible rows (rowOffset)
 *   2. mode page keeps 【】 selection; background polling does not overwrite it
 *   3. down/up still repaint after ON_BACKGROUND -> ON_FOREGROUND (rAF restart)
 *   4. a preset pressed with no fresh audio sends audio as a string, not an object
 */

const path = require("node:path");
const Module = require("node:module");

process.env.MEMORYLENS_BACKEND = "http://fake.local";
process.env.MEMORYLENS_IDENTIFY_MS = "600";
process.env.MEMORYLENS_BUTTON_DELAY_MS = "80";
process.env.MEMORYLENS_PRESET_HOLD_MS = "3000";
process.env.MEMORYLENS_FAKE_MIC = "0";
process.env.MEMORYLENS_AUTO_LISTEN = "0";

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log("  ok   " + name);
  else { failures++; console.log("  FAIL " + name + (detail ? "\n       " + detail : "")); }
}

// ---------------- FusionUI mock ----------------
const texts = [];
function style() {
  return {
    setTextColor() {}, setTextFont() {}, setTextAlign() {},
    setBackgroundColor() {}, setImageOpacity() {}, setOpacity() {},
  };
}
function widget(extra) {
  return Object.assign({
    setParent() {}, setIgnoreLayout() {}, setIgnoreClipping() {},
    setPosition() {}, setSize() {}, getLocalStyle: style,
    setHidden() {}, invalidate() {}, setFocused() {}, getFocused() { return false; },
    getRootWidget() { return widget(); },
  }, extra || {});
}
const fusionui = {
  Color: { fromRGB8: () => ({}) },
  Font: { getCachedFont: () => ({}) },
  TextAlign: { AUTO: 0, TOP_LEFT: 1, LEFT_MID: 4, CENTER: 5, RIGHT_MID: 6, LEFT: 3 },
  Board: { create: () => widget() },
  FusionRoot: { acquireFusionRootFromWindow: () => ({ getRootWidget: () => widget() }) },
  Select: { create: () => widget() },
  Text: {
    create() {
      const n = {
        value: "", __text: undefined,
        setParent() {}, setIgnoreLayout() {}, setPosition() {},
        setSize() {}, getLocalStyle: style,
        setText(v) { n.value = String(v); },
        getText() { return n.value; },
        setHidden() {}, invalidate() {},
      };
      texts.push(n);
      return n;
    },
  },
};

// ---------------- OS / frame mock ----------------
const raf = new Map();
let nextRaf = 1;
function requestAnimationFrame(cb) { const id = nextRaf++; raf.set(id, cb); return id; }
function cancelAnimationFrame(id) { raf.delete(id); }
function runFrames(n) {
  for (let k = 0; k < (n || 1); k++) {
    const cbs = Array.from(raf.entries());
    for (const [id, cb] of cbs) {
      if (raf.get(id) !== cb) continue;
      raf.delete(id);
      try { cb(Date.now()); } catch (e) { failures++; console.log("  FAIL frame callback: " + (e && e.stack || e)); }
    }
  }
}
const LIFE = {
  ON_CREATE: "onCreate", ON_FOREGROUND: "onForeground", ON_REOPEN: "onReopen",
  ON_BACKGROUND: "onBackground", ON_DESTROY: "onDestroy",
};
const os = {
  App: { createApp(cb) { os._cb = cb; setImmediate(() => cb(LIFE.ON_CREATE)); } },
  AppLifecycle: LIFE,
  WindowProperty: { create: () => ({ setTitle() {}, setSize() {} }) },
  Window: { create: () => ({ closed: false }) },
  requestAnimationFrame, cancelAnimationFrame,
  setDisplayKeepAwake: () => true,
};

// ---------------- input mock ----------------
const navQueue = [];
let voiceFactory = null;
let voiceEvents = [];
let voiceSession = null;
const input = {
  isNavigationAvailable: () => true,
  takeNavigationAction: () => (navQueue.length ? navQueue.shift() : null),
  takeTextInput: () => null,
  requestWindowFocus: () => true,
  getVoiceInputAvailability: () => ({ supported: true, declared: true, canRequest: true, reason: "none" }),
  requestVoiceInput: () => voiceFactory(),
};

// ---------------- fake backend ----------------
const requests = [];
let latest = { faces: [], rev: 1 };
function json(obj) { return { ok: true, status: 200, json: async () => obj }; }
async function fakeFetch(url, opts) {
  const u = String(url).replace("http://fake.local", "");
  let body = null;
  if (opts && opts.body) { try { body = JSON.parse(opts.body); } catch (e) { body = opts.body; } }
  requests.push({ url: u, body });
  if (u === "/api/latest") return json({ ok: true, rev: latest.rev, at: Date.now(), faces: latest.faces });
  if (u === "/api/settings") return json({ ok: true, hold_sec: 30, first_hold_sec: 9, identify_sec: 20, system_prompt: "" });
  if (u === "/api/presets") {
    return json({ ok: true, items: [
      { id: "hint", label: "提词", prompt: "hint" },
      { id: "todo", label: "待办", prompt: "todo" },
      { id: "recall", label: "以前", prompt: "recall" },
    ] });
  }
  if (u === "/api/voice/identify") {
    return json({
      status: "known", person_id: "p1", name: "王姐",
      card: { status: "known", name: "王姐", role: "产品经理", lines: ["答应给报价单"], similarity: 0.9 },
    });
  }
  if (u === "/api/agent/feed") {
    return json({
      ok: true, rev: (latest.rev || 1) + 1, ms: 5,
      view: { state: "完成", person: "王姐", person_id: "p1", rows: ["第一条", "第二条", "第三条"], similarity: 0.9 },
    });
  }
  if (u === "/api/agent/cancel") return json({ ok: true });
  return json({ ok: true });
}
global.fetch = fakeFetch;

// ---------------- load the app ----------------
const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "@irisview/osx/os") return os;
  if (request === "@irisview/osx/os/input") return input;
  if (request === "@irisview/osx/fusionui") return fusionui;
  return originalLoad.call(this, request, parent, isMain);
};
require(path.join(__dirname, "..", "glasses", "src", "index.js"));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const hud = () => texts.map((t) => t.value);
const has = (s) => hud().some((v) => v.includes(s));
async function nav(action) {
  navQueue.push(action);
  await sleep(150);
  runFrames(2);
}
const card8 = {
  kind: "agent", status: "known", name: "滚动测试", role: "", state: "完成",
  lines: ["第一行", "第二行", "第三行", "第四行", "第五行", "第六行", "第七行", "第八行"],
  similarity: 0.9,
};

(async () => {
  await sleep(60);
  runFrames(3);

  // 1) auto-scroll must change visible rows
  latest = { rev: 10, faces: [card8] };
  await sleep(850);
  runFrames(3);
  check("长卡片首屏显示第一行", has("第一行"), JSON.stringify(hud()));
  await sleep(1500);              // SCROLL_MS = 1400
  runFrames(3);
  check("自动滚动后不再停在第一行", !has("第一行"), JSON.stringify(hud()));
  check("自动滚动后显示第二行", has("第二行"), JSON.stringify(hud()));

  // back to idle
  latest = { rev: 11, faces: [] };
  await sleep(850);
  runFrames(2);
  check("清空 latest 后回到待机", has("按一下开始听"), JSON.stringify(hud()));

  // 2) mode page survives background polling
  await nav("activate");
  check("确认打开模式页", has("选模式") && has("【单人】"), JSON.stringify(hud()));
  await sleep(850);               // one poll tick without running frames
  check("poll 不会覆盖模式页", has("选模式") && has("【单人】") && !has("按一下开始听"), JSON.stringify(hud()));
  await nav("down");
  check("下键切到【多人】", has("【多人】"), JSON.stringify(hud()));
  await nav("up");
  check("上键切回【单人】", has("【单人】"), JSON.stringify(hud()));

  // 3) background -> foreground must restore the frame loop
  os._cb(LIFE.ON_BACKGROUND);
  await sleep(30);
  os._cb(LIFE.ON_FOREGROUND);
  await sleep(30);
  navQueue.push("down");
  await sleep(160);
  runFrames(4);
  check("回前台后下键仍能重绘到【多人】", has("【多人】"), JSON.stringify(hud()));
  navQueue.push("up");
  await sleep(160);
  runFrames(4);
  check("回前台后上键仍能重绘到【单人】", has("【单人】"), JSON.stringify(hud()));

  // 4) no fresh audio + preset: audio must stay a string, not the queue object
  voiceFactory = () => {
    voiceEvents = [];
    const s = {
      id: "s1", mode: "audio", state: "recording",
      takeEvent() { return voiceEvents.shift() || null; },
      finish() { s.state = "ended"; voiceEvents.push({ type: "end", status: "completed", reason: "none" }); return true; },
      cancel() { s.state = "ended"; voiceEvents.push({ type: "end", status: "cancelled", reason: "user-cancelled" }); return true; },
    };
    voiceSession = s;
    return s;
  };
  await nav("activate");          // enter single mode
  await sleep(500);
  runFrames(2);
  check("进入单人模式并开始听", has("在听") || has("识别人"), JSON.stringify(hud()));
  check("麦克风会话已建立", !!voiceSession);
  await nav("activate");          // start identify
  check("确认后进入识别人", has("识别人"), JSON.stringify(hud()));
  // feed audio only after identify starts, otherwise it lands in c.chunks and
  // identChunks stays empty ("没录到声音")
  for (let i = 0; i < 40; i++) {
    const buf = Buffer.alloc(640);
    for (let j = 0; j < buf.length; j += 2) buf.writeInt16LE(1600, j);
    voiceEvents.push({
      type: "audio", sampleRate: 16000, channels: 1, format: "s16le",
      sequence: i, sampleOffset: i * 320, timeUs: i * 20000, sampleCount: 320, data: buf,
    });
  }
  await sleep(1000);              // IDENTIFY_MS = 600
  runFrames(3);
  check("识别人返回王姐", has("王姐"), JSON.stringify(hud()));
  await sleep(200);               // BUTTON_DELAY_MS = 80
  runFrames(2);
  check("认出后出现预设按钮", has("提词") || has("待办"), JSON.stringify(hud()));
  await nav("activate");          // press preset with no fresh audio
  await sleep(400);
  const feeds = requests.filter((r) => r.url === "/api/agent/feed");
  const feed = feeds[feeds.length - 1];
  check("按预设确实发了 /api/agent/feed", !!feed, JSON.stringify(requests.slice(-6)));
  check("无新音频时 audio 是字符串而不是队列对象",
    !!feed && typeof feed.body.audio === "string", feed ? JSON.stringify(feed.body) : "no feed");
  check("没有出现智能体出错的卡片", !has("智能体出错了"), JSON.stringify(hud()));

  console.log(failures ? ("\nFAILED: " + failures) : "\nALL PASS");
  process.exit(failures ? 1 : 0);
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(2);
});

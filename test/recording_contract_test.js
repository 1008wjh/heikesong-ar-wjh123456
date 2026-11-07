"use strict";

/**
 * 录音契约测试（不依赖真实后端 / 模拟器）：
 *  - 15 秒首片 + 55 秒后续片；
 *  - 55 秒轮换会话时，必须等旧会话 end 消费完再请求新会话（官方 busy 契约）；
 *  - 切片 WAV 长度不能被 30 秒常量截断。
 *
 * 跑法：node test/recording_contract_test.js
 */

const path = require("node:path");
const Module = require("node:module");

const FRAME_BYTES = 640;          // 20ms / 16k / mono / s16le
const BYTES_PER_SEC = 32000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let fakeNow = 1_000_000;
Date.now = () => fakeNow;

let navQueue = [];
let active = null;
let busyCount = 0;
let sessionSeq = 0;
let pendingFrames = [];
let durations = [];
let seq = 0;
let sampleOffset = 0;

function pcmFrame(seed) {
  const b = Buffer.alloc(FRAME_BYTES);
  for (let i = 0; i < b.length; i += 2) b.writeInt16LE(((seed + i) % 2000) - 1000, i);
  return b;
}

function makeEvents(seconds) {
  const frames = Math.round((seconds * BYTES_PER_SEC) / FRAME_BYTES);
  const out = [];
  for (let i = 0; i < frames; i++) {
    out.push({
      type: "audio", sampleRate: 16000, channels: 1, format: "s16le",
      sequence: seq++, sampleOffset, timeUs: Math.floor(sampleOffset * 1_000_000 / 16000),
      sampleCount: FRAME_BYTES / 2, data: pcmFrame(i),
    });
    sampleOffset += FRAME_BYTES / 2;
  }
  return out;
}

function feedSeconds(seconds) {
  const evs = makeEvents(seconds);
  if (active && !active.ended) active.queue.push(...evs);
  else pendingFrames.push(...evs);
}

const input = {
  getVoiceInputAvailability: () => ({ supported: true, declared: true, canRequest: true, ready: true, reason: "none" }),
  takeNavigationAction: () => (navQueue.length ? navQueue.shift() : null),
  takeTextInput: () => null,
  requestVoiceInput() {
    if (active && !active.ended) {
      busyCount += 1;
      const e = new Error("busy");
      e.code = "busy";
      throw e;
    }
    const session = {
      id: "s" + (++sessionSeq),
      mode: "audio",
      state: "recording",
      ended: false,
      endedByFinish: false,
      queue: pendingFrames.splice(0),
      takeEvent() {
        if (session.ended) return null;
        const ev = session.queue.shift();
        if (!ev) return null;
        if (ev.type === "end") {
          session.ended = true;
          if (active === session) active = null;
        }
        return ev;
      },
      finish() {
        if (session.endedByFinish) return true;
        session.endedByFinish = true;
        session.queue.push({ type: "end", status: "completed", reason: "none" });
        return true;
      },
      cancel() {
        session.queue.push({ type: "end", status: "cancelled", reason: "user-cancelled" });
        return true;
      },
    };
    active = session;
    return session;
  },
};

const texts = [];
const fusionui = {
  Color: { fromRGB8: () => ({}) },
  Font: { getCachedFont: () => ({}) },
  TextAlign: { LEFT_MID: 4, CENTER: 5 },
  Board: { create: () => ({ setParent() {}, setIgnoreLayout() {}, setSize() {}, getLocalStyle: () => ({ setBackgroundColor() {} }) }) },
  FusionRoot: { acquireFusionRootFromWindow: () => ({ getRootWidget: () => ({ setIgnoreLayout() {}, setSize() {} }) }) },
  Text: {
    create() {
      const t = {
        value: "", __text: undefined,
        setParent() {}, setIgnoreLayout() {}, setPosition() {}, setSize() {},
        setText(v) { t.value = String(v); }, getText() { return t.value; },
        getLocalStyle() { return { setTextColor() {}, setTextFont() {}, setTextAlign() {} }; },
      };
      texts.push(t);
      return t;
    },
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

// 拦截网络：只记录 /api/agent/feed 的 WAV 时长
global.fetch = async (url, opts) => {
  const u = String(url);
  if (u.endsWith("/api/agent/feed")) {
    const body = JSON.parse(opts.body);
    const b64 = String(body.audio || "").split(",")[1] || "";
    const buf = Buffer.from(b64, "base64");
    durations.push((buf.length - 44) / BYTES_PER_SEC);
    return { json: async () => ({ view: { person_id: "p1", person: "王姐", similarity: 0.9, rows: ["r"], state: "完成", mode: "single" }, rev: durations.length }) };
  }
  if (u.endsWith("/api/latest")) return { json: async () => ({ faces: [], rev: 0, ms: 0 }) };
  return { json: async () => ({ ok: true }) };
};

process.env.MEMORYLENS_ARCHIVE = "0";
require(path.join(__dirname, "..", "glasses", "src", "index.js"));

(async () => {
  await sleep(200);
  navQueue.push("activate");       // 打开模式选择
  await sleep(200);
  navQueue.push("activate");       // 进入单人持续听

  // 等会话建立
  for (let i = 0; i < 50 && !active; i++) await sleep(20);
  if (!active) throw new Error("没有建立音频会话");

  // 第一片：15 秒
  feedSeconds(15);
  for (let i = 0; i < 100 && durations.length < 1; i++) await sleep(20);

  // 把假时间推过 55 秒，让代码走“主动换会话”路径
  fakeNow += 56_000;
  await sleep(150);                // 让 pump 触发 finish / end
  for (let i = 0; i < 50 && !active; i++) await sleep(20);
  if (!active) throw new Error("55 秒轮换后没有开新会话（busy 没有被正确等待）");

  // 第二片：55 秒
  feedSeconds(55);
  for (let i = 0; i < 150 && durations.length < 2; i++) await sleep(20);

  const ok = durations.length >= 2
    && Math.abs(durations[0] - 15) < 0.05
    && Math.abs(durations[1] - 55) < 0.05
    && busyCount === 0;

  console.log("durations=" + JSON.stringify(durations));
  console.log("busy=" + busyCount);
  if (!ok) {
    console.error("FAIL: 期望 15s + 55s，且无 busy");
    process.exit(1);
  }
  console.log("ok 录音契约：15s + 55s，55 秒轮换无 busy");
  process.exit(0);
})().catch((e) => {
  console.error("崩了：" + (e && e.stack || e));
  process.exit(1);
});

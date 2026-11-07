"use strict";

/**
 * 全流程追踪：进单人 → 说话 → 看 HUD 每一秒变成什么。
 * 用来复现/验证「点了确认直接冒出陌生人A」这类问题。
 *
 *   node tools/flow_trace.js            # 喂静音（复现"没说话也建号"）
 *   node tools/flow_trace.js voice      # 喂一段真人声（huihui_b.wav）
 */

const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");

const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
process.env.MEMORYLENS_BACKEND = BACKEND;
process.env.MEMORYLENS_ARCHIVE = process.env.MEMORYLENS_ARCHIVE || "0";

const MODE = process.argv[2] === "voice" ? "voice" : "silence";

const texts = [];
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
        setParent() {}, setIgnoreLayout() {}, setPosition() {},
        setSize([w, h]) { node.__w = w; node.__h = h; },
        setAlignment() {}, resetAlignment() {}, updateLayout() {}, invalidate() {},
        getLocalStyle: styleObject,
        setText(v) { node.value = String(v); },
        getText() { return node.value; },
        setLongMode() {}, resetLongMode() {}, setHeightByLineCount() {},
      };
      texts.push(node);
      return node;
    },
  },
};

const navQueue = [];
let session = null;
let feedTimer = null;

// 静音：每 40ms 送 2 帧（40ms 音频）
const SILENT_FRAME = Buffer.alloc(640, 0);
const voiceWav = MODE === "voice"
  ? fs.readFileSync(path.join(__dirname, "..", "test", "voice", "huihui_b.wav"))
  : null;
let pcm = voiceWav ? voiceWav.subarray(44) : null;   // 去掉 wav 头
let pcmPos = 0;

function nextFrame() {
  if (!pcm) return SILENT_FRAME;
  if (pcmPos + 640 > pcm.length) pcmPos = 0;
  const f = pcm.subarray(pcmPos, pcmPos + 640);
  pcmPos += 640;
  return f;
}

const input = {
  getVoiceInputAvailability: () => ({ available: true, canRequest: true, reason: "none" }),
  takeNavigationAction: () => (navQueue.length ? navQueue.shift() : null),
  takeTextInput: () => null,
  requestVoiceInput() {
    session = {
      id: "trace-" + Date.now(),
      state: "recording",
      takeEvent() {
        if (!this.pending) this.pending = [];
        return this.pending.length ? this.pending.shift() : null;
      },
      finish() { this.state = "ended"; if (feedTimer) { clearInterval(feedTimer); feedTimer = null; } },
      cancel() { this.finish(); },
    };
    feedTimer = setInterval(() => {
      if (!session || session.state !== "recording") return;
      for (let i = 0; i < 2; i++) {
        const buf = nextFrame();
        session.pending.push({
          type: "audio", sampleRate: 16000, channels: 1, format: "s16le",
          sampleCount: 320, data: new Uint8Array(buf),
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
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "@irisview/osx/fusionui") return fusionui;
  if (request === "@irisview/osx/os") return os;
  if (request === "@irisview/osx/os/input") return input;
  return origLoad.apply(this, arguments);
};

function rows() {
  return texts.map((t) => t.value).filter((v) => v && v !== " ");
}

(async () => {
  require(path.join(__dirname, "..", "glasses", "src", "index.js"));
  await new Promise((r) => setTimeout(r, 600));

  console.log("模式:", MODE, "| 初始:", rows().join(" / "));

  navQueue.push("activate");                 // 待机 → 选模式
  await new Promise((r) => setTimeout(r, 800));
  console.log("按确认后:", rows().join(" / "));

  navQueue.push("activate");                 // 选单人 → 开录
  for (let i = 1; i <= Number(process.env.TRACE_SECS || 24); i++) {
    await new Promise((r) => setTimeout(r, 1000));
    console.log(String(i).padStart(2) + "s:", rows().join(" / "));
  }

  navQueue.push("back");                     // 返回结束
  await new Promise((r) => setTimeout(r, 6000));
  console.log("结束后:", rows().join(" / "));
  process.exit(0);
})();

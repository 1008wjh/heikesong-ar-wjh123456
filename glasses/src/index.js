"use strict";

/**
 * MemoryLens 眼镜端（IrisView / FusionUI）
 *
 * 分工（v2：声纹为主）：
 *   眼镜  ——  按一下就开始听，采 16k 单声道 PCM，发到 /api/voice/scan，把结果画在镜片上
 *   手机/电脑 ——  可选：拍照走人脸，结果同样进 /api/latest，眼镜会一起显示
 *
 * 为什么改成声纹：IrisView SDK 里没有任何摄像头 API（kit 全量字节扫过，0 命中），
 * 但麦克风是正式能力（input.audio，16kHz/mono/s16le/20ms 一帧），
 * 而"听声辨人"正好把硬件用满，也不用举着手机对脸。
 */

const os = require("@irisview/osx/os");
const { App, AppLifecycle, Window, WindowProperty } = os;
const input = require("@irisview/osx/os/input");
const { Board, Color, Font, FusionRoot, Text, TextAlign } = require("@irisview/osx/fusionui");
const path = require("node:path");
const { createOutbox } = require("./outbox");

// 眼镜连哪台电脑。模拟器上就是本机；真机换成同一局域网里跑 server.py 的那台。
const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
const POLL_MS = 700;        // 拉结果
const NAV_MS = 120;         // 读按键/手势
const HTTP_TIMEOUT_MS = 1500;
const MAX_SAMPLES = 16000 * 30;
const OUTBOX_RETRY_MS = 8000;   // 离线录音的重发间隔

// 宿主注入的私有数据目录；没有（单测 / 无沙箱环境）就不落盘。
const APP_DATA_DIR = process.env.FUSIONUI_APP_DATA_DIR;
const outbox = createOutbox({
  post: (p, b) => postJson(p, b),
  dir: APP_DATA_DIR && path.isAbsolute(APP_DATA_DIR) ? path.join(APP_DATA_DIR, "outbox") : null,
  log: (m) => console.log(m),
});

const W = 640;
const H = 480;

const COL = {
  cyan: Color.fromRGB8(53, 224, 208),
  amber: Color.fromRGB8(255, 180, 84),
  white: Color.fromRGB8(236, 242, 244),
  gray: Color.fromRGB8(146, 160, 167),
  dim: Color.fromRGB8(84, 96, 102),
};

const retained = {};
let rafId = null;
let lastPaintAt = 0;
let lastScene = null;          // {card, rev, ms} 或 {idle:"..."}，用来周期性重绘
let pollTimer = null;
let navTimer = null;
let outboxTimer = null;
let inFlight = false;
let shownRev = -1;
let background = false;

// 实测结论（simulator）：单个 Text 只画得出一百多像素宽，再长就被硬切。
// 所以一行文字拆成若干个 Text，横向拼起来（每块 x 依次 +124）。
const CHUNK_PX = 124;             // 每块占的横向宽度
const CHUNK_TEXT_PX = 112;        // 每块能画出的文字宽度

const putText = (t, s) => {
  t.setText(s);
  t.setSize([t.__w, t.__h, 0]);
};

function makeText(board, x, y, w, h, size, color, align) {
  const t = Text.create();
  t.setParent(board);
  t.setIgnoreLayout(true);        // 子控件自己摆位置，别让 Board 的布局排它
  t.setPosition([x, y, 0]);
  t.setSize([w, h, 0]);
  t.__w = w; t.__h = h;
  const style = t.getLocalStyle();
  style.setTextColor(color);
  const font = Font.getCachedFont("", size, 0, 0);
  if (font) style.setTextFont(font);
  style.setTextAlign(align || TextAlign.LEFT_MID);

  return t;
}

// 一行 = 若干个 Text 拼起来。y 固定，x 从 left 开始每块 +CHUNK_PX。
function makeSlot(board, left, y, size, color, pieces) {
  const tiles = [];
  for (let i = 0; i < pieces; i++) {
    tiles.push(makeText(board, left + i * CHUNK_PX, y, CHUNK_TEXT_PX, size + 12, size, color));
  }
  return { tiles, size };
}

// 按可用宽度把字符串切成块，再逐块塞进 slot.tiles
const CJK = /[\u3000-\u9fff\uff00-\uffef]/;

function putSlot(slot, text) {
  const stride = CHUNK_TEXT_PX / slot.size;          // 每块能放多少个"字宽"
  const out = [];
  let cur = "";
  let w = 0;
  for (const ch of String(text || "")) {
    const cw = CJK.test(ch) ? 1 : 0.55;
    if (w + cw > stride && cur) { out.push(cur); cur = ""; w = 0; }
    cur += ch;
    w += cw;
  }
  if (cur) out.push(cur);
  slot.tiles.forEach((t, i) => putText(t, out[i] || ""));
  return out;
}

function buildHud() {
  const property = WindowProperty.create();
  property.setTitle("MemoryLens");
  property.setSize([0.64, 0.48, 0.01]);   // 视口按米给，内容按像素排
  retained.window = Window.create(property);

  retained.root = FusionRoot.acquireFusionRootFromWindow(retained.window);
  // 根控件默认只有 100x100（mm）那么大，超出部分会被裁掉 —— 先把它撑到够大。
  try {
    const rw = retained.root.getRootWidget();
    rw.setIgnoreLayout(true);
    rw.setSize([2000, 2000, 0]);
  } catch (e) {
  }

  retained.board = Board.create();
  retained.board.setParent(retained.root.getRootWidget());
  retained.board.setIgnoreLayout(true);
  try { retained.board.setIgnoreClipping(true); } catch (e) { /* optional */ }
  retained.board.setSize([W, H, 0]);
  retained.board.getLocalStyle().setBackgroundColor(Color.fromRGB8(0, 0, 0));

  // 镜片上单个 Text 只画得出一百一十几像素，所以整块 HUD 就按这个宽度排成窄列。
  retained.brand = makeText(retained.board, 8, 24, 320, 30, 18, COL.cyan);
  putText(retained.brand, "● MemoryLens");

  retained.status = makeText(retained.board, 8, 58, 200, 24, 14, COL.dim);
  putText(retained.status, "按一下开始听…");

  retained.name = makeText(retained.board, 8, 96, 200, 46, 28, COL.white);
  retained.role = makeText(retained.board, 8, 150, 200, 26, 16, COL.cyan);
  retained.sim = makeText(retained.board, 8, 180, 200, 22, 13, COL.dim);

  retained.info = [0, 1, 2, 3, 4, 5].map((i) =>
    makeText(retained.board, 8, 216 + i * 30, 200, 28, 16, COL.white));

  retained.foot = makeText(retained.board, 8, H - 40, 200, 22, 13, COL.dim);
  putText(retained.foot, "按一下就说");
}
// ---------- 网络 ----------

function getJson(path) {
  if (typeof fetch === "function") {
    const signal = typeof AbortSignal !== "undefined" && AbortSignal.timeout
      ? AbortSignal.timeout(HTTP_TIMEOUT_MS) : undefined;
    return fetch(BACKEND + path, { signal, cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  const http = require("node:http");
  return new Promise((resolve, reject) => {
    const req = http.get(BACKEND + path, { timeout: HTTP_TIMEOUT_MS }, (res) => {
      let buf = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { buf += c; });
      res.on("end", () => {
        try { resolve(JSON.parse(buf)); } catch (e) { reject(e); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

function postJson(path, body) {
  if (typeof fetch === "function") {
    const signal = typeof AbortSignal !== "undefined" && AbortSignal.timeout
      ? AbortSignal.timeout(8000) : undefined;
    return fetch(BACKEND + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
    }).then((r) => r.json());
  }
  const http = require("node:http");
  const payload = Buffer.from(JSON.stringify(body));
  return new Promise((resolve, reject) => {
    const req = http.request(BACKEND + path, {
      method: "POST",
      timeout: 8000,
      headers: { "Content-Type": "application/json", "Content-Length": payload.length },
    }, (res) => {
      let buf = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { buf += c; });
      res.on("end", () => {
        try { resolve(JSON.parse(buf)); } catch (e) { reject(e); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

// ---------- WAV 封装：16kHz / 单声道 / 16bit ----------

function toWav(chunks, totalBytes) {
  const n = Math.min(totalBytes, MAX_SAMPLES * 2);
  const buf = Buffer.alloc(44 + n);
  buf.write("RIFF", 0, "ascii");
  buf.writeUInt32LE(36 + n, 4);
  buf.write("WAVE", 8, "ascii");
  buf.write("fmt ", 12, "ascii");
  buf.writeUInt32LE(16, 16);         // fmt 块长度
  buf.writeUInt16LE(1, 20);          // PCM
  buf.writeUInt16LE(1, 22);          // 单声道
  buf.writeUInt32LE(16000, 24);      // 采样率
  buf.writeUInt32LE(32000, 28);      // 字节率
  buf.writeUInt16LE(2, 32);          // 块对齐
  buf.writeUInt16LE(16, 34);         // 位深
  buf.write("data", 36, "ascii");
  buf.writeUInt32LE(n, 40);
  let off = 44;
  for (const c of chunks) {
    if (off - 44 >= n) break;
    const take = Math.min(c.length, 44 + n - off);
    buf.set(c.subarray(0, take), off);
    off += take;
  }
  return "data:audio/wav;base64," + buf.toString("base64");
}

// ---------- 画 ----------


// 单行能画出的宽度 ≈ 110px，按"字宽"把长句切成小段（16px 下一段 6 个汉字）
const CJK_CH = /[\u3000-\u9fff\uff00-\uffef]/;

function splitLines(text, budget) {
  const out = [];
  let cur = "", w = 0;
  for (const ch of String(text || "")) {
    const cw = CJK_CH.test(ch) ? 1 : 0.55;
    if (w + cw > budget && cur) { out.push(cur); cur = ""; w = 0; }
    cur += ch; w += cw;
  }
  if (cur) out.push(cur);
  return out;
}

function paintIdle(msg) {
  stopScroll();
  viewMode = "idle";
  complexRows = null;
  complexKey = "";
  lastScene = { idle: msg };
  putText(retained.status, msg);
  putText(retained.name, "");
  putText(retained.role, "");
  putText(retained.sim, "");
  retained.info.forEach((t) => putText(t, ""));
  putText(retained.foot, "按一下就说");
}

function paintOffline(err) {
  stopScroll();
  putText(retained.status, "后台没连上");
  putText(retained.name, "眼镜在线");
  putText(retained.role, "");
  putText(retained.sim, "");
  const rows = splitLines("电脑上跑 start.bat", 6);
  retained.info.forEach((t, i) => putText(t, rows[i] || ""));
  putText(retained.foot, "按一下就说");
}

function paintCard(card, rev, ms) {
  const known = card.status !== "unknown";
  const kind = card.kind === "voice" ? "听到" : "看到";
  viewMode = "simple";
  complexRows = null;
  putText(retained.status,
    card.status === "short" ? "没听清 · 再说一次"
      : known ? kind + " · " + (ms || 0) + "ms"
        : (card.kind === "voice" ? "陌生的声音" : "陌生人 · 不认识"));
  putText(retained.name, card.name || "陌生人");
  putText(retained.role, card.role || "");
  putText(retained.sim, known && typeof card.similarity === "number"
    ? "SIM " + card.similarity.toFixed(2) : "");

  // tick 每 400ms 会重画一次同一张卡，别把滚动位置重置了
  const same = lastScene && lastScene.card === card && lastScene.rev === rev;
  lastScene = { card, rev, ms };       // 先更新，页脚提示才拿得到这张卡的状态
  if (!same) {
    cardRows = [];
    for (const ln of (card.lines || [])) cardRows.push(...splitLines(ln, 6));
    rowOffset = 0;
  }
  paintRows();
  syncScroll();
  paintFootHint();
  shownRev = rev;
}

// 详情页：复杂数据摊平后照样用自动滚动，只是多了个「再按一下收起」
function paintComplex(name) {
  const key = (name || "") + "|" + (complexRows ? complexRows.length : 0);
  const same = complexKey === key;         // tick 重画时别把滚动位置打回开头
  complexKey = key;
  viewMode = "complex";
  putText(retained.status, "详情 · 按「确认」收起");
  putText(retained.name, name || "");
  putText(retained.role, "");
  putText(retained.sim, "");
  cardRows = complexRows || [];
  if (!same) rowOffset = 0;
  paintRows();
  syncScroll();
  paintFootHint();
}

async function toggleDetail() {
  if (!lastScene || !lastScene.card) return;
  const card = lastScene.card;
  if (viewMode === "complex") {          // 再按一下 → 回到简单数据
    const rev = lastScene.rev;
    const ms = lastScene.ms;
    complexRows = null;
    lastScene = null;                    // 强制重算行，否则会沿用详情页的行
    paintCard(card, rev, ms);
    return;
  }
  if (!card.person_id) return;           // 陌生人没有详情
  putText(retained.status, "正在取详情…");
  try {
    const data = await getJson("/api/card/" + card.person_id + "/full");
    complexRows = (data && data.rows) || ["没有更详细的记录"];
  } catch (e) {
    complexRows = ["详情取不到：" + (e && e.message ? e.message : "网络错误")];
  }
  paintComplex(card.name);
}

// ---------- 自动滚动 ----------
// 镜片一屏只放得下 VISIBLE_ROWS 行；信息多于这个数就自己往下滚，一圈到底再从头开始。
const VISIBLE_ROWS = 6;
const SCROLL_MS = 1400;

let cardRows = [];        // 当前显示的所有行（简单数据或复杂数据）
let rowOffset = 0;        // 从第几行开始显示
let scrollTimer = null;
let viewMode = "idle";    // idle | simple | complex
let complexRows = null;   // 非空表示现在在「详情」页
let complexKey = "";
let autoDetailDone = false;      // 详情页内容指纹（tick 重画时判断要不要回到第一行）
let stopNote = 0;                // 停止持续听后，底栏先显示一阵"已停止"

function paintRows() {
  const total = cardRows.length;
  retained.info.forEach((t, i) => {
    if (!total) { putText(t, ""); return; }
    const row = cardRows[(rowOffset + i) % total];
    putText(t, i < Math.min(total, VISIBLE_ROWS) ? (row || "") : "");
  });
}

function syncScroll() {
  const need = cardRows.length > VISIBLE_ROWS;
  if (need && !scrollTimer) {
    scrollTimer = setInterval(() => {
      rowOffset = (rowOffset + 1) % cardRows.length;
      paintRows();
      paintFootHint();
    }, SCROLL_MS);
  } else if (!need && scrollTimer) {
    clearInterval(scrollTimer);
    scrollTimer = null;
  }
}

function paintFootHint() {
  if (stopNote && Date.now() < stopNote) { putText(retained.foot, "已停止 · 按一下再说"); return; }
  const pages = Math.max(1, Math.ceil(cardRows.length / VISIBLE_ROWS));
  const page = pages > 1 ? " · " + (Math.floor(rowOffset / VISIBLE_ROWS) + 1) + "/" + pages : "";
  if (viewMode === "complex") { putText(retained.foot, "确认收起 返回清屏" + page); return; }
  putText(retained.foot, (lastScene && lastScene.card && lastScene.card.status === "unknown"
    ? "按一下 · 去控制台新增" : "按一下再说") + page);
}

function stopScroll() {
  if (scrollTimer) { clearInterval(scrollTimer); scrollTimer = null; }
  cardRows = [];
  rowOffset = 0;
}
// ---------- 录音：持续听，按 3.5 秒切片送识别 ----------
//
// 平台的语音会话是"一次最多 60 秒、64 帧缓冲"，所以"一直听"不能靠一条长会话，
// 只能：① 每 40ms 把帧泵出来（不泵就丢音）② 每 3.5 秒切一片送 /api/voice/scan
// ③ 会话到 55 秒主动轮换一条新的（中间有毫秒级空档，对识别无影响）。

const SLICE_MS = 3500;          // 每片多长：太短认不准，太长反馈慢
const SLICE_MIN_MS = 1000;      // 停止时最后一片不足这个数就丢掉
const MAX_SESSION_MS = 55000;   // 平台上限 60 秒，提前换会话
const MAX_LISTEN_MS = 5 * 60 * 1000;   // 兜底：连续听 5 分钟自动停

let capture = null;   // {session,chunks,samples,startedAt,sessionStartedAt,slices,queue,busy,lastCard,stopped,dropped}

function clockOf(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}

// 只刷"状态行 + 底栏"；卡片内容由切片结果负责画，避免每 400ms 把卡片冲掉
function paintListening() {
  if (!capture) return;
  const secs = clockOf(Date.now() - capture.startedAt);
  const tail = capture.slices ? " · 已认 " + capture.slices + " 次" : "";
  retained.status.setText("在听 " + secs + tail);
  if (!capture.lastCard) {
    putText(retained.name, "在听…");
    putText(retained.role, "直接说话就行，不用按键");
    putText(retained.sim, "");
    const hint = [
      "说一句「我是 XXX」我就认人",
      "没人说话我会一直听着",
      "再按「确认」停止",
    ];
    retained.info.forEach((t, i) => putText(t, hint[i] || ""));
    putText(retained.foot, "确认 停止 · 返回 也停止");
  } else {
    putText(retained.foot, "确认 停止 · 返回 也停止");
  }
}

function openSession() {
  if (!capture) return;
  let session;
  try {
    session = input.requestVoiceInput({ mode: "audio" });
  } catch (e) {
    stopListening("开不了麦克风 · " + (e && e.message));
    return;
  }
  capture.session = session;
  capture.sessionStartedAt = Date.now();
  pump();
}

function pump() {
  if (!capture || capture.stopped) return;
  const c = capture;
  let ev;
  while ((ev = c.session.takeEvent())) {
    if (ev.type === "audio") {
      c.chunks.push(Buffer.from(ev.data));
      c.samples += ev.sampleCount;
    } else if (ev.type === "end") {
      // 平台把这条会话关了：换一条接着听（保留已累积的缓冲）
      if (!c.stopped) { openSession(); return; }
      return;
    }
  }
  if (c.samples >= 16000 * (SLICE_MS / 1000)) cutSlice(c);
  if (Date.now() - c.sessionStartedAt >= MAX_SESSION_MS) {
    try { c.session.finish(); } catch (e) { /* 已经结束 */ }
    if (!c.stopped) { openSession(); return; }
  }
  if (Date.now() - c.startedAt >= MAX_LISTEN_MS) {
    stopListening("听满 5 分钟 · 自动停了");
    return;
  }
  c.pumpTimer = setTimeout(pump, 40);
}

function cutSlice(c) {
  const bytes = c.samples * 2;
  const chunks = c.chunks;
  c.chunks = [];
  c.samples = 0;
  if (bytes < 16000 * 2 * (SLICE_MIN_MS / 1000)) return;
  c.queue.push(toWav(chunks, bytes));
  drain(c);
}

async function drain(c) {
  if (c.busy) return;
  c.busy = true;
  try {
    while (c.queue.length) {
      const wav = c.queue.shift();
      let res;
      try {
        res = await postJson("/api/voice/scan", { audio: wav });
      } catch (e) {
        // 连不上：这一段先落盘，联网后由 outbox 补传
        if (!outbox.push(wav)) c.dropped += 1;
        continue;
      }
      const f = res && res.faces && res.faces[0];
      if (!f) continue;
      c.slices += 1;
      c.lastCard = f;
      c.lastRev = res.rev;
      c.lastMs = res.ms;
      paintCard(f, res.rev, res.ms);   // 认出来就换卡片
      paintListening();                // 再把状态行/底栏盖回"在听"
    }
  } finally {
    c.busy = false;
  }
}

function startListening() {
  if (capture || background) return;
  let availability;
  try {
    availability = input.getVoiceInputAvailability("audio");
  } catch (e) {
    paintIdle("这台设备没有麦克风");
    return;
  }
  if (!availability || availability.canRequest === false) {
    paintIdle("麦克风不可用 · " + ((availability && availability.reason) || "unknown"));
    return;
  }
  capture = {
    session: null, chunks: [], samples: 0,
    startedAt: Date.now(), sessionStartedAt: 0,
    slices: 0, queue: [], busy: false, lastCard: null, stopped: false, dropped: 0,
    pumpTimer: null,
  };
  viewMode = "idle";
  complexRows = null;
  openSession();
  paintListening();
}

function stopListening(msg) {
  const c = capture;
  if (!c) return;
  c.stopped = true;
  if (c.pumpTimer) clearTimeout(c.pumpTimer);
  try { if (c.session) c.session.finish(); } catch (e) { /* 已经结束 */ }
  // 最后不足一片的也别浪费：够 1 秒就送出去
  const bytes = c.samples * 2;
  if (bytes >= 16000 * 2 * (SLICE_MIN_MS / 1000)) {
    c.queue.push(toWav(c.chunks, bytes));
  }
  c.samples = 0;
  c.chunks = [];
  capture = null;
  drain(c);                          // 用局部引用，队列不会因为 capture 置空而丢
  // 停下来的画面留在"最后认出的人"上，比退回空白页有用
  if (c.lastCard) {
    stopNote = Date.now() + 6000;         // 底栏先显示"已停止"，6 秒后回到常态提示
    paintCard(c.lastCard, c.lastRev, c.lastMs);
  } else {
    paintIdle(msg || "已停止 · 这次没认出来");
  }
}

// ---------- 主循环 ----------

function handleNavigation() {
  if (background) return;
  let nav = null;
  do {
    try { nav = input.takeNavigationAction(); } catch (e) { nav = null; }
    if (nav === "activate") {
      if (capture) stopListening();                                   // 在听 → 停
      else if (viewMode === "simple" || viewMode === "complex") toggleDetail();
      else startListening();                                          // 空白 → 开始持续听
    } else if (nav === "back") {
      if (capture) {
        stopListening("已取消");
      } else if (viewMode !== "idle") {
        clearView();        // 清屏：回到"按一下开始听"
      }
    }
  } while (nav);
}

function clearView() {
  complexRows = null;
  viewMode = "idle";
  shownRev = Math.max(shownRev, (lastScene && lastScene.rev) || 0);   // 别让它立刻又画回来
  paintIdle("按一下开始听…");
}

async function poll() {
  if (inFlight || background || capture) return;
  inFlight = true;
  try {
    const data = await getJson("/api/latest");
    if (!data || data.rev === shownRev) return;          // 没新结果就别动屏幕
    const faces = data.faces || [];
    if (!faces.length) {
      paintIdle("按一下开始听…");
      shownRev = data.rev;
      return;
    }
    paintCard(faces[0], data.rev, data.ms);
    // 演示/联调用：MEMORYLENS_AUTO_DETAIL=1 时，认出已知的人后自动翻到详情页
    if (process.env.MEMORYLENS_AUTO_DETAIL === "1" && faces[0].person_id && !autoDetailDone) {
      autoDetailDone = true;
      toggleDetail();
    }
  } catch (e) {
    paintOffline(e && e.message);
  } finally {
    inFlight = false;
  }
}

async function flushOutbox() {
  if (background) return;
  let sent = 0;
  try {
    sent = await outbox.flush();
  } catch (e) {
    return;
  }
  if (sent > 0) {
    console.log("[outbox] 补传了 " + sent + " 段录音");
    paintIdle("补传了 " + sent + " 段录音");
  }
}

function tick() {
  rafId = typeof os.requestAnimationFrame === "function" ? os.requestAnimationFrame(tick) : null;
  const now = Date.now();
  if (now - lastPaintAt < 400) return;
  lastPaintAt = now;
  if (capture) { paintListening(); return; }   // 在听的时候只刷计时，别把卡片冲掉
  if (!lastScene) return;
  if (complexRows) paintComplex(lastScene.card ? lastScene.card.name : "");
  else if (lastScene.card) paintCard(lastScene.card, lastScene.rev, lastScene.ms);
  else paintIdle(lastScene.idle);
}

function start() {
  if (retained.window) return;
  buildHud();
  poll();
  pollTimer = setInterval(poll, POLL_MS);
  lastPaintAt = 0;
  if (typeof os.requestAnimationFrame === "function") rafId = os.requestAnimationFrame(tick);
  navTimer = setInterval(handleNavigation, NAV_MS);
  outboxTimer = setInterval(flushOutbox, OUTBOX_RETRY_MS);
  setTimeout(flushOutbox, 1200);        // 起来先把上次没发出去的补上
  try { require("@irisview/osx/os").setDisplayKeepAwake(true); } catch (e) { /* 可选能力 */ }
  console.log("MemoryLens glasses app ready → " + BACKEND);
}

function stopTimers() {
  if (rafId !== null && typeof os.cancelAnimationFrame === "function") { os.cancelAnimationFrame(rafId); rafId = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (navTimer) { clearInterval(navTimer); navTimer = null; }
  if (outboxTimer) { clearInterval(outboxTimer); outboxTimer = null; }
  if (scrollTimer) { clearInterval(scrollTimer); scrollTimer = null; }   // 后台不滚
}

App.createApp((lifecycle) => {
  if (lifecycle === AppLifecycle.ON_CREATE) start();

  if (lifecycle === AppLifecycle.ON_FOREGROUND || lifecycle === AppLifecycle.ON_REOPEN) {
    background = false;
    if (retained.window) {
      start();
      pollTimer = pollTimer || setInterval(poll, POLL_MS);
      navTimer = navTimer || setInterval(handleNavigation, NAV_MS);
      if (lastScene && lastScene.card) syncScroll();     // 回前台继续滚
    }
  }

  if (lifecycle === AppLifecycle.ON_BACKGROUND) {
    background = true;
    stopListening("已切到后台");
    stopTimers();
  }

  if (lifecycle === AppLifecycle.ON_DESTROY) {
    background = true;
    stopListening("已退出");
    stopTimers();
    retained.board = undefined;
    retained.root = undefined;
    retained.window = undefined;
  }
});


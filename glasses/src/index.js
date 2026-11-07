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
let lastScene = null;
let lastCardKey = "";
let endedAt = 0;          // {card, rev, ms} 或 {idle:"..."}，用来周期性重绘
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
  if (!t) return;
  t.setText(s === undefined || s === null ? " " : String(s));
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
  putText(retained.name, " ");
  putText(retained.role, " ");
  putText(retained.sim, " ");
  retained.info.forEach((t) => putText(t, " "));
  putText(retained.foot, "按一下就说");
}

function paintOffline(err) {
  stopScroll();
  putText(retained.status, "后台没连上");
  putText(retained.name, "眼镜在线");
  putText(retained.role, " ");
  putText(retained.sim, " ");
  const rows = splitLines("电脑上跑 start.bat", 6);
  retained.info.forEach((t, i) => putText(t, rows[i] || ""));
  putText(retained.foot, "按一下就说");
}

function paintCard(card, rev, ms) {
  const known = card.status !== "unknown";
  const kind = card.kind === "voice" ? "听到" : "看到";
  // 智能体链路会直接给状态（待命/处理中/待确认/完成/异常），优先用它
  const agentState = card.kind === "agent" ? (card.state || "") : "";
  viewMode = "simple";
  complexRows = null;
  putText(retained.status,
    card.status === "short" ? "没听清 · 再说一次"
      : agentState ? agentState + (card.degraded ? " · 兜底" : "") + " · " + (ms || 0) + "ms"
        : known ? kind + " · " + (ms || 0) + "ms"
          : (card.kind === "voice" ? "陌生的声音" : "陌生人 · 不认识"));
  putText(retained.name, card.name || "陌生人");
  putText(retained.role, card.layout === "notice" ? "多人对话 · 没入库" : (card.role || ""));
  putText(retained.sim, known && typeof card.similarity === "number"
    ? "SIM " + card.similarity.toFixed(2) : "");

  // tick 每 400ms 会重画一次同一张卡，别把滚动位置重置了。
  // 指纹用内容而不是对象引用：换卡但 rev 没变时，行也必须跟着换。
  const cardKey = [card.name, card.state, card.role, (card.lines || []).join("|")].join("\u0001");
  const same = cardKey === lastCardKey;
  lastCardKey = cardKey;
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
  putText(retained.role, " ");
  putText(retained.sim, " ");
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
    if (!total) { putText(t, " "); return; }
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
// 平台的语音会话是"一次最多 60 秒、64 帧缓冲"，所以"一直听"不能靠一条长会话：
// ① 每 40ms 把帧泵出来（不泵就丢音）
// ② 每 15 秒切一片：第一片拿去认人，之后的每一片直接进这段对话
// ③ 会话到 55 秒主动轮换一条新的（中间有毫秒级空档，对识别无影响）
// ④ 边切边传，不在内存里堆整段（要支持 30 分钟）

const SLICE_MS = 15000;         // 一片 15 秒：够听清一句话，也让"认人"不用等太久
const FIRST_SLICE_MS = 15000;   // 第一片：15 秒就要出结果（认人 + 第一版要点）
const NEXT_SLICE_MS = 55000;    // 之后每 55 秒一片，正好在平台 60 秒会话上限之前
const SLICE_MIN_MS = 3000;      // 停止时最后一片不足这个数就丢掉
const MAX_SESSION_MS = 55000;   // 平台上限 60 秒，提前换会话
const MAX_LISTEN_MS = 30 * 60 * 1000;   // 兜底：连续听 30 分钟自动停
const AUTO_ARCHIVE = process.env.MEMORYLENS_ARCHIVE !== "0";   // 停止后把整段对话整理进档案

let capture = null;   // {session,chunks,samples,queue,busy,personId,personName,sessionId,slices,sent,lastCard,stopped,dropped}

function clockOf(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}

function listenLine() {
  const c = capture;
  const tail = c.sent ? " · " + c.sent + " 段" : "";
  const silent = c.samples === 0 && Date.now() - c.startedAt > 3000;
  return "在听 " + clockOf(Date.now() - c.startedAt) + (silent ? " · 没声音" : tail);
}

// 只刷"状态行 + 底栏"；卡片内容由切片结果负责画，避免每 400ms 把卡片冲掉
let listenSig = "";
function paintListening() {
  if (!capture) return;
  const c0 = capture.lastCard;
  // 整屏指纹：文字没变就整帧跳过（否则每 400ms 重设一遍，镜片上会闪）
  const sig = listenLine() + "|" + (c0
    ? [c0.name, c0.role, c0.status, c0.similarity, (c0.lines || []).join("~")].join("|")
    : "none");
  if (sig === listenSig) return;
  listenSig = sig;
  retained.status.setText(listenLine());
  if (capture.lastCard && !complexRows) {
    // 认出来之后：名字/身份/信息行保持卡片内容，别被"在听"的提示冲掉
    const card = capture.lastCard;
    putText(retained.name, card.name || "陌生人");
    putText(retained.role, card.layout === "notice" ? "多人对话 · 没入库" : (card.role || ""));
    putText(retained.sim, card.status !== "unknown" && typeof card.similarity === "number"
      ? "SIM " + card.similarity.toFixed(2) : "");
    const rows = [];
    for (const ln of (card.lines || [])) rows.push(...splitLines(ln, 6));
    if (rows.join("|") !== cardRows.join("|")) { cardRows = rows; rowOffset = 0; }
    paintRows();
    putText(retained.foot, "确认 详情 · 返回 结束");
    return;
  }
  if (!capture.lastCard) {
    const silent = capture.samples === 0 && Date.now() - capture.startedAt > 3000;
    putText(retained.name, "在听…");
    putText(retained.role, silent ? "点「确认采集」" : "直接说话就行，不用按键");
    putText(retained.sim, " ");
    const hint = [
      "前 15 秒用来认人",
      "认出来就一直记这段对话",
      "按「返回」结束并入库",
    ];
    retained.info.forEach((t, i) => putText(t, hint[i] || ""));
  }
  putText(retained.foot, "确认 详情 · 返回 结束");
}

function openSession() {
  if (!capture) return;
  let session;
  try {
    session = input.requestVoiceInput({ mode: "audio" });
  } catch (e) {
    stopAndArchive("开不了麦克风 · " + (e && e.message));
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
      const buf = Buffer.from(ev.data);
      c.chunks.push(buf);
      c.samples += ev.sampleCount;
    } else if (ev.type === "end") {
      // 平台把这条会话关了：换一条接着听（保留已累积的缓冲）
      if (!c.stopped) { openSession(); return; }
      return;
    }
  }
  const target = (c.sent || 0) === 0 ? FIRST_SLICE_MS : NEXT_SLICE_MS;
  if (c.samples >= 16000 * (target / 1000)) cutSlice(c);
  if (Date.now() - c.sessionStartedAt >= MAX_SESSION_MS) {
    try { c.session.finish(); } catch (e) { /* 已经结束 */ }
    if (!c.stopped) { openSession(); return; }
  }
  if (Date.now() - c.startedAt >= MAX_LISTEN_MS) {
    stopAndArchive("听满 30 分钟 · 自动停了");
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

// 队列里只有一种东西：一段 WAV。
// 还没认人时，这段先拿去认人；认出来之后，所有段都直接进这段对话。
async function drain(c) {
  if (c.busy) return;
  c.busy = true;
  try {
    while (c.queue.length) {
      const wav = c.queue.shift();
      if (!c.personId) {
        await identify(c, wav);
        continue;
      }
      await pushChunk(c, wav);
    }
  } finally {
    c.busy = false;
  }
}







function startListening(mode) {
  if (capture || background) return;
  listenSig = "";
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
    lastCard: null,
    session: null, chunks: [], samples: 0,    startedAt: Date.now(), sessionStartedAt: 0,
    slices: 0, sent: 0, queue: [], busy: false,
    mode: mode === "meeting" ? "meeting" : "single",
    agentSession: "g-" + Date.now().toString(36),
    personId: "", personName: "", sessionId: null,
    lastCard: null, stopped: false, dropped: 0, pumpTimer: null,
  };
  viewMode = "idle";
  complexRows = null;
  openSession();
  paintListening();
}

// 按「返回」：收尾最后一片 → 等队列传完 → 让模型析出要点 → 写进那个人的档案
async function identify(c, wav) {
  // 第一片：声纹认人（陌生人由后端自动建号）+ 让智能体给出第一版要点
  let res;
  try {
    res = await postJson("/api/agent/feed", {
      session: c.agentSession, audio: wav, auto_add: true, mode: c.mode,
    });
  } catch (e) {
    if (!outbox.push(wav)) c.dropped += 1;
    return;
  }
  c.sent += 1;
  const v = (res && res.view) || null;
  if (!v) return;
  c.personId = v.person_id || "";
  c.personName = v.person || "";
  if (!c.personId) return;
  c.slices += 1;
  c.lastCard = agentCard(v);
  c.lastRev = res.rev;
  c.lastMs = v.ms || 0;
  shownRev = res.rev;
  lastScene = { card: c.lastCard, rev: res.rev, ms: v.ms || 0 };
  paintCard(c.lastCard, res.rev, v.ms || 0);
  paintListening();                // 再把状态行/底栏盖回"在听"
}

function agentCard(v) {
  return {
    kind: "agent",
    status: v.person ? "known" : "unknown",
    name: v.person || "这段对话",
    role: v.role || "",
    similarity: v.similarity || 0,
    lines: v.rows || [],
    state: v.state,
    layout: v.layout,
    options: v.options || [],
    degraded: !!v.degraded,
  };
}

async function ensureSession(c) {
  return !!c.agentSession;
}

async function pushChunk(c, wav) {
  // 之后每 55 秒一片：交给同一个智能体会话，它会拼档案 + 给下一步建议
  try {
    const res = await postJson("/api/agent/feed", {
      session: c.agentSession, audio: wav, auto_add: true, mode: c.mode,
    });
    c.sent += 1;
    const v = (res && res.view) || null;
    if (!v) return;
    if (v.person_id) { c.personId = v.person_id; c.personName = v.person; }
    if (v.rows && v.rows.length) {
      c.lastCard = agentCard(v);
      c.lastRev = res.rev;
      c.lastMs = v.ms || 0;
        lastScene = { card: c.lastCard, rev: res.rev, ms: v.ms || 0 };
      paintCard(c.lastCard, res.rev, v.ms || 0);
      paintListening();
    }
  } catch (e) {
    if (!outbox.push(wav)) c.dropped += 1;
  }
}

async function stopAndArchive(msg) {
  if (capture) {
    capture.stopped = true;
    retained.status.setText("收尾 · 传剩余录音…");
  }
  const c = capture;
  if (!c) return;
  listenSig = "";
  c.stopped = true;
  if (c.pumpTimer) clearTimeout(c.pumpTimer);
  try { if (c.session) c.session.finish(); } catch (e) { /* 已经结束 */ }
  const bytes = c.samples * 2;
  if (bytes >= 16000 * 2 * (SLICE_MIN_MS / 1000)) {
    c.queue.push(toWav(c.chunks, bytes));
  }
  c.samples = 0;
  c.chunks = [];
  capture = null;
  const revAtStop = shownRev;
  endedAt = Date.now();
  putText(retained.foot, "收尾 · 传剩余录音…");
  await drain(c);
  if (!c.personId) {
    if (shownRev === revAtStop) paintIdle(msg || "没认出人 · 这段没入库");
    return;
  }
  if (!AUTO_ARCHIVE || !c.sessionId) {
    if (shownRev === revAtStop) clearView();
    return;
  }
  paintIdle("整理 " + c.personName + " 这段…");
  try {
    const analyzed = await postJson("/api/converse/analyze", { session: c.sessionId });
    const items = (analyzed && analyzed.items) || [];
    if (!items.length) {
      paintIdle("没析出要点 · 转写已存");
      return;
    }
    const saved = await postJson("/api/converse/save", { session: c.sessionId, items });
    paintIdle("已记入 " + c.personName + " · " + ((saved && saved.saved) || items.length) + " 条");
  } catch (e) {
    paintIdle("整理失败 · " + (e && e.message));
  }
}

// ---------- 主循环 ----------


// ---------------- 文本通道：虚拟键盘 + AI 建议 ----------------
// 眼镜上只有六个离散动作（上下左右/确认/返回），所以键盘做成"短语键"：
// 每格是一个常用词，方向键选，确认键输入。比 26 个字母更适合中文。
const KEY_ROWS = [
  ["我", "你", "他", "好", "不"],
  ["是", "的", "了", "再", "说"],
  ["一", "次", "下", "周", "报"],
  ["删除", "发送", "关闭"],
];
const DEFAULT_OPTS = ["只留待办", "再说细一点", "不是他"];
let textMode = null;   // {kind:"keys"|"opts", r, c, draft, options, index}

function openText(kind) {
  if (capture) {
    // 录音时也可以开：文本只会另起一段，不会打断正在录的对话
  }
  textMode = { kind: kind, r: 0, c: 0, draft: "", index: 0, options: (lastScene && lastScene.card && lastScene.card.options) || DEFAULT_OPTS };
  paintText();
}

function closeText() {
  textMode = null;
  if (capture) { paintListening(); return; }
  if (lastScene && lastScene.card) { paintCard(lastScene.card, lastScene.rev, lastScene.ms); return; }
  paintIdle("按一下开始听 · 上=打字 下=看建议");
}

function keyRowsView() {
  return KEY_ROWS.map((row, r) =>
    row.map((k, c) => (r === textMode.r && c === textMode.c ? "【" + k + "】" : k)).join(" "));
}

function paintText() {
  if (!textMode) return;
  if (textMode.kind === "opts") {
    putText(retained.status, "建议 · 上下选");
    putText(retained.name, (lastScene && lastScene.card && lastScene.card.name) || "一句话指令");
    putText(retained.role, "确认=发送");
    putText(retained.sim, " ");
    const rows = textMode.options.map((o, i) => (i === textMode.index ? "▶ " + o : "  " + o));
    retained.info.forEach((t, i) => putText(t, rows[i] || ""));
    putText(retained.foot, "返回 关掉");
    return;
  }
  putText(retained.status, "打字 · 方向键选");
  putText(retained.name, textMode.draft ? textMode.draft.slice(-5) : "?");
  putText(retained.role, "短语键");
  putText(retained.sim, " ");
  const rows = keyRowsView();
  retained.info.forEach((t, i) => putText(t, rows[i] || ""));
  putText(retained.foot, "删除 发送 关闭");
}

async function sendText(text) {
  const t = (text || "").trim();
  if (!t) return;
  textMode = null;
  try {
    const resp = await postJson("/api/agent/feed", {
      session: "text-" + Date.now().toString(36),
      text: t,
      auto_add: false,
    });
    const v = resp && resp.view;
    if (!v) { paintIdle("没拿到结果"); return; }
    const card = {
      kind: "agent",
      status: v.person ? "known" : "unknown",
      name: v.person || "文字",
      role: "",
      similarity: v.similarity || 0,
      lines: v.rows || [],
      state: v.state,
      layout: v.layout,
      options: v.options || [],
      degraded: !!v.degraded,
    };
    lastScene = { card: card, rev: resp.rev, ms: 0 };
    shownRev = resp.rev;
    paintCard(card, resp.rev, v.ms || 0);
  } catch (e) {
    paintOffline(e && e.message);
  }
}

function navText(act) {
  if (!textMode) return;
  if (textMode.kind === "opts") {
    const n = textMode.options.length;
    if (act === "up") textMode.index = (textMode.index + n - 1) % n;
    else if (act === "down") textMode.index = (textMode.index + 1) % n;
    else if (act === "activate") { sendText(textMode.options[textMode.index]); return; }
    else if (act === "back") { closeText(); return; }
    paintText();
    return;
  }
  let r = textMode.r;
  let c = textMode.c;
  if (act === "left") c = (c + KEY_ROWS[r].length - 1) % KEY_ROWS[r].length;
  else if (act === "right") c = (c + 1) % KEY_ROWS[r].length;
  else if (act === "up") { r = (r + KEY_ROWS.length - 1) % KEY_ROWS.length; c = Math.min(c, KEY_ROWS[r].length - 1); }
  else if (act === "down") { r = (r + 1) % KEY_ROWS.length; c = Math.min(c, KEY_ROWS[r].length - 1); }
  else if (act === "back") { closeText(); return; }
  else if (act === "activate") {
    const k = KEY_ROWS[r][c];
    if (k === "删除") textMode.draft = textMode.draft.slice(0, -1);
    else if (k === "发送") { sendText(textMode.draft); return; }
    else if (k === "关闭") { closeText(); return; }
    else textMode.draft = (textMode.draft + k).slice(0, 24);
  }
  textMode.r = r;
  textMode.c = c;
  paintText();
}

// ---------------- 开场：先选单人还是多人（纯代码，不问模型） ----------------
let modeSelect = null;   // {index:0|1}

const MODE_ITEMS = [
  { key: "single", label: "单人", hint: "一个人聊 · 15 秒认人" },
  { key: "meeting", label: "多人", hint: "三个人以上 · 只转写不入库" },
];

function openMode() {
  modeSelect = { index: 0 };
  paintMode();
}

function closeMode() {
  modeSelect = null;
  if (lastScene && lastScene.card) { paintCard(lastScene.card, lastScene.rev, lastScene.ms); return; }
  paintIdle("按一下开始 · 先选单人还是多人");
}

function paintMode() {
  if (!modeSelect) return;
  putText(retained.status, "选模式 · 左右选 · 确认进入");
  putText(retained.name, "开始记录");
  putText(retained.role, "先选这一段是什么");
  putText(retained.sim, " ");
  const rows = MODE_ITEMS.map((m, i) => (i === modeSelect.index ? "【" + m.label + "】" : "  " + m.label));
  retained.info.forEach((t, i) => putText(t, rows[i] || ""));
  retained.info[2] && putText(retained.info[2], MODE_ITEMS[modeSelect.index].hint);
  putText(retained.foot, "确认进入 · 返回关掉");
}

function navMode(act) {
  if (!modeSelect) return;
  if (act === "left" || act === "up") { modeSelect.index = (modeSelect.index + 1) % MODE_ITEMS.length; paintMode(); return; }
  if (act === "right" || act === "down") { modeSelect.index = (modeSelect.index + 1) % MODE_ITEMS.length; paintMode(); return; }
  if (act === "back") { closeMode(); return; }
  if (act === "activate") {
    const mode = MODE_ITEMS[modeSelect.index].key;
    modeSelect = null;
    startListening(mode);
    return;
  }
}

function handleNavigation() {
  const act = input.takeNavigationAction();
  if (!act) return;
  if (modeSelect) { navMode(act); return; }
  if (textMode) { navText(act); return; }
  if (capture) {
    if (act === "activate") { toggleDetail(); return; }   // 录音中看详情，不打断
    if (act === "back") { stopAndArchive("结束了"); return; }
    if (act === "up") { openText("keys"); return; }
    if (act === "down") { openText("opts"); return; }
    return;
  }
  if (act === "up") { openText("keys"); return; }
  if (act === "down") { openText("opts"); return; }
  if (act === "activate") {
    // 不在录音时，确认键只有一个含义：开一段新的（先问单人还是多人）
    openMode();
    return;
  }
  if (act === "back") { clearView(); return; }
}

function clearView() {
  complexRows = null;
  viewMode = "idle";
  shownRev = Math.max(shownRev, (lastScene && lastScene.rev) || 0);   // 别让它立刻又画回来
  paintIdle("按一下开始听…");
}

async function poll() {

  inFlight = true;
  try {
    const data = await getJson("/api/latest");

    const faces = data.faces || [];
    if (!faces.length) {
      paintIdle("按一下开始听…");
      shownRev = data.rev;
      return;
    }
    const c0 = faces[0];
    if (c0.kind === "agent" && !capture && Date.now() - endedAt < 15000) {
      // 上一段晚到的对话结果：不在待机时画，免得盖住当前画面
      return;
    }
    if (capture) {
      // 正在听：只更新数据，渲染交给 paintListening()（否则会在两屏之间来回跳）
      capture.lastCard = c0;
      capture.lastRev = data.rev;
      capture.lastMs = data.ms;
      return;
    }
    paintCard(c0, data.rev, data.ms);
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
  // 先续上下一帧，再按状态分派 —— 否则一旦进键盘/选模式就永远回不来了
  rafId = typeof os.requestAnimationFrame === "function" ? os.requestAnimationFrame(tick) : null;
  if (modeSelect) { paintMode(); return; }
  if (textMode) { paintText(); return; }
  const now = Date.now();
  if (now - lastPaintAt < 400) return;
  lastPaintAt = now;
  if (capture) {
    // 在听的时候：详情页照常显示，但状态行继续走计时
    if (complexRows) {
      paintComplex(lastScene && lastScene.name ? lastScene.name : capture.personName);
      retained.status.setText(listenLine());
      putText(retained.foot, "确认 收起 · 返回 结束");
    } else {
      paintListening();
    }
    return;
  }
  // 注意：这里不要无条件重画 lastScene.card —— 新结果（比如人脸）刚上屏，
  // 下一帧就被旧卡片盖回去。卡片只在 poll() 拿到新 rev 时画。
  if (!lastScene) return;
  if (complexRows) paintComplex(lastScene.card ? lastScene.card.name : "");
}

function start() {
  if (retained.window) return;
  buildHud();
  paintIdle("按一下开始听…");   // 先把空闲态画出来，别留控件的默认 "Text"
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
    stopAndArchive("已切到后台");
    stopTimers();
  }

  if (lifecycle === AppLifecycle.ON_DESTROY) {
    background = true;
    stopAndArchive("已退出");
    stopTimers();
    retained.board = undefined;
    retained.root = undefined;
    retained.window = undefined;
  }
});

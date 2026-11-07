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

// 眼镜连哪台电脑。模拟器上就是本机；真机换成同一局域网里跑 server.py 的那台。
const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
// 服务端开了 AR_API_TOKEN 时，眼镜端也要带上（MEMORYLENS_TOKEN）
const API_TOKEN = (process.env.MEMORYLENS_TOKEN || "").trim();
const POLL_MS = 700;        // 拉结果
const NAV_MS = 120;         // 读按键/手势
const HTTP_TIMEOUT_MS = 1500;
const RECORD_MS = 4000;     // 一次最多听 4 秒，够说一句"我是王姐"
const MEETING_RECORD_MS = 20000;  // 会议模式：一段 20 秒，避免触到 input.audio 60 秒会话上限
const MAX_SAMPLES = 16000 * 30;

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
let inFlight = false;
let shownRev = -1;
let recording = null;   // { session, chunks: Uint8Array[], samples, startedAt, timer, limitMs }
let background = false;
// 会议模式：null = 没开；{ id, chunks } = 正在录一场多人会议
let meeting = null;
let meetingSeq = 0;

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

function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  if (API_TOKEN) h["X-API-Token"] = API_TOKEN;
  return h;
}

function getJson(path) {
  if (typeof fetch === "function") {
    const signal = typeof AbortSignal !== "undefined" && AbortSignal.timeout
      ? AbortSignal.timeout(HTTP_TIMEOUT_MS) : undefined;
    return fetch(BACKEND + path, { headers: authHeaders(), signal, cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  const http = require("node:http");
  return new Promise((resolve, reject) => {
    const req = http.get(BACKEND + path, { headers: authHeaders(), timeout: HTTP_TIMEOUT_MS }, (res) => {
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
      headers: authHeaders({ "Content-Type": "application/json" }),
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
      headers: authHeaders({ "Content-Type": "application/json", "Content-Length": payload.length }),
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
  const kind = card.kind === "voice" ? "听到" : card.kind === "meeting" ? "会议" : "看到";
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
// ---------- 录音 ----------

function stopRecording(cancelled) {
  if (!recording) return;
  const r = recording;
  recording = null;
  if (r.timer) clearTimeout(r.timer);
  try { cancelled ? r.session.cancel() : r.session.finish(); } catch (e) { /* 会话可能已结束 */ }
  if (!cancelled) paintIdle(meeting ? "上传中…" : "听完了 · 正在认人…");
}

async function finishRecording() {
  const r = recording;
  if (!r) return;
  const chunks = r.chunks;
  const bytes = r.samples * 2;
  stopRecording(false);
  if (bytes < 16000 * 2 * 0.6) {      // 不到 0.6 秒，后台也认不了
    meeting ? paintMeeting("太短 · 再说长一点") : paintIdle("太短了 · 按住多说两句");
    return;
  }
  if (meeting) {
    const dataUrl = toWav(chunks, bytes);   // toWav 已经返回 data:audio/wav;base64,...
    await uploadMeetingChunk(dataUrl);
    return;
  }
  retained.status.setText("正在认人…");
  try {
    const res = await postJson("/api/voice/scan", { audio: toWav(chunks, bytes) });
    if (res && res.faces && res.faces.length) paintCard(res.faces[0], res.rev, res.ms);
    else paintIdle("后台没给结果，再试一次");
  } catch (e) {
    paintOffline(e && e.message);
  }
}

function pumpRecording() {
  const r = recording;
  if (!r) return;
  let ev;
  while ((ev = r.session.takeEvent())) {
    if (ev.type === "audio") {
      r.chunks.push(Buffer.from(ev.data));
      r.samples += ev.sampleCount;
    } else if (ev.type === "end") {
      finishRecording();
      return;
    }
  }
  if (r.samples >= 16000 * ((r.limitMs || RECORD_MS) / 1000)) finishRecording();
  else setTimeout(pumpRecording, 40);
}

function startRecording(recordMs) {
  if (recording || background) return;
  const limit = recordMs || RECORD_MS;
  let availability;
  try {
    availability = input.getVoiceInputAvailability("audio");
  } catch (e) {
    paintIdle("这台设备没有麦克风");
    return;
  }
  if (!availability || availability.canRequest === false) {
    paintIdle("麦克风不可用 · " + (availability && availability.reason || "unknown"));
    return;
  }
  let session;
  try {
    session = input.requestVoiceInput({ mode: "audio" });
  } catch (e) {
    paintIdle("开不了麦克风 · " + (e && e.message));
    return;
  }
  recording = { session, chunks: [], samples: 0, startedAt: Date.now(), timer: null, limitMs: limit };
  retained.status.setText("在听… 说话");
  putText(retained.name, "");
  putText(retained.role, "");
  putText(retained.sim, "");
  retained.info.forEach((t) => putText(t, ""));
  retained.info[0].setText(meeting ? "会议录音中" : "说一句话（比如：我是王姐）");
  retained.info[1].setText("再按一下结束，或 " + Math.round(limit / 1000) + " 秒后自动结束");

  // 兜底：到点还没结束就主动 finish()
  recording.timer = setTimeout(() => finishRecording(), limit + 1200);
  setTimeout(pumpRecording, 40);
}

// ---------- 会议模式（多人轮流说话） ----------

function paintMeeting(msg) {
  stopScroll();
  viewMode = "idle";
  complexRows = null;
  lastScene = { idle: msg };
  putText(retained.status, msg);
  putText(retained.name, "会议录制");
  putText(retained.role, meeting ? "已传 " + meeting.chunks + " 段" : "");
  putText(retained.sim, "");
  retained.info.forEach((t) => putText(t, ""));
  const rows = splitLines(meeting ? "按确认录 20 秒 · 按返回结束" : "按上键开始会议", 6);
  retained.info.forEach((t, i) => putText(t, rows[i] || ""));
  putText(retained.foot, meeting ? "确认录音 · 返回结束" : "按一下就说");
}

async function startMeeting() {
  if (meeting || recording) return;
  putText(retained.status, "正在建会…");
  try {
    const r = await postJson("/api/v1/session/create", { title: "眼镜会议" });
    if (!r || !r.ok) throw new Error((r && r.error) || "建会失败");
    meeting = { id: r.sessionId, chunks: 0 };
    meetingSeq = 0;
    paintMeeting("会议已开始");
  } catch (e) {
    paintOffline(e && e.message);
  }
}

async function uploadMeetingChunk(dataUrl) {
  if (!meeting) return;
  const m = meeting;
  meetingSeq += 1;
  retained.status.setText("上传第 " + meetingSeq + " 段…");
  try {
    const r = await postJson("/api/v1/audio/upload", {
      sessionId: m.id, seq: meetingSeq, audio: dataUrl,
    });
    if (!r || !r.ok) throw new Error((r && r.error) || "上传失败");
    m.chunks += 1;
    paintMeeting("已上传 " + m.chunks + " 段");
  } catch (e) {
    console.error("[meeting] upload failed:", e && (e.stack || e.message) || e);
    paintOffline("上传失败：" + (e && e.message ? e.message : "网络错误"));
  }
}

async function finishMeeting() {
  const m = meeting;
  if (!m) return;
  if (recording) stopRecording(true);
  meeting = null;
  putText(retained.status, "正在分离…");
  try {
    const r = await postJson("/api/v1/session/" + encodeURIComponent(m.id) + "/finalize", {});
    if (!r || !r.ok) throw new Error((r && r.error) || "分离失败");
    const lines = (r.speakers || []).map(
      (s) => (s.name || s.spk_id) + " · " + Math.round(s.seconds || 0) + "s");
    if (!lines.length) lines.push("没分出人声，靠近点再说");
    const card = {
      status: "known", name: "会议 · " + (r.speaker_count || 0) + " 人", role: "多人分离",
      similarity: null, kind: "meeting", lines,
    };
    paintCard(card, Date.now(), r.ms || 0);
  } catch (e) {
    paintOffline("分离失败：" + (e && e.message ? e.message : "网络错误"));
  }
}

// ---------- 主循环 ----------

function handleNavigation() {
  if (background) return;
  let nav = null;
  do {
    try { nav = input.takeNavigationAction(); } catch (e) { nav = null; }
    if (meeting) {
      if (nav === "activate") {
        if (recording) finishRecording();
        else startRecording(MEETING_RECORD_MS);
      } else if (nav === "back") {
        if (recording) stopRecording(true);
        finishMeeting();
      }
      continue;
    }
    if (nav === "activate") {
      if (recording) finishRecording();
      else if (viewMode === "simple" || viewMode === "complex") toggleDetail();
      else startRecording();
    } else if (nav === "back") {
      if (recording) {
        stopRecording(true);
        paintIdle("已取消");
      } else if (viewMode !== "idle") {
        clearView();        // 清屏：回到"按一下开始听"
      }
    } else if (nav === "up" && !recording && viewMode === "idle") {
      startMeeting();       // 空屏按「上」进入会议模式（下保留给翻页）
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
  if (inFlight || background || recording || meeting) return;
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

function tick() {
  rafId = typeof os.requestAnimationFrame === "function" ? os.requestAnimationFrame(tick) : null;
  const now = Date.now();
  if (now - lastPaintAt < 400) return;
  lastPaintAt = now;
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
  try { require("@irisview/osx/os").setDisplayKeepAwake(true); } catch (e) { /* 可选能力 */ }
  console.log("MemoryLens glasses app ready → " + BACKEND);
}

function stopTimers() {
  if (rafId !== null && typeof os.cancelAnimationFrame === "function") { os.cancelAnimationFrame(rafId); rafId = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (navTimer) { clearInterval(navTimer); navTimer = null; }
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
    stopRecording(true);
    stopTimers();
  }

  if (lifecycle === AppLifecycle.ON_DESTROY) {
    background = true;
    stopRecording(true);
    stopTimers();
    retained.board = undefined;
    retained.root = undefined;
    retained.window = undefined;
  }
});

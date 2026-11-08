"use strict";

// 模拟器里 JS 异常只会让客户端显示"应用需要处理"；把堆栈打出来便于定位。
try {
  if (typeof process !== "undefined" && process.on) {
    process.on("uncaughtException", (e) => {
      console.error("[uncaught] " + ((e && e.stack) || e));
    });
    process.on("unhandledRejection", (e) => {
      console.error("[unhandled] " + ((e && e.stack) || e));
    });
  }
} catch (e) { /* 非 Node 环境忽略 */ }

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
const MAX_SAMPLES = 16000 * 60;   // 平台单会话上限 60 秒；55 秒片不能被 30 秒常量截断
const OUTBOX_RETRY_MS = 8000;  // 离线录音的重发间隔
// 卡片（认人结果/55 秒建议）露一会儿就收起来，录音不停
let HOLD_MS = 9000;                       // 第一片自动认人后：9 秒
let IDENTIFY_MS = Number(process.env.MEMORYLENS_IDENTIFY_MS || 20000);   // 识别人：录多久
let PRESET_HOLD_MS = Number(process.env.MEMORYLENS_PRESET_HOLD_MS || 30000);  // 手动提交：30 秒      // 认人卡片在镜片上停留多久
// 认出人之后先把档案停 5 秒，再出三个按钮（这段时间按键仍然有效）
const BUTTON_DELAY_MS = Number(process.env.MEMORYLENS_BUTTON_DELAY_MS || 5000);

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
let endedAt = 0;
let bootAt = Date.now();   // 启动前的旧结果不显示（首页要干净）          // {card, rev, ms} 或 {idle:"..."}，用来周期性重绘
let pollTimer = null;
let navTimer = null;
let outboxTimer = null;
let inFlight = false;
let shownRev = -1;
let background = false;
let lastMeeting = null;      // 最近一场会议的 session；会议结束后仍可用文本指令回看

// ---------- PRD 多人会议模块 ----------
// 会议模块自己管一套 UI 状态：未录制/录制中、底部实时字幕、AI 回答浮窗。
// 单人模块完全不受影响：只有 mode=meeting 的 capture 才会走这里。
const MEETING_SLICE_MS = Number(process.env.MEMORYLENS_MEETING_SLICE_MS || 8000);
const MEETING_FIRST_SLICE_MS = Number(process.env.MEMORYLENS_MEETING_FIRST_SLICE_MS || 6000);
const MEETING_POPUP_ROWS = 5;      // 浮窗一次显示几行（超出用上下键滚）
const MEETING_SUB_ROWS = 3;        // 底部常驻字幕行数
let meeting = null;   // {session, meetingId, recording, stopping, startedAt, segments, speakers, popup, ...}

// 实测结论（simulator）：单个 Text 只画得出一百多像素宽，再长就被硬切。
// 所以一行文字拆成若干个 Text，横向拼起来（每块 x 依次 +124）。
const CHUNK_PX = 124;             // 每块占的横向宽度
const CHUNK_TEXT_PX = 112;        // 每块能画出的文字宽度

const putText = (t, s) => {
  if (!t) return;
  const v = s === undefined || s === null || s === "" ? " " : String(s);
  if (t.__text === v) return;       // 值没变就别碰控件：400ms 全量 setText 会闪
  t.__text = v;
  t.setText(v);                     // 不再每次 setSize：Text 创建时已经设过尺寸
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

  // ---------- 多人会议模块专用控件 ----------
  // 右上角常驻「录制中 / 未录制」；底部 3 行实时字幕；中间可滚动 AI 回答浮窗。
  retained.recBadge = makeText(retained.board, 400, 20, 220, 26, 14, COL.amber, TextAlign.RIGHT_MID);
  retained.subTitle = makeText(retained.board, 8, 330, 220, 20, 12, COL.dim);
  putText(retained.subTitle, "实时转写 · 按确认开始");
  retained.subRows = [];
  for (let i = 0; i < MEETING_SUB_ROWS; i++) {
    retained.subRows.push(makeSlot(retained.board, 8, 352 + i * 26, 14, COL.white, 3));
  }
  // 浮窗：半透明 Board + 4 列 Text 拼一行（单个 Text 画不宽，见文件头注释）
  // MEMORYLENS_UI_POPUP=0 是排障开关：真机/模拟器渲染异常时可以关掉浮窗定位。
  // 注意：这个模拟器运行时不允许在根 Board 下再挂第二个 Board（会报
  // IV-RT:1116 / display could not be updated），所以浮窗不用嵌套 Board：
  // 用一个带背景色的空 Text 当"半透明底板"，文字直接挂在根 Board 上，
  // 靠创建顺序保证叠在底板之上。MEMORYLENS_UI_POPUP=0 可整体关掉浮窗排障。
  if (process.env.MEMORYLENS_UI_POPUP !== "0") {
    retained.popupBg = makeText(retained.board, 8, 116, 500, 226, 12, COL.white);
    try { retained.popupBg.getLocalStyle().setBackgroundColor(Color.fromRGB8(6, 10, 14)); } catch (e) { /* 忽略 */ }
    putText(retained.popupBg, " ");
    retained.popupTitle = makeText(retained.board, 18, 120, 466, 24, 14, COL.cyan);
    retained.popupLines = [];
    for (let i = 0; i < MEETING_POPUP_ROWS; i++) {
      retained.popupLines.push(makeSlot(retained.board, 18, 152 + i * 28, 12, COL.white, 4));
    }
    retained.popupFoot = makeText(retained.board, 18, 318, 466, 18, 11, COL.dim);
    retained.popupWidgets = [retained.popupBg, retained.popupTitle,
      ...retained.popupLines.reduce((a, s) => a.concat(s.tiles), []), retained.popupFoot];
  }
  hideMeetingUi();
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

function postJson(path, body, timeoutMs) {
  const ms = timeoutMs || 8000;
  if (typeof fetch === "function") {
    const signal = typeof AbortSignal !== "undefined" && AbortSignal.timeout
      ? AbortSignal.timeout(ms) : undefined;
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
      timeout: ms,
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
  const actual = chunks.reduce((n, c) => n + c.length, 0);
  let n = Math.min(totalBytes || actual, actual, MAX_SAMPLES * 2);
  n -= n % 2;                       // 16-bit PCM：data 长度必须是偶数
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
  hideModeSel();
  // 录音期间屏幕必须一直是「在听」：任何路径想画待机都直接忽略
  if (capture) return;
  // 会议模块优先：任何"待机"路径都不要把会议 UI 冲掉
  if (meeting) {
    if (msg) meeting.note = msg;
    paintMeeting();
    return;
  }
  hideMeetingUi();
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
  const msg = String(err || "");
  const slow = /timeout|timed out|aborted|ETIMEDOUT/i.test(msg);
  if (meeting) {
    meeting.note = slow ? "后台有点慢 · 稍等" : "后台没连上 · 重试中";
    paintMeeting();
    return;
  }
  if (capture) {
    // 正在听/正在想的时候后端偶尔慢：绝不能把整页换成"后台没连上"，
    // 只在状态行闪一句，按钮/卡片都留在原地。
    capture.msg = slow ? "后台有点慢 · 稍等" : "后台没连上 · 重试中";
    capture.msgUntil = Date.now() + 2500;
    paintListening();
    return;
  }
  stopScroll();
  putText(retained.status, slow ? "后台在跑 · 这次慢" : "后台没连上");
  putText(retained.name, slow ? "再按一次" : "眼镜在线");
  putText(retained.role, " ");
  putText(retained.sim, " ");
  const rows = splitLines(slow ? "这次有点慢，再按一次确认" : "电脑上跑 start.bat", 6);
  retained.info.forEach((t, i) => putText(t, rows[i] || ""));
  putText(retained.foot, "按一下就说");
}

function paintCard(card, rev, ms) {
  hideModeSel();
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
    && card.matched_by !== "pinned" && !card.created
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

let paintedRows = "";
function paintRows() {
  const want = cardRows.join("|");
  if (want === paintedRows) return;
  paintedRows = want;

  const total = cardRows.length;
  retained.info.forEach((t, i) => {
    if (!total) { putText(t, " "); return; }
    const row = cardRows[i];
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
  if (capture && capture.mode === "meeting") {
    putText(retained.foot, "确认追问 返回归档" + page);
    return;
  }
  putText(retained.foot, (lastScene && lastScene.card && lastScene.card.status === "unknown"
    ? "按一下 · 去控制台新增" : "按一下再说") + page);
}

function stopScroll() {
  if (scrollTimer) { clearInterval(scrollTimer); scrollTimer = null; }
  cardRows = [];
  rowOffset = 0;
}

// ---------------- 多人会议（PRD）UI ----------------
// 与单人共用一套 Text 控件，但会议只用底部字幕 + 浮窗，不改单人卡片。

function setHiddenSafe(widget, hidden) {
  if (!widget) return;
  try {
    if (typeof widget.setHidden === "function") widget.setHidden(!!hidden);
  } catch (e) { /* 旧版 SDK 没有 hidden */ }
}

function hideMeetingPopup() {
  (retained.popupWidgets || []).forEach((w) => setHiddenSafe(w, true));
  // 清空内容：真机上隐藏控件就看不到了，单测替身则靠"文字为空"判断已关闭
  putText(retained.popupTitle, "");
  (retained.popupLines || []).forEach((slot) => putSlot(slot, ""));
  putText(retained.popupFoot, "");
}

function hideMeetingUi() {
  setHiddenSafe(retained.recBadge, true);
  setHiddenSafe(retained.subTitle, true);
  (retained.subRows || []).forEach((slot) => slot.tiles.forEach((t) => setHiddenSafe(t, true)));
  hideMeetingPopup();
}

function showMeetingUi() {
  setHiddenSafe(retained.recBadge, false);
  // 用户已取消底部字幕：会议不再渲染 subTitle / subRows，只留录制状态 + AI 回答
  setHiddenSafe(retained.subTitle, true);
  (retained.subRows || []).forEach((slot) => slot.tiles.forEach((t) => setHiddenSafe(t, true)));
}

// 按"字宽"切行：中文 1，英文/数字约 0.55（沿用 splitLines 的规则）
function wrapMeetingText(text, budget) {
  const out = [];
  for (const raw of String(text || "").split(/\r?\n/)) {
    if (!raw.trim()) { out.push(""); continue; }
    out.push(...splitLines(raw, budget));
  }
  while (out.length && !out[0].trim()) out.shift();
  while (out.length && !out[out.length - 1].trim()) out.pop();
  return out.length ? out : ["（没有内容）"];
}

function setMeetingPopup(brief, degraded) {
  // 小屏优先显示完整回答（算法名/结论/步骤），而不是摘要三件套。
  const raw = [];
  const answer = String((brief && brief.answer) || "").trim();
  if (answer) {
    answer.split(/\n+/).map((x) => x.trim()).filter(Boolean).slice(0, 4)
      .forEach((line) => raw.push(line));
  } else {
    if (brief && brief.summary) raw.push("回答：" + brief.summary);
    if (brief && Array.isArray(brief.keywords) && brief.keywords.length) {
      raw.push("关键词：" + brief.keywords.slice(0, 4).join(" / "));
    }
    (brief && Array.isArray(brief.points) ? brief.points : []).slice(0, 3)
      .forEach((p, i) => raw.push((i + 1) + ". " + p));
  }
  if (!raw.length) raw.push("（没有内容）");
  if (degraded) raw.push("· 本地兜底（补 Key 后升级）");
  const lines = [];
  for (const line of raw) lines.push(...wrapMeetingText(line, 30));
  if (meeting) {
    meeting.popup = { open: true, lines: lines.slice(0, MEETING_POPUP_ROWS) };
    meeting.answerBrief = brief || {};
  }
}

function closeMeetingPopup() {
  if (meeting && meeting.popup) meeting.popup.open = false;
  hideMeetingPopup();
}

function paintMeetingPopup() {
  if (!meeting || !meeting.popup || !meeting.popup.open) { hideMeetingPopup(); return; }
  if (!retained.popupLines) return;
  (retained.popupWidgets || []).forEach((w) => setHiddenSafe(w, false));
  const lines = meeting.popup.lines || [];
  putText(retained.popupTitle, "AI 回答" + (meeting.answerDegraded ? " · 兜底" : ""));
  for (let i = 0; i < MEETING_POPUP_ROWS; i++) {
    putSlot(retained.popupLines[i], lines[i] || "");
  }
  putText(retained.popupFoot, "左键刷新 · 右键关闭");
}

function meetingSpeakerNo(seg) {
  const list = (meeting && meeting.speakers) || [];
  let i = list.findIndex((s) => (s.id && s.id === seg.spk_id)
    || (s.spk_id && s.spk_id === seg.spk_id));
  if (i < 0) {
    const m = /(\d+)$/.exec(seg.spk_id || "");
    if (m) i = Math.max(0, Number(m[1]) - 1);
  }
  return i + 1;
}

function meetingRoleLine() {
  const speakers = (meeting && meeting.speakers) || [];
  if (!speakers.length) return meeting && meeting.segments && meeting.segments.length
    ? (meeting.segments.length + " 段 · " + (meeting.segmentCount || 0) + " 条转写")
    : "按确认开始录制";
  return speakers.slice(0, 3).map((s, i) =>
    "S" + (i + 1) + " " + (s.name && !/^发言人/.test(s.name) ? s.name : "发言人")).join(" · ")
    + (speakers.length > 3 ? " …" : "");
}

function meetingSubtitleRows() {
  const rows = [];
  const segs = (meeting && meeting.segments) || [];
  for (const seg of segs.slice(-MEETING_SUB_ROWS)) {
    const text = String(seg.content || "").replace(/\s+/g, " ").trim();
    if (!text) continue;
    const line = "S" + meetingSpeakerNo(seg) + " " + text;
    const parts = splitLines(line, 22);
    rows.push(parts[0] + (parts.length > 1 ? "…" : ""));
  }
  while (rows.length < MEETING_SUB_ROWS) rows.unshift("");
  return rows.slice(-MEETING_SUB_ROWS);
}

function checkMeetingMic() {
  // 平台/模拟器偶尔会在长请求后静默关掉 voice input：capture 还在，
  // 但音频帧不再来。超过 4.5 秒没有一帧就主动换一条语音会话。
  if (!meeting || !meeting.recording || !capture || capture.stopped) return;
  const now = Date.now();
  const last = capture.lastAudioAt || capture.startedAt || now;
  if (now - last < 4500) return;
  if (now - (capture.lastMicRestartAt || 0) < 9000) return;
  capture.lastMicRestartAt = now;
  meeting.note = "麦克风重连中…";
  try { restartSession(capture); } catch (e) { /* 等下一轮 */ }
}

function paintMeeting() {
  if (!meeting) return;
  checkMeetingMic();
  hideModeSel();
  showMeetingUi();
  const rec = !!meeting.recording;
  const secs = rec && meeting.startedAt ? clockOf(Date.now() - meeting.startedAt, 1) : "";
  const viewState = (meeting.view && meeting.view.state) || "";
  let status;
  if (meeting.thinking) status = "AI 正在想…";
  else if (meeting.note) status = String(meeting.note).slice(0, 28);
  else if (rec) status = "多人会议 · 录制中 " + secs + (viewState ? " · " + viewState : "");
  else status = "多人会议 · 未录制" + (viewState ? " · " + viewState : "");
  putText(retained.brand, "● MemoryLens");
  putText(retained.status, status);
  putText(retained.name, "多人会议");
  putText(retained.role, meetingRoleLine());
  putText(retained.sim, "");
  putText(retained.recBadge, rec ? "● 录制中 " + secs : "○ 未录制");
  retained.info.forEach((t) => putText(t, " "));

  const popupOpen = !!(meeting.popup && meeting.popup.open);
  if (popupOpen) {
    // 浮窗开着时，上半区让给回答
    putText(retained.name, " ");
    putText(retained.role, " ");
    putText(retained.sim, " ");
  } else {
    // 不显示字幕，改显示会议卡：状态/待办/总结（最多 3 行）
    // 眼镜上不显示会议总结：总结只留控制台/接口；这里只显示会议卡 rows。
    const rows = (meeting.view && Array.isArray(meeting.view.rows) ? meeting.view.rows : [])
      .filter((x) => String(x || "").trim());
    rows.slice(0, 3).forEach((line, i) => putText(retained.info[i], String(line).slice(0, 24)));
  }

  putText(retained.foot, rec
    ? "确认 停止 · 左问 右关 · 上打字"
    : (meeting.saved ? "确认 再录 · 左问 · 上打字 · 返回退出" : "确认 开始 · 左问 · 返回退出"));

  if (popupOpen) paintMeetingPopup();
  else hideMeetingPopup();
}

async function ensureMeetingSession() {
  if (!meeting) return "";
  if (meeting.session) return meeting.session;
  try {
    const r = await postJson("/api/meeting/start", {}, 8000);
    if (r && r.ok && r.session) {
      meeting.session = r.session;
      meeting.meetingId = r.meeting_id || "";
    }
  } catch (e) { /* 后台没起 */ }
  return meeting.session || "";
}

async function enterMeeting() {
  hideModeSel();
  stopScroll();
  lastScene = null;
  viewMode = "idle";
  complexRows = null;
  meeting = {
    session: "", meetingId: "", recording: false, stopping: false, startedAt: 0,
    segments: [], speakers: [], segmentCount: 0, duration: 0, note: "",
    saved: false, thinking: false, answerAsked: false, answerDegraded: false,
    view: null, summary: "", keyPoints: [], actionItems: [],
    popup: { open: false, lines: [] },
  };
  paintMeeting();
  await ensureMeetingSession();
  paintMeeting();
}

async function startMeetingRecording() {
  if (!meeting || meeting.recording || meeting.stopping) return;
  const sid = await ensureMeetingSession();
  if (!sid) {
    meeting.note = "后台没连上 · 先启动 start.bat";
    paintMeeting();
    return;
  }
  meeting.recording = true;
  meeting.stopping = false;
  meeting.startedAt = Date.now();
  meeting.saved = false;
  meeting.thinking = false;
  meeting.note = "";
  meeting.segments = [];
  meeting.speakers = [];
  meeting.segmentCount = 0;
  meeting.view = null;
  meeting.summary = "";
  meeting.keyPoints = [];
  meeting.answerAsked = false;
  closeMeetingPopup();
  paintMeeting();
  try {
    await startListening("meeting", sid);
  } catch (e) { /* 下面统一判断 */ }
  if (!capture) {
    meeting.recording = false;
    meeting.note = "麦克风没起来 · 再按一次确认";
    paintMeeting();
  }
}

async function stopMeetingRecording() {
  if (!meeting) return;
  if (meeting.stopping && meeting.stopPromise) {
    await meeting.stopPromise;
    return;
  }
  if (!meeting.recording) return;
  meeting.stopping = true;
  const p = (async () => {
    try { await stopAndArchive(); } catch (e) { /* stopAndArchive 自己会维护 UI */ }
    if (meeting) {
      meeting.stopping = false;
      meeting.recording = false;
      if (!meeting.note) meeting.note = "已停止 · 按确认继续录";
      paintMeeting();
    }
  })();
  meeting.stopPromise = p;
  await p;
}

async function ensureMeetingCapture() {
  // 有些平台在长时间 HTTP 请求/弹窗后会静默关掉 voice input，
  // 但 meeting.recording 还是 true；这里把它重新接上。
  if (!meeting || !meeting.recording || capture) return;
  meeting.note = "麦克风重连中…";
  paintMeeting();
  try { await startListening("meeting", meeting.session); } catch (e) { /* 下面统一判断 */ }
  if (!capture) {
    meeting.recording = false;
    meeting.note = "麦克风断了 · 按确认重开";
  } else {
    meeting.note = "录音继续";
  }
  paintMeeting();
}

async function flushMeetingAudio() {
  // 左键提问前，把当前还没切片的这几秒强制切出去并等转写回来；
  // 否则问题还在麦克风缓冲里，AI 只能看到上一段上下文。
  if (!meeting || !meeting.recording || !capture) return;
  const c = capture;
  try { cutSlice(c, 1000); } catch (e) { /* 音频太短就不强切 */ }
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline && (c.busy || (c.queue && c.queue.length))) {
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
}

async function askMeeting(question) {
  if (!meeting) return;
  meeting.thinking = true;
  if (meeting.recording) {
    meeting.note = "先听完最后一句…";
    paintMeeting();
    await flushMeetingAudio();
  }
  const sid = meeting.session || (await ensureMeetingSession());
  if (!sid) {
    meeting.thinking = false;
    meeting.note = "后台没连上 · 先启动 start.bat";
    paintMeeting();
    return;
  }
  meeting.thinking = true;
  meeting.note = "AI 正在想…";
  paintMeeting();
  try {
    const body = { session: sid };
    if (question) body.question = String(question).slice(0, 240);
    const r = await postJson("/api/meeting/ask", body, 30000);
    if (r && r.ok) {
      meeting.answerAsked = true;
      meeting.answer = r.answer || "";
      meeting.answerDegraded = !!r.degraded;
      setMeetingPopup(r, !!r.degraded);
      meeting.note = "";
    } else {
      meeting.note = "AI 失败：" + ((r && r.error) || "未知错误");
    }
  } catch (e) {
    meeting.note = "AI 请求失败 · 检查后端";
  }
  meeting.thinking = false;
  if (meeting.recording && !capture) await ensureMeetingCapture();
  paintMeeting();
}

async function exitMeeting() {
  if (!meeting) return;
  closeMeetingPopup();
  if (meeting.recording || meeting.stopping) {
    await stopMeetingRecording();
  }
  const sid = meeting.session;
  const empty = !meeting.segments.length && !meeting.answerAsked;
  meeting = null;
  hideMeetingUi();
  lastScene = null;
  if (sid && empty) {
    postJson("/api/meeting/discard", { session: sid }, 5000).catch(() => {});
  }
  // PRD：返回键退出多人模块，回到"模块选择页"
  openMode();
}

function navMeeting(act) {
  if (!meeting) return;
  // 确认键：录音启停（浮窗开着也照常）
  if (act === "activate") {
    if (meeting.recording) stopMeetingRecording();
    else startMeetingRecording();
    return;
  }
  if (act === "left") { askMeeting(); return; }
  // 浮窗开着时，右键/返回键都只关浮窗，绝不能顺势退出并停止录音。
  if (act === "right") { if (meeting.popup && meeting.popup.open) closeMeetingPopup(); return; }
  if (act === "back" && meeting.recording && meeting.popup && meeting.popup.open) {
    // 录音中返回键也只关浮窗；停录后返回仍然正常退出。
    closeMeetingPopup();
    return;
  }
  if (act === "up") {
    // 回答已截断，不需要滚动；浮窗关闭时上键打开短语键盘，用文字提问/发指令
    if (!(meeting.popup && meeting.popup.open)) openText("keys");
    return;
  }
  if (act === "down") return;
  if (act === "back") { exitMeeting(); return; }
}

function onMeetingFeed(res) {
  if (!meeting || !res) return;
  if (res.session && meeting.session && res.session !== meeting.session) return;
  if (Array.isArray(res.segments)) meeting.segments = res.segments;
  if (Array.isArray(res.speakers)) meeting.speakers = res.speakers;
  if (res.view) meeting.view = res.view;
  meeting.segmentCount = res.segment_count || (meeting.segments || []).length;
  meeting.duration = res.duration_sec || meeting.duration;
  if (!meeting.thinking) meeting.note = "";
  paintMeeting();
}

async function meetingFeed(c, wav) {
  try {
    const res = await postJson("/api/meeting/feed",
      { session: c.agentSession, audio: wav, auto_add: true }, 45000);
    if (capture && capture !== c) return;
    c.sent += 1;
    if (res && res.ok) onMeetingFeed(res);
    else if (meeting && !meeting.thinking) {
      meeting.note = "转写失败 · 等下一片";
      paintMeeting();
    }
  } catch (e) {
    if (meeting && !meeting.thinking) {
      meeting.note = "转写超时 · 继续录";
      paintMeeting();
    }
  }
}
// ---------- 录音：持续听，按 3.5 秒切片送识别 ----------
//
// 平台的语音会话是"一次最多 60 秒、64 帧缓冲"，所以"一直听"不能靠一条长会话：
// ① 每 40ms 把帧泵出来（不泵就丢音）
// ② 每 55 秒切一片：第一片拿去认人，之后的每一片接进这段对话
// ③ 会话到 55 秒主动轮换一条新的（中间有毫秒级空档，对识别无影响）
// ④ 边切边传，不在内存里堆整段（要支持 30 分钟）

const SLICE_MS = Number(process.env.MEMORYLENS_SLICE_MS || 55000);
const FIRST_SLICE_MS = Number(process.env.MEMORYLENS_FIRST_SLICE_MS || 0);
const SILENT_PEAK = 60;      // 整片峰值低于这个数就认为麦克风没进声音   // 第一片短一点，先认人         // 一片 55 秒：正好卡在平台 60 秒会话上限之前
const MAX_SESSION_MS = 59000;   // 平台 60 秒硬上限，留 1 秒余量再让平台自己 end
const SLICE_MIN_MS = 5000;      // 停止时最后一片不足这个数就丢掉
const MAX_LISTEN_MS = 30 * 60 * 1000;   // 兜底：连续听 30 分钟自动停
const AUTO_ARCHIVE = process.env.MEMORYLENS_ARCHIVE !== "0";   // 停止后把整段对话整理进档案

let capture = null;   // {session,chunks,samples,queue,busy,personId,personName,sessionId,slices,sent,lastCard,stopped,dropped}

// 在听页每秒改一次文字会让整块面板重绘（镜片上就是闪），所以按 10 秒取整
function clockOf(ms, step) {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const q = step ? Math.floor(sec / step) * step : sec;
  const m = Math.floor(q / 60);
  const r = q % 60;
  return m + ":" + (r < 10 ? "0" + r : r);
}

function listenLine() {
  const c0 = capture;
  if (c0 && !c0.session) return "等「确认采集」";
  const c = capture;
  const tail = c.sent ? " · " + c.sent + " 段" : "";
  const silent = c.samples === 0 && Date.now() - c.startedAt > 3000;
  return "在听 " + clockOf(Date.now() - c.startedAt, 10) + (silent ? " · 没声音" : tail);
}

// 只刷"状态行 + 底栏"；卡片内容由切片结果负责画，避免每 400ms 把卡片冲掉
let listenSig = "";
// 把最近 24 帧的音量画成一条"波形"：▁▂▃▄▅▆▇ 七档
const BARS = " .:-=+*#";
function levelBar(n) {
  const ps = (capture && capture.peaks) || [];
  if (!ps.length) return "";
  const width = n || 12;
  let out = "";
  for (const v of ps.slice(-width)) {
    // 有声音就给至少一格，否则整行空白看着像"没有波形"
    const i = Math.min(7, Math.max(v > 0.002 ? 1 : 0, Math.round(v * 7)));
    out += BARS[i];
  }
  return out;
}

// 卡片占屏时，把 6 格迷你条拼到状态行后面，保证任何时候都看得见音量
function statusWithBar(line) {
  if (capture && capture.noSoundUntil && Date.now() < capture.noSoundUntil) {
    return "麦克风没声音";
  }
  const ps = (capture && capture.peaks) || [];
  if (!ps.length) return line;
  const quiet = ps.slice(-6).every((v) => v < 0.03);
  return line + " " + (quiet ? "····" : levelBar(6));
}

function paintListening(msg, holdMs) {
  hideModeSel();
  if (!capture) return;
  if (msg) { capture.msg = msg; capture.msgUntil = Date.now() + (holdMs || 4000); }
  const thinking = capture.thinkingUntil && Date.now() < capture.thinkingUntil ? "在想…" : "";
  const flash = !thinking && capture.msg && Date.now() < capture.msgUntil ? capture.msg : "";
  const held = capture.lastCard && capture.showUntil && Date.now() < capture.showUntil;
  const c0 = held ? capture.lastCard : null;
  const left = c0 ? Math.max(0, Math.ceil((capture.showUntil - Date.now()) / 1000)) : 0;
  // 回复在的时候，状态行改成倒计时，让人知道还有几秒
  const identTarget = Math.round(IDENTIFY_MS / 1000);
  const identSecs = capture.identifying
    ? Math.min(identTarget, Math.round((Date.now() - capture.identAt) / 1000)) : 0;
  // 临时提示优先于「在想…」：后台偶尔慢的时候，让人知道还在跑，而不是以为卡死了
  const head = flash || thinking
    || (capture.identifying ? ("识别人 " + identSecs + "/" + identTarget) : "")
    || (c0 ? ("回复 · " + left + "s") : statusWithBar(listenLine()));
  // 认出人之后先只显示档案，过几秒再出三个按钮；这段时间按键仍然有效
  const buttonsReady = !capture.buttonsAt || Date.now() >= capture.buttonsAt;
  // 整屏指纹：文字没变就整帧跳过
  const sig = head + "|" + buttonsReady + "|" + (c0 ? left + "|" + (c0.lines || []).join("~")
    : levelBar() + "|" + presetIdx);
  if (sig === listenSig) return;
  listenSig = sig;
  putText(retained.status, head);

  // 上半区：人（有回复时把回复的三行放这里）
  if (c0) {
    putText(retained.name, c0.name || "陌生人");
    putText(retained.role, c0.layout === "notice" ? "多人对话 · 没入库" : (c0.role || ""));
    putText(retained.sim, c0.status !== "unknown" && typeof c0.similarity === "number"
      && c0.matched_by !== "pinned" && !c0.created
      ? "SIM " + c0.similarity.toFixed(2) : "");
    // 名字/身份已经在上面两行了，这里只放"信息行"，最多三行
    const role = (c0.role || "").trim();
    const rows = [];
    for (const ln of (c0.lines || [])) {
      if (role && String(ln).trim() === role) continue;   // 身份那行已经在上面显示过了
      for (const seg of splitLines(ln, 6)) rows.push(seg);
    }
    retained.info.slice(0, 3).forEach((t, i) => putText(t, rows[i] || ""));
  } else {
    const silent = capture.samples === 0 && Date.now() - capture.startedAt > 3000;
    putText(retained.name, "在听…");
    putText(retained.role, silent ? "点「确认采集」" : "直接说话就行");
    putText(retained.sim, " ");
    retained.info.slice(0, 3).forEach((t) => putText(t, " "));
  }

  // 下半区：单人还没认出人 → 只给一个「识别人」；认出之后（多人会议不需要认人）→ 三个预设按钮
  if (!capture.identified && capture.mode !== "meeting") {
    const secs = capture.identifying
      ? Math.min(identTarget, Math.round((Date.now() - capture.identAt) / 1000)) : 0;
    putText(retained.info[3], "> 识别人");
    putText(retained.info[4], capture.identifying
      ? (secs + "/" + identTarget + " 秒") : ("录 " + identTarget + " 秒来找人"));
    putText(retained.info[5], " ");
    putText(retained.foot, capture.identifying ? "再按确认 提前结束" : "确认 识别人");
  } else if (!buttonsReady) {
    // 档案先单独停几秒：让人看清是谁，再给按钮
    retained.info.slice(3, 6).forEach((t) => putText(t, " "));
    putText(retained.foot, "先看档案 · 按钮 " + Math.ceil((capture.buttonsAt - Date.now()) / 1000) + " 秒后出现");
  } else {
    const chips = PRESETS.map((p, i) => (i === presetIdx ? "> " : "  ") + p.label);
    retained.info.slice(3, 6).forEach((t, i) => putText(t, chips[i] || " "));
    putText(retained.foot, capture.mode === "meeting" ? "确认追问 返回归档" : "上下换 确认发");
  }
  paintedRows = "";
  cardRows = [];
}

// 调试用：容器里没有声卡时，用正弦波顶替麦克风，方便验证波形与切片链路
const FAKE_MIC = process.env.MEMORYLENS_FAKE_MIC === "1";

function makeFakeSession() {
  let t = 0;
  let lastAt = 0;
  return {
    takeEvent() {
      // 按真实节奏出帧：每次最多一帧 20ms，否则会把 pump 的 while 循环堵死
      if (Date.now() - lastAt < 20) return null;
      lastAt = Date.now();
      if (t >= 16000 * 60) return { type: "end", status: "stopped" };
      const frames = 1;                       // 每次只给 20ms，跟真实设备同节奏
      const buf = Buffer.alloc(320 * 2 * frames);
      const amp = 6000 * (0.4 + 0.6 * Math.abs(Math.sin(t / 16000 * Math.PI * 2)));
      for (let i = 0; i < 320 * frames; i++) {
        const v = Math.round(amp * Math.sin((t + i) / 16000 * Math.PI * 2 * 220));
        buf.writeInt16LE(v, i * 2);
      }
      t += 320 * frames;
      return { type: "audio", sampleRate: 16000, channels: 1, format: "s16le", sampleCount: 320 * frames, data: buf };
    },
    finish() {},
    cancel() {},
  };
}

// 关掉当前会话、等平台回收完再开下一条（end 事件之后必须留出这段时间）
function restartSession(c) {
  const old = c.session;
  c.session = null;
  closeSession(old, c, () => {
    if (!capture || capture.stopped) return;
    setTimeout(() => {
      if (!capture || capture.stopped || capture.session) return;
      openSession();
    }, 300);
  });
}

// 关掉一条语音会话：先 finish() 让平台把排队音频发完，再把 audio 收进当前切片、
// 把 end 读走。不能先 cancel()（会丢弃排队音频），也不能把 audio 丢掉。
function closeSession(session, c, done) {
  if (!session) { if (done) done(); return; }
  try { session.finish(); } catch (e) { /* 已结束 */ }
  const t0 = Date.now();
  (function drainEnd() {
    let guard = 0;
    try {
      while (guard++ < 64) {
        const ev = session.takeEvent();
        if (!ev) break;
        if (ev.type === "audio" && c) {
          const buf = Buffer.from(ev.data);
          c.chunks.push(buf);
          c.samples += ev.sampleCount;
        } else if (ev.type === "end") {
          if (done) done();
          return;
        }
      }
    } catch (e) { /* 会话已失效 */ }
    if (Date.now() - t0 < 1500) setTimeout(drainEnd, 60);
    else if (done) done();
  })();
}

function openSession() {
  if (!capture) return;
  // 上一会话可能还没被平台回收（"麦克风繁忙"多半就是它）——先显式收掉
  try {
    if (capture.session) capture.session.finish();
  } catch (e) { /* 已经结束 */ }
  capture.session = null;
  let session;
  try {
    session = FAKE_MIC ? makeFakeSession() : input.requestVoiceInput({ mode: "audio" });
  } catch (e) {
    const msg = String((e && e.message) || e || "");
    if (/end event|consume/i.test(msg)) {
      // 平台还在回收上一条会话：不结束这一段，稍后重试
      setTimeout(() => { if (capture && !capture.stopped) openSession(); }, 800);
      return;
    }
    stopAndArchive("开不了麦克风 · " + (msg.length > 18 ? msg.slice(0, 18) : msg));
    return;
  }
  capture.session = session;
  capture.sessionStartedAt = Date.now();
  pump();
}

function peakOf(data) {
  if (!data) return 0;
  let view = null;
  if (typeof ArrayBuffer !== "undefined" && data instanceof ArrayBuffer) {
    view = new Int16Array(data);
  } else if (ArrayBuffer.isView(data)) {
    if (data.BYTES_PER_ELEMENT === 2) {
      view = new Int16Array(data.buffer, data.byteOffset, Math.floor(data.byteLength / 2));
    } else {
      view = new Int16Array(data.buffer, data.byteOffset, Math.floor(data.byteLength / 2));
    }
  } else {
    return 0;
  }
  let max = 0;
  for (let i = 0; i < view.length; i++) {
    const v = view[i] < 0 ? -view[i] : view[i];
    if (v > max) max = v;
  }
  return max;
}

function pump() {
  if (!capture || capture.stopped) return;
  const c = capture;
  // restartSession 会把 c.session 置空，等平台回收后再 openSession；
  // 这期间旧的 pumpTimer 仍可能烧到，不能直接 c.session.takeEvent()。
  if (!c.session) {
    c.pumpTimer = setTimeout(pump, 40);
    return;
  }
  let ev;
  let guard = 0;             // 单次最多处理 8 个事件，避免把渲染线程饿死
  while (guard++ < 8 && (ev = c.session.takeEvent())) {
    if (ev.type === "audio") {
      const buf = Buffer.from(ev.data);
      c.chunks.push(buf);
      if (c.identifying) {
        c.identChunks.push(buf);
        if (Date.now() - c.identAt >= IDENTIFY_MS) finishIdentify(c);
      }
      c.samples += ev.sampleCount;
      c.lastAudioAt = Date.now();
      // 用 PCM 绝对值的峰值估音量，画音量条用。
      // 平台给的不一定是 Uint8Array：有可能是 Int16Array 或 ArrayBuffer，
      // 直接按 int16 视图读，避免"有声音但波形不动"。
      const peak = peakOf(ev.data);
      if (!c.__fmt) {
        c.__fmt = 1;
        console.log("[audio] type=" + (ev.data && ev.data.constructor && ev.data.constructor.name) +
          " bytes=" + (ev.data && ev.data.byteLength) + " samples=" + ev.sampleCount + " peak=" + peak);
      }
      c.level = Math.min(1, Math.sqrt(peak / 9000));
      c.peaks.push(c.level);
      if (c.peaks.length > 24) c.peaks.shift();
    } else if (ev.type === "end") {
      // 平台把这条会话关了：换一条接着听（保留已累积的缓冲）。
      // 注意：不能在收到 end 的同一拍里 requestVoiceInput —— 平台还没回收上一条会话，
      // 会报 "must consume the previous voice session's end event before requesting another"。
      if (!c.stopped) { restartSession(c); return; }
      return;
    }
  }
  // 到点就收尾：不能只靠音频帧触发 —— 没麦克风或完全静音时永远等不到
  if (c.identifying && Date.now() - c.identAt >= IDENTIFY_MS) finishIdentify(c);
  // 光看字节数不够：平台可能一口气把缓冲全塞过来，0.2 秒就"攒够 55 秒"，
  // 于是刚进单人模式就立刻切一片去认人（实测会凭空冒出「陌生人A」）。
  // 所以再要求这一片至少经过 Math.min(target, 3s) 的真实时间。
  const sliceStart = c.sliceStart || c.startedAt;
  const elapsed = Date.now() - sliceStart;
  // 单人：不自动切片，一切由「识别人 / 三个按钮 + 确认」手动驱动；
  // 多人：PRD 要"实时字幕"，所以会议模式按 6s 首片 / 8s 后续切，边录边出字。
  const cutAfterMs = c.mode === "meeting"
    ? (c.sent === 0 && MEETING_FIRST_SLICE_MS > 0 ? MEETING_FIRST_SLICE_MS : MEETING_SLICE_MS)
    : (c.sent === 0 && FIRST_SLICE_MS > 0 ? FIRST_SLICE_MS : SLICE_MS);
  const shouldCut = c.mode === "meeting"
    && c.samples >= 16000 * (cutAfterMs / 1000) && elapsed >= 4000;
  if (shouldCut) cutSlice(c);
  if (Date.now() - c.sessionStartedAt >= MAX_SESSION_MS) {
    if (!c.stopped) { restartSession(c); return; }
  }
  if (Date.now() - c.startedAt >= MAX_LISTEN_MS) {
    stopAndArchive("听满 30 分钟 · 自动停了");
    return;
  }
  c.pumpTimer = setTimeout(pump, 40);
}

function cutSlice(c, minMs) {
  const bytes = c.samples * 2;
  const chunks = c.chunks;
  c.chunks = [];
  c.samples = 0;
  c.sliceStart = Date.now();          // 下一片重新计时
  // 整片静音就别上传了：既省一次 ASR，也能让镜片明确告诉用户"没收到声音"
  let slicePeak = 0;
  for (const buf of chunks) {
    for (let i = 0; i + 1 < buf.length; i += 2) {
      const v = Math.abs(buf.readInt16LE(i));
      if (v > slicePeak) slicePeak = v;
    }
  }
  if (slicePeak < SILENT_PEAK) {
    c.noSoundUntil = Date.now() + 6000;
    c.silentSlices = (c.silentSlices || 0) + 1;
    return;
  }
  const need = minMs || SLICE_MIN_MS;
  if (bytes < 16000 * 2 * (need / 1000)) return;
  c.queue.push({ wav: toWav(chunks, bytes), manual: !!c.manualNext,
                 preset: c.pendingPreset || "" });
  c.manualNext = false;
  c.pendingPreset = "";
  drain(c);
}

// 队列里只有一种东西：一段 WAV。
// 还没认人时，这段先拿去认人；认出来之后，所有段都直接进这段对话。
async function drain(c) {
  if (c.busy) return;
  c.busy = true;
  try {
    while (c.queue.length) {
      const item = c.queue.shift();
      const wav = item && item.wav ? item.wav : item;
      const manual = !!(item && item.manual);
      const preset = (item && item.preset) || "";
      if (c.mode === "meeting") {
        await meetingFeed(c, wav);
        continue;
      }
      if (!c.personId) {
        await identify(c, wav);
        continue;
      }
      await pushChunk(c, wav, manual, preset);
    }
  } finally {
    c.busy = false;
  }
}







async function startListening(mode, agentSession) {
  if (capture || background) return;
  // 新的一段：capture 是全新的对象，identified 从头就是 null，
  // 不会沿用上一段的人（上一段的异步结果由 capture!==c 的守卫挡掉）。
  listenSig = "";
  // 上一段刚结束时会话还没被平台释放，立刻再申请就会报"麦克风繁忙"
  const cool = 2200 - (Date.now() - (endedAt || 0));
  if (cool > 0) {
    paintIdle("稍等 · 麦克风正在释放");
    await new Promise((r) => setTimeout(r, cool));
  }
  let availability = { canRequest: true };
  if (!FAKE_MIC) {
    try {
      availability = input.getVoiceInputAvailability("audio");
    } catch (e) {
      paintIdle("这台设备没有麦克风");
      return;
    }
  }
  if (!availability || availability.canRequest === false) {
    // 常见原因是上一次录音还没被回收，等一会儿再试
    for (let i = 0; i < 4 && availability && availability.canRequest === false; i++) {
      paintIdle("麦克风忙 · 等一下再试");
      await new Promise((r) => setTimeout(r, 700));
      try {
        availability = input.getVoiceInputAvailability("audio");
      } catch (e) {
        availability = null;
      }
    }
  }
  if (!availability || availability.canRequest === false) {
    paintIdle("麦克风被占用 · 稍后再按一次");
    return;
  }
  capture = {
    lastCard: null,
    identified: null,     // {id,name}
    identifying: false,
    identChunks: [],
    identAt: 0,
    showUntil: 0,
    level: 0,          // 最近一帧的音量（0..1）
    peaks: [],         // 最近 N 帧的音量，用来画音量条
    session: null, chunks: [], samples: 0,    startedAt: Date.now(), sessionStartedAt: 0,
    lastAudioAt: Date.now(), lastMicRestartAt: 0,
    slices: 0, sent: 0, queue: [], busy: false,
    mode: mode === "meeting" ? "meeting" : "single",
    agentSession: agentSession || ("g-" + Date.now().toString(36)),
    personId: "", personName: "", sessionId: null,
    lastCard: null, stopped: false, dropped: 0, pumpTimer: null,
  };
  // 单人开始会清掉上一场会议；会议结束（返回归档）后保留，文本指令还能回看。
  if (capture.mode === "meeting") lastMeeting = { session: capture.agentSession, at: Date.now() };
  else lastMeeting = null;
  viewMode = "idle";
  complexRows = null;
  openSession();
  if (capture && capture.mode === "meeting" && meeting) paintMeeting();
  else paintListening();
}

// 按「返回」：收尾最后一片 → 等队列传完 → 让模型析出要点 → 写进那个人的档案
async function identify(c, wav) {
  // 第一片：声纹认人（陌生人由后端自动建号）+ 让智能体给出第一版要点
  let res;
  try {
    res = await postJson("/api/agent/feed", {
      session: c.agentSession, audio: wav, auto_add: true, mode: c.mode,
    }, 30000);
  } catch (e) {
    if (!outbox.push(wav)) c.dropped += 1;
    return;
  }
  if (capture && capture !== c) return;   // 这一段已经结束、又开了新的一段：旧结果不要画上去
  c.sent += 1;
  const v = (res && res.view) || null;
  if (!v) return;
  c.personId = v.person_id || "";
  c.personName = v.person || "";
  if (!c.personId) return;
  c.slices += 1;
  c.lastCard = agentCard(v);
  c.showUntil = Date.now() + HOLD_MS;
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
    // pinned 的 1.00 不是"像"，是"沿用已认出的人"，别在镜片上显示成相似度
    matched_by: v.matched_by || "",
  };
}

async function ensureSession(c) {
  return !!c.agentSession;
}

async function pushChunk(c, wav, manual, preset) {
  // 认人之后：每按一次确认 = 一次（manual）；老路径（55 秒自动）保留 manual=false
  try {
    const res = await postJson("/api/agent/feed", {
      session: c.agentSession, audio: wav, auto_add: true, mode: c.mode,
      manual: !!manual, preset: preset || "", person_id: c.personId || "",
    }, 30000);
    if (capture && capture !== c) return;   // 换段了：这段的回复不能盖到新的一段上
    c.sent += 1;
    const v = (res && res.view) || null;
    c.thinkingUntil = 0;
    if (!v) return;
    if (v.person_id) { c.personId = v.person_id; c.personName = v.person; }
    if (v.rows && v.rows.length) {
      c.lastCard = agentCard(v);
      c.lastRev = res.rev;
      c.lastMs = v.ms || 0;
      // 关键：没有 showUntil，"在听"页就会立刻把这张卡刷掉
      c.showUntil = Date.now() + (manual ? PRESET_HOLD_MS : HOLD_MS);
      lastScene = { card: c.lastCard, rev: res.rev, ms: v.ms || 0 };
      paintCard(c.lastCard, res.rev, v.ms || 0);
      paintListening();
    }
  } catch (e) {
    if (!outbox.push(wav)) c.dropped += 1;
  }
}

async function stopAndArchive(msg) {
  if (capture) capture.thinkingUntil = 0;
  if (capture) {
    capture.stopped = true;
    putText(retained.status, "收尾 · 传剩余录音…");
  }
  const c = capture;
  if (!c) return;
  listenSig = "";
  c.stopped = true;
  if (c.pumpTimer) clearTimeout(c.pumpTimer);
  const closing = c.session;
  c.session = null;
  // finish() 会把排队音频发完再给 end；必须等 closeSession 收完音频再组 WAV
  await new Promise((resolve) => closeSession(closing, c, resolve));
  const bytes = c.samples * 2;
  // 会议的最后 1-2 秒常常就是"下周三之前"这种关键句，不能按单人的 5 秒门槛丢掉
  const tailMin = c.mode === "meeting" ? 1200 : SLICE_MIN_MS;
  if (bytes >= 16000 * 2 * (tailMin / 1000)) {
    c.queue.push({ wav: toWav(c.chunks, bytes), manual: true });
  }
  c.samples = 0;
  c.chunks = [];
  capture = null;
  const revAtStop = shownRev;
  endedAt = Date.now();
  putText(retained.foot, "收尾 · 传剩余录音…");
  await drain(c);
  // drain() 在 busy 时会直接返回；归档前必须等队列真正清空，
  // 否则会议 meta 会漏掉最后几片（尤其最后一句待办）。
  const drainT0 = Date.now();
  while ((c.busy || c.queue.length) && Date.now() - drainT0 < 45000) {
    if (!c.busy && c.queue.length) drain(c).catch(() => {});
    await new Promise((r) => setTimeout(r, 100));
  }
  if (c.mode === "meeting") {
    const m = meeting;
    if (m && m.session === c.agentSession) {
      m.note = "正在保存会议…";
      paintMeeting();
    }
    let r = null;
    try {
      // PRD：停止录制时断开语音连接、持久化音频与转写；后端顺便归档会议图状态
      r = await postJson("/api/meeting/stop", { session: c.agentSession }, 30000);
    } catch (e) {
      r = { ok: false, error: (e && e.message) || "后台没响应" };
    }
    if (m && m.session === c.agentSession) {
      m.recording = false;
      m.stopping = false;
      m.thinking = false;
      m.saved = !!(r && r.ok);
      if (r && r.ok) {
        m.segmentCount = r.segment_count || m.segmentCount;
        m.duration = r.duration_sec || m.duration;
        m.summary = r.summary_text || r.summary || "";
        m.keyPoints = Array.isArray(r.key_points) ? r.key_points : [];
        m.actionItems = Array.isArray(r.action_items) ? r.action_items : [];
        m.view = null;   // 停录后会议卡让位给总结
        // 眼镜上只提示保存结果，不显示会议总结（总结在控制台看）
        m.note = "会议已保存 · " + (r.speaker_count || 0) + " 人 / "
          + (r.segment_count || 0) + " 段";
      } else {
        m.note = "会议保存失败 · " + ((r && r.error) || "未知错误");
      }
      paintMeeting();
    } else {
      paintIdle("会议已保存");
    }
    return;
  }
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
    const analyzed = await postJson("/api/converse/analyze", { session: c.sessionId }, 30000);
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
// 主页面快捷预设：id 与服务端 PRESETS 对应，提交时会把 hint 一起发过去
let PRESETS = [
  { id: "hint", label: "提词" },
  { id: "todo", label: "待办" },
  { id: "recall", label: "以前" },
];
let presetIdx = 0;

// 按钮在后台可编辑，启动时拉一次（失败就用上面的默认三个）
async function loadUiSettings() {
  try {
    const d = await getJson("/api/settings");
    const hold = Number(d && d.hold_sec);
    const first = Number(d && d.first_hold_sec);
    if (hold >= 3) PRESET_HOLD_MS = Math.round(hold * 1000);
    if (first >= 3) HOLD_MS = Math.round(first * 1000);
    const ident = Number(d && d.identify_sec);
    // 显式写了环境变量就以 env 为准（测试/演示要缩短识别人时长）
    if (ident >= 5 && !process.env.MEMORYLENS_IDENTIFY_MS) IDENTIFY_MS = Math.round(ident * 1000);
  } catch (e) { /* 用默认值 */ }
}

async function loadPresetList() {
  try {
    const d = await getJson("/api/presets");
    const items = (d && d.items) || [];
    if (items.length) {
      PRESETS = items.slice(0, 3).map((x) => ({ id: x.id, label: x.label }));
      if (presetIdx >= PRESETS.length) presetIdx = 0;
      listenSig = "";
    }
  } catch (e) { /* 用默认值 */ }
}

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
  if (meeting) { paintMeeting(); return; }
  if (capture) { paintListening(); return; }
  if (lastScene && lastScene.card) { paintCard(lastScene.card, lastScene.rev, lastScene.ms); return; }
  paintIdle("按一下开始听 · 上=打字 下=看建议");
}

function keyRowsView() {
  return KEY_ROWS.map((row, r) =>
    row.map((k, c) => (r === textMode.r && c === textMode.c ? "【" + k + "】" : k)).join(" "));
}

function paintText() {
  hideModeSel();
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
  // 会议模式：问句走 AI 回答；其它短指令走会议文本指令（确认人数/切布局等）。
  if (meeting) {
    const isQuestion = /^[问查]/.test(t) || /[?？]/.test(t)
      || /什么|怎么|谁|哪|多少|几点|为什么|多久/.test(t);
    if (isQuestion) {
      askMeeting(t.replace(/^[问查]\s*/, "").trim() || t);
      return;
    }
    const sid = meeting.session || (await ensureMeetingSession());
    if (!sid) { meeting.note = "后台没连上"; paintMeeting(); return; }
    try {
      const resp = await postJson("/api/meeting/feed", { session: sid, text: t }, 15000);
      if (resp && resp.ok) onMeetingFeed(resp);
      else { meeting.note = "指令失败"; paintMeeting(); }
    } catch (e) {
      meeting.note = "指令失败 · 检查后端";
      paintMeeting();
    }
    return;
  }
  // 会议中的文本是对“当前会议”的指令，不能再开 text-* 新 session。
  let session = "text-" + Date.now().toString(36);
  let mode = "";
  if (capture) {
    session = capture.agentSession;
    mode = capture.mode;
  } else if (lastMeeting) {
    session = lastMeeting.session;
    mode = "meeting";
  }
  if (capture) {
    capture.thinkingUntil = Date.now() + 20000;
    paintListening("处理中…");
  }
  try {
    const body = { session: session, text: t, auto_add: false };
    if (mode) body.mode = mode;
    const resp = await postJson("/api/agent/feed", body, 30000);
    const v = resp && resp.view;
    if (!v) { if (capture) capture.thinkingUntil = 0; paintIdle("没拿到结果"); return; }
    const card = {
      kind: "agent",
      status: v.person ? "known" : "unknown",
      name: v.person || "文字",
      role: v.role || "",
      similarity: v.similarity || 0,
      lines: v.rows || [],
      state: v.state,
      layout: v.layout,
      options: v.options || [],
      degraded: !!v.degraded,
    };
    lastScene = { card: card, rev: resp.rev, ms: 0 };
    shownRev = resp.rev;
    if (capture) {
      capture.thinkingUntil = 0;
      capture.lastCard = card;
      capture.lastRev = resp.rev;
      capture.lastMs = v.ms || 0;
      capture.showUntil = Date.now() + 20000;
      paintCard(card, resp.rev, v.ms || 0);
      paintListening();
      return;
    }
    paintCard(card, resp.rev, v.ms || 0);
  } catch (e) {
    if (capture) capture.thinkingUntil = 0;
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
  { key: "single", label: "单人", hint: "一个人聊 · 55 秒出片" },
  { key: "meeting", label: "多人", hint: "2 人以上 · 分人 / 待办 / 归档" },
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

// 平台自带 Select（下拉/选项）——模式选择不再用一排 Text 手搓
let modeSel = null;
let modeSelTried = false;

function ensureModeSel() {
  if (modeSelTried) return modeSel;
  modeSelTried = true;
  try {
    if (!ui || !ui.Select || typeof ui.Select.create !== "function") return null;
    const s = ui.Select.create();
    s.setParent(retained.board);
    s.setIgnoreLayout(true);
    s.setPosition([8, 126, 0]);
    s.setSize([300, 44, 0]);
    s.setOptions(MODE_ITEMS.map((m) => m.label));
    s.getLocalStyle().setTextColor(COL.white);
    modeSel = s;
  } catch (e) {
    modeSel = null;
  }
  return modeSel;
}

function hideModeSel() {
  if (modeSel) {
    try { modeSel.setHidden(true); } catch (e) { /* 旧版本没有 hidden */ }
  }
}

function paintMode() {
  if (!modeSelect) return;
  putText(retained.status, "选模式 · 上下选 · 确认进入");
  putText(retained.name, "开始记录");
  putText(retained.role, "先选这一段是什么");
  putText(retained.sim, " ");
  // 模拟器/单色屏上原生 Select 的选中框不会跟着 index 走（只显示一个 []），
  // 统一用 Text 手搓，左右键一定能看到【】移动。
  const rows = MODE_ITEMS.map((m, i) => (i === modeSelect.index ? "【" + m.label + "】" : "  " + m.label));
  retained.info.forEach((t, i) => putText(t, rows[i] || ""));
  putText(retained.info[2], MODE_ITEMS[modeSelect.index].hint);
  putText(retained.foot, "上下选 确认进入 返回关掉");
}

function navMode(act) {
  if (!modeSelect) return;
  if (act === "left" || act === "up") {
    modeSelect.index = (modeSelect.index - 1 + MODE_ITEMS.length) % MODE_ITEMS.length;
    paintMode();
    return;
  }
  if (act === "right" || act === "down") {
    modeSelect.index = (modeSelect.index + 1) % MODE_ITEMS.length;
    paintMode();
    return;
  }
  if (act === "back") { closeMode(); return; }
  if (act === "activate") {
    const mode = MODE_ITEMS[modeSelect.index].key;
    modeSelect = null;
    if (mode === "meeting") {
      enterMeeting().catch(() => {});
      return;
    }
    startListening("single").catch(() => {});
    return;
  }
}

function handleNavigation() {
  const act = input.takeNavigationAction();
  if (!act) return;
  if (modeSelect) { navMode(act); return; }
  if (textMode) { navText(act); return; }
  // 多人会议模块：确认录/停、左键 AI、右键关浮窗、上下滚浮窗、返回退出
  if (meeting) { navMeeting(act); return; }
  if (capture && !capture.stopped) {
    const thinking = capture.thinkingUntil && Date.now() < capture.thinkingUntil;
    // 还没认出对方：确认 = 录 20 秒找人（再按一次提前结束），上下不切换
    if (!capture.identified && capture.mode !== "meeting") {
      if (act === "activate") {
        if (capture.identifying) { finishIdentify(capture); }
        else { startIdentify(capture); }
        return;
      }
      if (act === "back") {
        if (thinking) { cancelThinking(); return; }
        stopAndArchive("结束了");
        return;
      }
      return;
    }
    if (act === "back") {
      if (thinking) { cancelThinking(); return; }         // 想太久就中断这一问
      stopAndArchive("结束了");
      return;
    }
    if (act === "up") {                                   // 上下都用来换按钮
      presetIdx = (presetIdx - 1 + PRESETS.length) % PRESETS.length;
      listenSig = "";
      paintListening();
      return;
    }
    if (act === "down") {
      presetIdx = (presetIdx + 1) % PRESETS.length;
      listenSig = "";
      paintListening();
      return;
    }
    if (act === "activate") { submitPreset(); return; }    // 确认：用当前按钮开工作流
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

// 下键：把「当前预设」+ 这段录音一起交给智能体
function startIdentify(c) {
  if (!c || c.stopped) return;
  c.identifying = true;
  c.identAt = Date.now();
  c.identChunks = [];
  presetIdx = 0;
  const target = Math.round(IDENTIFY_MS / 1000);
  retained.status.setText("识别人 0/" + target);
  paintListening("对着对方说两句 · " + target + " 秒");
}

async function finishIdentify(c) {
  if (!c || !c.identifying) return;
  c.identifying = false;
  const chunks = c.identChunks;
  c.identChunks = [];
  c.chunks = [];            // 认人用的音频不要混进后面的提词切片
  c.samples = 0;
  if (!chunks.length) {
    // 整段没有音频：提示 3 秒，然后回到「识别人」页，再按确认就是重新录
    c.identified = null;
    c.buttonsAt = 0;
    paintListening("没录到声音 · 3 秒后回到识别人", 3000);
    return;
  }
  const wav = toWav(chunks, chunks.reduce((n, b) => n + b.length, 0));
  retained.status.setText("在想…");
  try {
    const r = await postJson("/api/voice/identify", { audio: wav }, 60000);
    // 识别期间用户可能已经结束这一段、甚至开了新的一段：结果只归它自己的 capture
    if (capture !== c) return;
    if (r && r.status === "known") {
      c.identified = { id: r.person_id, name: r.name };
      c.personId = r.person_id;
      c.personName = r.name;
      c.lastCard = r.card || null;
      c.showUntil = Date.now() + PRESET_HOLD_MS;
      c.buttonsAt = Date.now() + BUTTON_DELAY_MS;      // 先看档案，再出按钮
      if (c.lastCard) paintCard(c.lastCard, { ms: 0 });
      // 认出来之后立刻重画下半区：三个预设按钮要在这一帧就换上去
      paintListening("认出 " + r.name);
    } else if (r && r.status === "only_owner") {
      paintListening("只听到你自己");
    } else if (r && r.status === "no_owner") {
      paintListening("先录主人的声音");
    } else {
      paintListening("没认出对方");
    }
  } catch (e) {
    if (capture === c) paintListening("识别人失败 · 再试一次");
  }
}

function submitPreset() {
  const c = capture;
  if (!c || c.stopped) return;
  if (!c.personId) { paintListening("还在认人 · 再说两句"); return; }
  const p = PRESETS[presetIdx];
  const bytes = (c.samples || 0) * 2;
  c.thinkingUntil = Date.now() + 20000;
  if (bytes < 16000 * 2 * 1.5) {
    // 从上次提交到现在没怎么说话：不硬等，直接拿「档案 + 之前听到的话」问一次模型
    c.queue.push({ wav: "", manual: true, preset: p.id });
    drain(c);
    return;
  }
  c.manualNext = true;
  c.pendingPreset = p.id;
  cutSlice(c, 1500);
}

// 返回键在"在想…"时按下：让服务端作废这一问
function cancelThinking() {
  const c = capture;
  if (!c) return;
  c.thinkingUntil = 0;
  c.pendingPreset = "";
  const session = c.agentSession;
  postJson("/api/agent/cancel", { session }, 4000).catch(() => {});
  paintListening("已取消 · 再按一次重问");
}

// 录音中按一次确认：把"从上次发送到现在"的音频切下来，交给智能体回答
function manualAsk() {
  const c = capture;
  if (!c || c.stopped) return;
  if (!c.personId) {
    paintListening("还在认人 · 再说两句");
    return;
  }
  const bytes = (c.samples || 0) * 2;
  if (bytes < 16000 * 2 * 1.5) {          // 1.5 秒就够一句短指令
    paintListening("再说两句再按确认");
    return;
  }
  c.manualNext = true;
  c.thinkingUntil = Date.now() + 20000;   // 结果回来之前状态行一直是“在想…”
  cutSlice(c, 1500);
}

function clearView() {
  complexRows = null;
  viewMode = "idle";
  shownRev = Math.max(shownRev, (lastScene && lastScene.rev) || 0);   // 别让它立刻又画回来
  paintIdle("按一下开始听…");
}

async function poll() {
  // 会议模块自己维护字幕/浮窗，不让 /api/latest 的单人卡片顶上来
  if (meeting) return;
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
    if ((data.at || 0) < bootAt) return;      // 启动时就躺在 /api/latest 里的旧卡片，不画
    if (c0.kind === "agent" && !capture && Date.now() - endedAt < 15000) {
      // 上一段晚到的对话结果：不在待机时画，免得盖住当前画面
      return;
    }
    if (capture) {
      // 正在听：只更新数据，渲染交给 paintListening()（否则会在两屏之间来回跳）
      // 只收这一段开始之后产生的卡片；上一段留下的（比如「陌生人A」）不能一进来就顶上来
      if ((data.at || 0) < capture.startedAt) return;
      // 更硬的隔离：上一段晚到的归档/回复带着旧的 session，不能画到这一段上
      // （否则第二段会一直显示第一段认出的那个人和 SIM 1.00）
      if (c0.session && c0.session !== capture.agentSession) return;
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
    // 正在听/正在想，或者刚结束还在整理归档的时候，/api/latest 偶发超时
    // （后端在跑 ASR/模型）是正常的：不要画整页报错，真正失败会由那次请求自己报。
    const busyAfterStop = !capture && endedAt && Date.now() - endedAt < 45000;
    if (capture || busyAfterStop) { console.log("[poll] " + ((e && e.message) || e)); return; }
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
  if (meeting) {
    // 会议界面每 400ms 刷一次计时/录制动效；内容由 paintMeeting 内部去重
    paintMeeting();
    return;
  }
  if (capture) {
    // 在听的时候：详情页照常显示，但状态行继续走计时
    if (complexRows) {
      paintComplex(lastScene && lastScene.name ? lastScene.name : capture.personName);
      putText(retained.status, statusWithBar(listenLine()));
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
  console.log("[boot] buildHud begin");
  buildHud();
  console.log("[boot] buildHud done");
  paintIdle("按一下开始听…");   // 先把空闲态画出来，别留控件的默认 "Text"
  console.log("[boot] paintIdle done");
  loadUiSettings().catch(() => {});
  loadPresetList().catch(() => {});
  poll();
  pollTimer = setInterval(poll, POLL_MS);
  lastPaintAt = 0;
  if (typeof os.requestAnimationFrame === "function") rafId = os.requestAnimationFrame(tick);
  navTimer = setInterval(handleNavigation, NAV_MS);
  outboxTimer = setInterval(flushOutbox, OUTBOX_RETRY_MS);
  setTimeout(flushOutbox, 1200);        // 起来先把上次没发出去的补上
  try { require("@irisview/osx/os").setDisplayKeepAwake(true); } catch (e) { /* 可选能力 */ }
  // 调试用：没按键的容器里也能自动进入「在听」，配合 MEMORYLENS_FAKE_MIC 验证整条链路
  if (process.env.MEMORYLENS_AUTO_LISTEN === "1") {
    setTimeout(() => { if (!capture) startListening("single").catch((e) => console.log("[auto] " + (e && e.stack || e))); }, 1500);
  }
  console.log("[env] fake=" + process.env.MEMORYLENS_FAKE_MIC + " auto=" + process.env.MEMORYLENS_AUTO_LISTEN);
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

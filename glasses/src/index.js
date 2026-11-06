"use strict";

/**
 * MemoryLens 眼镜端（IrisView / FusionUI）
 *
 * 分工：
 *   手机或笔记本  ——  拍照、抓拍、调 /api/scan
 *   眼镜          ——  只负责"看"：轮询 /api/latest，把名字/身份/三条信息画在镜片上
 *
 * 为什么眼镜不自己拍：IrisView SDK 里没有任何摄像头 API（全仓 grep 证实），
 * 眼镜只能取麦克风（requestVoiceInput）和按键/手势（takeTextInput / 导航）。
 * 所以"拍照"这一半必须留在手机上，这也是戴眼镜时更自然的姿势。
 */

const { App, AppLifecycle, Window, WindowProperty } = require("@irisview/osx/os");
const { Board, Color, Font, FusionRoot, Text, TextAlign } = require("@irisview/osx/fusionui");

// 眼镜连哪台电脑。模拟器上就是本机；真机换成手机热点里那台的局域网 IP。
const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
const POLL_MS = 700;      // 轮询间隔：够快，又不至于把 CPU 烧了
const HTTP_TIMEOUT_MS = 900;

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
let pollTimer = null;
let inFlight = false;
let shownRev = -1;

function makeText(board, x, y, w, h, size, color, align) {
  const t = Text.create();
  t.setParent(board);
  t.setPosition([x, y, 0]);
  t.setSize([w, h, 0]);
  const style = t.getLocalStyle();
  style.setTextColor(color);
  style.setTextFont(Font.getCachedFont("", size, 0, 0));
  style.setTextAlign(align || TextAlign.LEFT_MID);
  return t;
}

function buildHud() {
  const property = WindowProperty.create();
  property.setTitle("MemoryLens");
  property.setSize([0.64, 0.48, 0.01]);   // 视口按米给，内容按像素排
  retained.window = Window.create(property);

  retained.root = FusionRoot.acquireFusionRootFromWindow(retained.window);
  retained.board = Board.create();
  retained.board.setParent(retained.root.getRootWidget());
  retained.board.setIgnoreLayout(true);
  retained.board.setSize([W, H, 0]);
  retained.board.getLocalStyle().setBackgroundColor(Color.fromRGB8(0, 0, 0));

  retained.brand = makeText(retained.board, 40, 26, 320, 28, 18, COL.cyan);
  retained.brand.setText("● MemoryLens");

  retained.status = makeText(retained.board, 40, 56, 560, 22, 14, COL.dim);
  retained.status.setText("正在连接后台…");

  retained.name = makeText(retained.board, 40, 108, 460, 56, 40, COL.white);
  retained.role = makeText(retained.board, 40, 168, 520, 30, 20, COL.cyan);
  retained.sim = makeText(retained.board, 500, 118, 100, 22, 13, COL.dim, TextAlign.RIGHT_MID);

  retained.info = [0, 1, 2].map((i) =>
    makeText(retained.board, 40, 222 + i * 36, 560, 30, 19, COL.white));

  retained.foot = makeText(retained.board, 40, H - 44, 560, 20, 13, COL.dim);
  retained.foot.setText("手机抓拍 · 眼镜显示");
}

// ---------- 取数据 ----------

function getJson(path) {
  // 优先用全局 fetch（Node 24 自带）；万一宿主没给，就退回 node:http
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

// ---------- 画 ----------

function paintIdle(msg) {
  retained.status.setText(msg);
  retained.name.setText("");
  retained.role.setText("");
  retained.sim.setText("");
  retained.info.forEach((t) => t.setText(""));
}

function paintOffline(err) {
  retained.status.setText("后台没连上：" + (err || "未知错误"));
  retained.name.setText("眼镜在线");
  retained.role.setText("手机端打开 http://<电脑IP>:8765 抓拍");
  retained.sim.setText("");
  retained.info[0].setText("· 确认电脑和眼镜在同一个网络");
  retained.info[1].setText("· 电脑上已运行 start.bat / server.py");
  retained.info[2].setText("");
}

function paintFace(face, rev, ms) {
  const known = face.status !== "unknown";
  retained.status.setText(known ? "已认识 · " + (ms || 0) + "ms" : "陌生人 · 不在人脉库");
  retained.name.setText(face.name || "陌生人");
  retained.role.setText(face.role || "");
  retained.sim.setText(known && typeof face.similarity === "number"
    ? "SIM " + face.similarity.toFixed(2) : "");

  const lines = (face.lines || []).slice(0, 3);
  retained.info.forEach((t, i) => t.setText(lines[i] || ""));
  shownRev = rev;
}

async function poll() {
  if (inFlight) return;
  inFlight = true;
  try {
    const data = await getJson("/api/latest");
    const faces = data.faces || [];
    if (data.rev === shownRev) return;          // 没新结果就别动屏幕
    if (!faces.length) {
      paintIdle(data.rev ? "没看到人，手机再抓拍一张" : "等待手机抓拍…");
      shownRev = data.rev;
      return;
    }
    paintFace(faces[0], data.rev, data.ms);
  } catch (e) {
    paintOffline(e && e.message);
  } finally {
    inFlight = false;
  }
}

function start() {
  if (retained.window) return;
  buildHud();
  poll();
  pollTimer = setInterval(poll, POLL_MS);
  console.log("MemoryLens glasses app ready → " + BACKEND);
}

App.createApp((lifecycle) => {
  if (lifecycle === AppLifecycle.ON_CREATE) start();
  if (lifecycle === AppLifecycle.ON_DESTROY) {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    retained.board = undefined;
    retained.root = undefined;
    retained.window = undefined;
  }
});

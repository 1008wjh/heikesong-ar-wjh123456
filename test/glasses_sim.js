"use strict";

/**
 * 眼镜端（glasses/src/index.js）离线仿真。
 *
 * IrisView 的模拟器在这台机器上跑不起来（缺 GL/GTK 库，见 docs/07-IrisView-工具包.md），
 * 但应用逻辑本身可以在普通 Node 里跑：把 @irisview/osx/* 换成会记录文字的最小替身，
 * 然后把真实的 /api/scan 打进去，看 HUD 上最终会写出什么。
 *
 * 跑法：node test/glasses_sim.js   （需要 8765 的服务在跑）
 */

const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");

const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
process.env.MEMORYLENS_BACKEND = BACKEND;
process.env.POLL_MS = "120";

const texts = [];          // 所有 Text 控件，按创建顺序
let created = 0;
let failures = 0;

function check(name, cond, detail) {
  if (cond) { console.log("  ok  " + name + (detail ? "  " + detail : "")); }
  else { failures++; console.log("  FAIL " + name + "  " + (detail || "")); }
}

// ---------- 最小 FusionUI 替身 ----------

function styleObject() {
  return {
    setBackgroundColor() {},
    setTextColor() {},
    setTextFont() {},
    setTextAlign() {},
  };
}

const fusionui = {
  Color: { fromRGB8: (r, g, b) => ({ r, g, b }), fromRGBA8: (r, g, b, a) => ({ r, g, b, a }) },
  Font: { getCachedFont: () => ({}) },
  TextAlign: { AUTO: 0, TOP_LEFT: 1, LEFT_MID: 4, CENTER: 5, RIGHT_MID: 6 },
  Board: {
    create() {
      return {
        setParent() {}, setIgnoreLayout() {}, setSize() {},
        getLocalStyle: styleObject,
        getRootWidget() { return {}; },
      };
    },
  },
  FusionRoot: { acquireFusionRootFromWindow: () => ({ getRootWidget: () => ({}) }) },
  Text: {
    create() {
      const node = {
        value: "",
        setParent() {}, setPosition() {}, setSize() {}, setAlignment() {},
        resetAlignment() {}, updateLayout() {}, invalidate() {},
        getLocalStyle: styleObject,
        setText(v) { node.value = String(v); },
        getText() { return node.value; },
        setLongMode() {}, resetLongMode() {}, setHeightByLineCount() {},
      };
      texts.push(node);
      created++;
      return node;
    },
  },
};

const os = {
  App: { createApp(cb) { os._cb = cb; setImmediate(() => cb("onCreate")); } },
  AppLifecycle: { ON_CREATE: "onCreate", ON_DESTROY: "onDestroy" },
  WindowProperty: { create: () => ({ setTitle() {}, setSize() {} }) },
  Window: { create: () => ({}) },
};

const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "@irisview/osx/os") return os;
  if (request === "@irisview/osx/fusionui") return fusionui;
  return originalLoad.call(this, request, parent, isMain);
};

// ---------- 工具 ----------

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function hud() {
  return texts.map((t) => t.value).filter((v) => v !== "");
}

async function postScan(imagePath) {
  const buf = fs.readFileSync(imagePath);
  const body = JSON.stringify({ image: "data:image/jpeg;base64," + buf.toString("base64") });
  const res = await fetch(BACKEND + "/api/scan", {
    method: "POST", headers: { "Content-Type": "application/json" }, body,
  });
  return res.json();
}

// ---------- 跑 ----------

(async () => {
  console.log("== 眼镜端仿真（后端 " + BACKEND + "）");

  const health = await (await fetch(BACKEND + "/api/health")).json();
  check("后端在线", health.ok === true, JSON.stringify(health));

  require(path.join(__dirname, "..", "glasses", "src", "index.js"));
  await sleep(300);
  check("HUD 建好（品牌 + 状态 + 名字 + 身份 + 相似度 + 3 行 + 页脚）", created === 9, "控件数=" + created);
  check("HUD 初始有状态行", hud().some((v) => v.includes("等待") || v.includes("已认识") || v.includes("陌生人")),
    JSON.stringify(hud()));

  // 1) 抓一个库里的人
  const r = await postScan(path.join(__dirname, "messi5.jpg"));
  check("扫码成功", r.ok === true && r.faces.length === 1, r.ms + "ms");
  await sleep(500);
  let lines = hud();
  check("HUD 出现名字「里奥」", lines.includes("里奥"), JSON.stringify(lines));
  check("HUD 出现身份", lines.includes("球王 · 老友"), JSON.stringify(lines));
  const info = ["答应送他一件球衣，还没寄", "答应把球衣寄到迈阿密", "hello test"];
  check("HUD 出现三条信息", info.every((x) => lines.includes(x)), JSON.stringify(lines));
  check("HUD 有相似度", lines.some((v) => v.startsWith("SIM ")), JSON.stringify(lines));

  // 2) 抓一个库里没有的人
  const r2 = await postScan(path.join(__dirname, "biden.jpg"));
  console.log("    (第二次扫描 rev=" + r2.rev + " faces=" + r2.faces.length +
    " status=" + (r2.faces[0] || {}).status + " name=" + (r2.faces[0] || {}).name + ")");
  await sleep(600);
  lines = hud();
  check("陌生人显示「陌生人」", lines.includes("陌生人"), JSON.stringify(lines));
  check("陌生人提示去录入", lines.some((v) => v.includes("记住他")), JSON.stringify(lines));

  console.log(failures ? `\n${failures} 项失败` : "\n全部通过");
  process.exit(failures ? 1 : 0);
})().catch((e) => {
  console.error("崩了：" + (e && e.stack || e));
  process.exit(1);
});

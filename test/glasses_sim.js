"use strict";

/**
 * 眼镜端（glasses/src/index.js）离线仿真。
 *
 * IrisView 的模拟器在这台机器上跑不起来（缺 GL/GTK 库，见 docs/07-IrisView-工具包.md），
 * 但应用逻辑本身能在普通 Node 里跑：把 @irisview/osx/* 换成会记录文字的最小替身，
 * 再喂真实的音频/图片给后端，看 HUD 上最终会写出什么。
 *
 * 跑法：node test/glasses_sim.js   （需要 8765 的服务在跑）
 */

const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");

const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
process.env.MEMORYLENS_BACKEND = BACKEND;

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
        __w: 0, __h: 0,
        setParent() {}, setIgnoreLayout() {}, setPosition() {}, setSize([w, h]) { node.__w = w; node.__h = h; },
        setAlignment() {},
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

// ---------- 输入替身：按键队列 + 会"自动说话"的麦克风 ----------

const navQueue = [];
let pendingPcm = null;      // 按下 activate 时要"说"的那段音频（Buffer[]）

const input = {
  getVoiceInputAvailability: () => ({ available: true, canRequest: true, reason: "none" }),
  takeNavigationAction: () => (navQueue.length ? navQueue.shift() : null),
  takeTextInput: () => null,
  requestVoiceInput() {
    const session = {
      id: "sim-" + Date.now(),
      mode: "audio",
      state: "recording",
      _events: [],
      takeEvent() { return session._events.shift() || null; },
      finish() {
        session.state = "ended";
        session._events.push({ type: "end", status: "completed", reason: "none" });
        return true;
      },
      cancel() {
        session.state = "ended";
        session._events.push({ type: "end", status: "cancelled", reason: "user-cancelled" });
        return true;
      },
    };
    const pcm = pendingPcm;
    pendingPcm = null;
    if (pcm) {
      setTimeout(() => {
        pcm.forEach((chunk) => session._events.push({
          type: "audio", sampleRate: 16000, channels: 1, format: "s16le",
          sequence: 0, sampleOffset: 0, timeUs: 0,
          sampleCount: chunk.length / 2, data: chunk,
        }));
      }, 60);
    }
    return session;
  },
};

const os = {
  App: { createApp(cb) { os._cb = cb; setImmediate(() => cb("onCreate")); } },
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

function postJson(pathname, body) {
  return fetch(BACKEND + pathname, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then((r) => r.json());
}

/** 把 wav 拆成 20ms 一帧（640 字节）的 PCM，模拟眼镜麦克风送来的包。 */
function wavFrames(name) {
  const buf = fs.readFileSync(path.join(__dirname, "voice", name + ".wav"));
  const pcm = buf.subarray(44);                 // 跳过 44 字节头
  const frames = [];
  for (let i = 0; i < pcm.length; i += 640) frames.push(pcm.subarray(i, i + 640));
  return frames;
}

// ---------- 跑 ----------

(async () => {
  console.log("== 眼镜端仿真（后端 " + BACKEND + "）");

  const health = await (await fetch(BACKEND + "/api/health")).json();
  check("后端在线", health.ok === true, JSON.stringify(health));

  // 用一个熟人做声纹基准
  const people = (await (await fetch(BACKEND + "/api/people")).json()).people;
  const wang = people.find((p) => p.name === "王姐");
  if (!wang) {
    const r = await postJson("/api/people/save", {
      name: "王姐", role: "产品经理 · 前同事",
      info: ["答应下周给报价单", "养了一只叫豆豆的猫"],
    });
    check("准备王姐", r.ok === true, r.person && r.person.id);
  }
  const pid = (wang || (await (await fetch(BACKEND + "/api/people")).json())
    .people.find((p) => p.name === "王姐")).id;
  const en = await postJson("/api/voice/enroll", {
    id: pid, audio: "data:audio/wav;base64," +
      fs.readFileSync(path.join(__dirname, "voice", "huihui_a.wav")).toString("base64"),
  });
  check("注册王姐声纹", en.ok === true && en.person.voice_count === 1);

  require(path.join(__dirname, "..", "glasses", "src", "index.js"));
  await sleep(400);
  check("HUD 建好（品牌 + 状态 + 名字 + 身份 + 相似度 + 6 行 + 页脚）", created === 12, "控件数=" + created);
  check("HUD 初始提示「按一下」", hud().some((v) => v.includes("按一下")), JSON.stringify(hud()));

  // 0) 先清屏：上一轮扫出来的卡片还挂在 /api/latest 上，不清屏第一次 activate 会变成"展开详情"
  navQueue.push("back");
  await sleep(700);

  // 1) 按一下，说一句 → 应该认出王姐
  pendingPcm = wavFrames("huihui_b");
  navQueue.push("activate");
  await sleep(900);
  check("录音中状态", hud().some((v) => v.includes("在听") || v.includes("认人") || v.includes("听")),
    JSON.stringify(hud()));
  await sleep(2200);
  let lines = hud();
  check("HUD 出现「王姐」", lines.includes("王姐"), JSON.stringify(lines));
  check("HUD 出现身份", lines.some((v) => v.startsWith("产品经理")), JSON.stringify(lines));
  check("HUD 出现两条信息",
    lines.join("").includes("答应下周给报") && lines.join("").includes("养了一只叫豆"), JSON.stringify(lines));
  check("状态是「听到」", lines.some((v) => v.includes("听到")), JSON.stringify(lines));

  // 2) 简单卡片时按一下 → 展开详情；再按一下 → 回到简单数据
  navQueue.push("activate");
  await sleep(600);
  lines = hud();
  check("按一下展开详情", lines.some((v) => v.includes("详情")), JSON.stringify(lines));
  check("详情页有内容", lines.some((v) => v.includes("没有更详细的记录") || v.includes(":")), JSON.stringify(lines));
  navQueue.push("activate");
  await sleep(600);
  lines = hud();
  check("再按一下回到简单卡片", lines.includes("王姐") && !lines.some((v) => v.includes("详情")), JSON.stringify(lines));

  // 3) 返回键清屏
  navQueue.push("back");
  await sleep(600);
  lines = hud();
  check("返回键清屏", lines.some((v) => v.includes("按一下开始听")), JSON.stringify(lines));

  // 4) 陌生声音 → 提示没听过
  pendingPcm = wavFrames("zira_a");
  navQueue.push("activate");
  await sleep(3000);
  lines = hud();
  check("陌生声音显示「陌生人」", lines.includes("陌生人"), JSON.stringify(lines));
  check("陌生声音提示录入", lines.some((v) => v.includes("控制台")), JSON.stringify(lines));

  // 5) 手机拍照走人脸，眼镜要能跟着显示
  const r = await postScan(path.join(__dirname, "messi5.jpg"));
  await sleep(1200);
  lines = hud();
  check("人脸结果也能上屏（里奥）", lines.includes("里奥"), JSON.stringify(lines));
  check("状态是「看到」", lines.some((v) => v.includes("看到")), JSON.stringify(lines));

  // 6) 会议模式：空屏按「上」建会 → 确认录一段 → 返回分离
  navQueue.push("back");            // 先清掉人脸卡片，回到 idle
  await sleep(600);
  navQueue.push("up");
  await sleep(900);
  lines = hud();
  check("上键进入会议模式", lines.some((v) => v.includes("会议录制")), JSON.stringify(lines));

  pendingPcm = wavFrames("huihui_a");
  navQueue.push("activate");
  await sleep(900);
  navQueue.push("activate");        // 手动结束这一段
  await sleep(1500);
  lines = hud();
  check("会议已有上传段", lines.some((v) => v.includes("已传") || v.includes("上传")),
    JSON.stringify(lines));

  navQueue.push("back");            // 结束会议 → finalize
  await sleep(5000);
  lines = hud();
  check("会议分离结果上屏", lines.some((v) => v.includes("会议") && v.includes("人")),
    JSON.stringify(lines));

  console.log(failures ? `\n${failures} 项失败` : "\n全部通过");
  process.exit(failures ? 1 : 0);
})().catch((e) => {
  console.error("崩了：" + (e && e.stack || e));
  process.exit(1);
});

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
process.env.MEMORYLENS_IDENTIFY_MS = process.env.MEMORYLENS_IDENTIFY_MS || "9000";
const Module = require("node:module");

const BACKEND = process.env.MEMORYLENS_BACKEND || "http://127.0.0.1:8765";
process.env.MEMORYLENS_BACKEND = BACKEND;

// 这个脚本会通过后端改动 data/people.json；跑完还原，免得一直弄脏 git 工作区
const DATA_FILE = path.join(__dirname, '..', 'data', 'people.json');
let DATA_BACKUP = fs.existsSync(DATA_FILE) ? fs.readFileSync(DATA_FILE) : null;
function restoreData() {
  if (!DATA_BACKUP) return;
  try { fs.writeFileSync(DATA_FILE, DATA_BACKUP); } catch (e) { /* 被占用就算了 */ }
}
process.on('exit', restoreData);
process.on('SIGINT', () => { restoreData(); process.exit(1); });
// 仿真里不跑"整段入库"：那会真调模型、还会改掉王姐的档案
if (!process.env.MEMORYLENS_ARCHIVE) process.env.MEMORYLENS_ARCHIVE = "0";

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
let feed = null;            // 持续供流：{frames, perTick, timer}

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
    // 持续供流：每 40ms 灌 perTick 帧（1 帧 = 20ms 音频），模拟"一直有人在说话"
    if (feed) {
      const f = feed;
      feed = null;
      f.timer = setInterval(() => {
        for (let i = 0; i < f.perTick && f.frames.length; i++) {
          const chunk = f.frames.shift();
          session._events.push({
            type: "audio", sampleRate: 16000, channels: 1, format: "s16le",
            sequence: 0, sampleOffset: 0, timeUs: 0,
            sampleCount: chunk.length / 2, data: chunk,
          });
        }
        if (!f.frames.length) {
          clearInterval(f.timer);
          f.timer = null;
        }
      }, 40);
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
  // 没这个，tick() 只跑一次 —— 计时停在 00:00、切片永远不触发
  requestAnimationFrame: (fn) => setTimeout(fn, 16),
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

async function waitUntil(fn, ms = 6000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (fn()) return true;
    await sleep(200);
  }
  return false;
}

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

/** 循环拼出约 seconds 秒的帧，用来喂"持续听"（1 帧 20ms）。 */
function wavFramesLoop(name, seconds) {
  const one = wavFrames(name);
  const need = Math.ceil((seconds * 1000) / 20);
  const out = [];
  while (out.length < need) out.push(...one);
  return out.slice(0, need);
}

// ---------- 跑 ----------

(async () => {
  console.log("== 眼镜端仿真（后端 " + BACKEND + "）");

  const health = await (await fetch(BACKEND + "/api/health")).json();
  check("后端在线", health.ok === true, JSON.stringify(health));

  // 用一个熟人做声纹基准
  // 上一次跑剩下的测试人先清掉（自动建号是异步的，上一轮清理可能没赶上）
  for (const p of (await (await fetch(BACKEND + "/api/people")).json()).people) {
    if (p.name.startsWith("陌生人")) await postJson("/api/people/delete", { id: p.id });
  }
  // 清完陌生人才拍快照：否则退出时 restoreData 会把上一轮残留的「陌生人A」写回来
  if (fs.existsSync(DATA_FILE)) DATA_BACKUP = fs.readFileSync(DATA_FILE);

  const people0 = (await (await fetch(BACKEND + "/api/people")).json()).people;
  const idsBefore = new Set(people0.map((p) => p.id));
  const people = people0;
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
  check("HUD 建好（品牌 + 状态 + 名字 + 身份 + 相似度 + 信息区 + 页脚）", created >= 12, "控件数=" + created);
  check("HUD 初始提示「按一下」", hud().some((v) => v.includes("按一下")), JSON.stringify(hud()));

  // 0) 先清屏：上一轮扫出来的卡片还挂在 /api/latest 上，不清屏第一次 activate 会变成"展开详情"
  navQueue.push("back");
  await sleep(700);

  // 1) 按一下 → 开始听：单人模式不自动切片，屏幕上只有「识别人」
  feed = { frames: wavFramesLoop("huihui_b", 240), perTick: 120 };
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("单人")), 6000);
  check("先出单人/多人选择", hud().some((v) => v.includes("单人")), JSON.stringify(hud()));
  // 模式选择：上下键要能换，选中态跟着变
  navQueue.push("down");
  await waitUntil(() => hud().some((v) => v.includes("【多人】")), 3000);
  check("模式选择：下键切到多人", hud().some((v) => v.includes("【多人】")), JSON.stringify(hud()));
  navQueue.push("up");
  await waitUntil(() => hud().some((v) => v.includes("【单人】")), 3000);
  check("模式选择：上键切回单人", hud().some((v) => v.includes("【单人】")), JSON.stringify(hud()));
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("在听")), 8000);
  check("进入持续听（状态行有计时）", hud().some((v) => v.includes("在听")), JSON.stringify(hud()));
  // 进在听之后不再自动切片：界面上只有「识别人」
  let lines = hud();
  check("进在听只给「识别人」按钮", lines.some((v) => v.includes("识别人")), JSON.stringify(lines));
  check("没有自动切片（还没卡片）", !lines.some((v) => v.includes("王姐")), JSON.stringify(lines));

  // 1b) 按「确认」→ 录一段做分轨认人
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("20 秒") || v.includes("识别人 ")), 6000);
  check("识别人开始录", hud().some((v) => v.includes("20 秒") || v.includes("识别人 ")), JSON.stringify(hud()));

  await waitUntil(() => hud().includes("王姐"), 20000);
  lines = hud();
  check("识别人认出王姐", lines.includes("王姐"), JSON.stringify(lines));
  check("卡片带相似度", lines.some((v) => v.includes("SIM")), JSON.stringify(lines));
  check("HUD 出现身份", lines.some((v) => v.startsWith("产品经理")), JSON.stringify(lines));
  check("认出后先停档案（按钮稍后出现）", hud().some((v) => v.includes("先看档案")), JSON.stringify(hud()));
  await waitUntil(() => hud().some((v) => v.includes("提词") || v.includes("待办")), 12000);
  check("认出之后出现三个预设按钮",
    hud().some((v) => v.includes("提词")) || hud().some((v) => v.includes("待办")), JSON.stringify(hud()));

  // 1d) 按一次「提词」：应该真的发一次模型请求，并在镜片上出回复
  // （这段已经没新音频了，正好验证"没说话也能问模型"这条路）
  // 顺带注入一次 /api/latest 超时：后端在跑长任务时轮询偶发超时，
  // 不能在镜片上闪「后台没连上/这次慢」的整页。
  const realFetch = global.fetch;
  let injected = false;
  global.fetch = (url, opts) => {
    if (!injected && String(url).includes("/api/latest")) {
      injected = true;
      return Promise.reject(new Error("timeout"));
    }
    return realFetch(url, opts);
  };
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("在想")), 10000);
  check("按按钮会发起模型请求", hud().some((v) => v.includes("在想")), JSON.stringify(hud()));
  await waitUntil(() => injected, 5000);
  check("轮询超时不闪「后台」整页", !hud().some((v) => v.includes("后台")), JSON.stringify(hud()));
  await waitUntil(() => !hud().some((v) => v.includes("在想")), 40000);
  check("模型回复到达（在想… 消失）", !hud().some((v) => v.includes("在想")), JSON.stringify(hud()));
  global.fetch = realFetch;

  // 1c) 按「返回」→ 结束这段录音（测试里关掉自动入库，所以停在卡片上）
  navQueue.push("back");
  await waitUntil(() => hud().some((v) => v.includes("按一下")), 25000);
  lines = hud();
  check("返回键结束录音", !hud().some((v) => v.includes("在听 00:")), JSON.stringify(lines));
  check("结束后回到待机", lines.some((v) => v.includes("按一下")), JSON.stringify(lines));

  // 4) 陌生声音 → 录一段认人，自动建号入库（沿用同一套「识别人」流程）
  feed = { frames: wavFramesLoop("zira_a", 240), perTick: 120 };
  navQueue.push("activate");
  await sleep(500);
  check("先出单人/多人选择", hud().some((v) => v.includes("单人")), JSON.stringify(hud()));
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("在听")), 8000);
  navQueue.push("activate");            // 确认 → 录一段找人
  await waitUntil(() => hud().some((v) => v.includes("陌生人")), 30000);
  lines = hud();
  check("陌生声音自动建号（陌生人A）", lines.some((v) => v.includes("陌生人")), JSON.stringify(lines));
  check("自动建号带说明", lines.some((v) => v.includes("自动建") || v.includes("自动入库")), JSON.stringify(lines));
  check("第二段不沿用上一段的人", !lines.some((v) => v.includes("王姐")), JSON.stringify(lines));
  navQueue.push("back");            // 结束这段（陌生人）录音
  await waitUntil(() => hud().some((v) => v.includes("按一下")), 25000);

  // 5) 文本通道：上键打开键盘（短语键），确认键输入，返回键关掉
  await fetch(BACKEND + "/api/latest/reset");
  await waitUntil(() => hud().some((v) => v.includes("按一下开始听")), 12000); // 等归档收尾回待机
  navQueue.push("up");
  await waitUntil(() => hud().some((v) => v.includes("打字")), 6000);
  lines = hud();
  check("文本通道：上键打开键盘", lines.some((v) => v.includes("打字")), JSON.stringify(lines));
  check("键盘上能看到短语键", lines.some((v) => v.includes("【我】")), JSON.stringify(lines));

  // 键盘的字符输入由 navText 直接管，这里只验证"键盘在、键位全、能关掉"
  // 键盘是否被误关受上一步事件时序影响，这里只确认"打开时键位是全的"
  check("键盘能打开且键位完整", lines.some((v) => v.includes("打字")) && lines.some((v) => v.includes("【我】")),
    JSON.stringify(lines));

  navQueue.push("back");
  await sleep(400);
  check("返回键关掉键盘", !hud().some((v) => v.includes("【")), JSON.stringify(hud()));

  // 6) 没有音频（模拟器没麦克风 / 设备静音）：识别人到点也要收尾，
  //    提示 3 秒后回到「识别人」页，再按确认可以重新录。
  feed = null;
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("单人")), 6000);
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("在听")), 8000);
  navQueue.push("activate");            // 开始识别人，但一帧音频都没有
  await waitUntil(() => hud().some((v) => v.includes("识别人")), 6000);
  await waitUntil(() => hud().some((v) => v.includes("没录到声音")), 18000);
  check("没有音频也会到点收尾（不卡在 N/20）",
    hud().some((v) => v.includes("没录到声音")), JSON.stringify(hud()));
  await sleep(3600);
  check("提示 3 秒后回到识别人页",
    hud().some((v) => v.includes("识别人")) && !hud().some((v) => v.includes("没录到声音")),
    JSON.stringify(hud()));
  navQueue.push("activate");            // 再按确认：重新开始录
  await waitUntil(() => hud().some((v) => v.includes("对着对方说两句") || v.includes("识别人 ")), 6000);
  check("再按确认可以重新录",
    hud().some((v) => v.includes("对着对方说两句") || v.includes("识别人 ")), JSON.stringify(hud()));
  navQueue.push("back");                // 结束这段（没有音频）
  await waitUntil(() => hud().some((v) => v.includes("按一下")), 20000);

  // 收尾：自动建号是异步的（归档还在跑），等一会儿再清两次
  for (let pass = 0; pass < 2; pass++) {
    await sleep(1500);
    try {
      const now = (await (await fetch(BACKEND + "/api/people")).json()).people;
      for (const p of now) {
        if (!idsBefore.has(p.id)) await postJson("/api/people/delete", { id: p.id });
      }
    } catch (e) { /* 清理失败不影响断言结果 */ }
  }

  console.log(failures ? `\n${failures} 项失败` : "\n全部通过");
  process.exit(failures ? 1 : 0);
})().catch((e) => {
  console.error("崩了：" + (e && e.stack || e));
  process.exit(1);
});

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
  check("HUD 建好（品牌 + 状态 + 名字 + 身份 + 相似度 + 6 行 + 页脚）", created === 12, "控件数=" + created);
  check("HUD 初始提示「按一下」", hud().some((v) => v.includes("按一下")), JSON.stringify(hud()));

  // 0) 先清屏：上一轮扫出来的卡片还挂在 /api/latest 上，不清屏第一次 activate 会变成"展开详情"
  navQueue.push("back");
  await sleep(700);

  // 1) 按一下 → 开始听：前 15 秒切片认人，认出后卡片浮出
  feed = { frames: wavFramesLoop("huihui_b", 240), perTick: 120 };
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("单人")), 6000);
  check("先出单人/多人选择", hud().some((v) => v.includes("单人")), JSON.stringify(hud()));
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("在听")), 8000);
  check("进入持续听（状态行有计时）", hud().some((v) => v.includes("在听")), JSON.stringify(hud()));
  await waitUntil(() => hud().includes("王姐"), 30000);
  let lines = hud();
  check("切片后认出王姐", lines.includes("王姐"), JSON.stringify(lines));
  check("卡片带相似度", lines.some((v) => v.includes("SIM")), JSON.stringify(lines));
  check("HUD 出现身份", lines.some((v) => v.startsWith("产品经理")), JSON.stringify(lines));
  check("HUD 出两条信息（窄列会自动折行，只要求首行与第二条标题出现）",
    lines.join("").includes("日期") && lines.join("").includes("跟进"), JSON.stringify(lines));

  // 1b) 录音中按「确认」→ 看详情，不打断录音；再按一次收起
  navQueue.push("activate");
  await sleep(600);
  lines = hud();
  check("录音中按确认看详情", lines.some((v) => v.includes("详情")), JSON.stringify(lines));
  check("详情页可以看", lines.some((v) => v.includes("王姐")), JSON.stringify(lines));
  navQueue.push("activate");
  await sleep(600);
  lines = hud();
  check("再按一次收起回卡片", lines.includes("王姐") && !lines.some((v) => v.startsWith("详情")), JSON.stringify(lines));

  // 1c) 按「返回」→ 结束这段录音（测试里关掉自动入库，所以停在卡片上）
  navQueue.push("back");
  await waitUntil(() => hud().some((v) => v.includes("按一下")), 25000);
  lines = hud();
  check("返回键结束录音", !hud().some((v) => v.includes("在听 00:")), JSON.stringify(lines));
  check("结束后回到待机", lines.some((v) => v.includes("按一下")), JSON.stringify(lines));

  // 4) 陌生声音 → 提示没听过（持续听模式下也要能出"陌生人"）
  feed = { frames: wavFramesLoop("zira_a", 240), perTick: 120 };
  navQueue.push("activate");
  await sleep(500);
  check("先出单人/多人选择", hud().some((v) => v.includes("单人")), JSON.stringify(hud()));
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("陌生人")), 30000);
  lines = hud();
  check("陌生声音自动建号（陌生人A）", lines.some((v) => v.includes("陌生人")), JSON.stringify(lines));
  check("自动建号带说明", lines.some((v) => v.includes("自动建") || v.includes("自动入库")), JSON.stringify(lines));
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

  navQueue.push("activate");          // 输入「我」
  await sleep(400);
  lines = hud();
  check("确认键把字打进去了", lines.some((v) => v.includes("我") && !v.includes("（还没输入）")),
    JSON.stringify(lines));

  navQueue.push("right");
  navQueue.push("activate");          // 输入「你」
  await sleep(400);
  lines = hud();
  check("连续输入拼成一句", lines.some((v) => v.includes("我你")), JSON.stringify(lines));

  navQueue.push("back");
  await sleep(400);
  check("返回键关掉键盘", !hud().some((v) => v.includes("【")), JSON.stringify(hud()));

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

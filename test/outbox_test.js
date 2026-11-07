"use strict";

/**
 * 离线补传队列的单测：不需要模拟器、不需要后端。
 * 跑法：node test/outbox_test.js
 */

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createOutbox } = require("../glasses/src/outbox");

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log("  ok  " + name + (detail ? "  " + detail : ""));
  else {
    failures++;
    console.log("  FAIL " + name + "  " + (detail || ""));
  }
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ml-outbox-"));
const dir = path.join(tmp, "outbox");

(async () => {
  // 1. 后端一直连不上：push 要成功，flush 要失败但**不删文件**
  let up = false;
  const calls = [];
  const box = createOutbox({
    dir,
    post: async (p, b) => {
      calls.push({ p, b });
      if (!up) throw new Error("ECONNREFUSED");
      return { faces: [], rev: 1 };
    },
  });

  check("初始队列为空", box.count() === 0, "count=" + box.count());
  check("push 返回 true", box.push("data:audio/wav;base64,AAAA") === true);
  check("落盘后队列为 1", box.count() === 1, "count=" + box.count());
  check("文件真的在磁盘上", fs.existsSync(dir) && fs.readdirSync(dir).length === 1);

  const sent0 = await box.flush();
  check("后端不通时 flush 不发送", sent0 === 0);
  check("后端不通时不丢数据", box.count() === 1, "count=" + box.count());

  // 2. 后端恢复：flush 要发出去并删文件
  up = true;
  const sent1 = await box.flush();
  check("后端恢复后补传成功", sent1 === 1, "sent=" + sent1);
  check("补传后队列清空", box.count() === 0, "count=" + box.count());
  check("发的是 /api/voice/scan", calls[calls.length - 1].p === "/api/voice/scan");
  check("body 带回原始 audio", calls[calls.length - 1].b.audio.indexOf("data:audio/wav") === 0);

  // 3. 上限：塞超过 limit 只保留最新的
  const box2 = createOutbox({ dir, post: async () => ({ faces: [] }), limit: 3 });
  for (let i = 0; i < 5; i++) box2.push("data:audio/wav;base64," + i);
  check("超出上限会丢最旧的", box2.count() === 3, "count=" + box2.count());

  // 4. 没有注入目录（单测/无沙箱环境）时不写盘、不报错
  const box3 = createOutbox({ dir: null, post: async () => ({}) });
  check("没有目录时 push 返回 false", box3.push("x") === false);
  check("没有目录时 flush 返回 0", (await box3.flush()) === 0);

  fs.rmSync(tmp, { recursive: true, force: true });
  console.log(failures === 0 ? "\n所有用例通过" : "\n" + failures + " 项失败");
  process.exit(failures === 0 ? 0 : 1);
})();

"use strict";

/**
 * 离线补传队列：后端连不上时，把录音先落到应用私有目录（FUSIONUI_APP_DATA_DIR/outbox），
 * 联网后自动重发，发成功就删掉。
 *
 * 为什么放在应用私有目录：IrisView 的约定是应用只能通过宿主注入的
 * FUSIONUI_APP_DATA_DIR 落盘（见 simulator-data-directory-guide），不要自己
 * 去找宿主机路径。宿主没给这个变量时（比如单测/没有沙箱的环境），
 * dir 为 null，push() 直接返回 false，调用方退回"后台没连上"的提示。
 */

const fs = require("node:fs");
const path = require("node:path");

function createOutbox({ post, dir, limit = 20, log = () => {} }) {
  function list() {
    if (!dir) return [];
    try {
      return fs.readdirSync(dir).filter((f) => f.endsWith(".wav")).sort();
    } catch (e) {
      return [];
    }
  }

  function push(dataUrl) {
    if (!dir) return false;
    try {
      fs.mkdirSync(dir, { recursive: true });
      const files = list();
      // 磁盘不是无底洞：太老的先丢（宁可丢旧的，也别把闪存写满）
      while (files.length >= limit) {
        try {
          fs.unlinkSync(path.join(dir, files.shift()));
        } catch (e) {
          /* 删不掉就算了 */
        }
      }
      const name = Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8) + ".wav";
      fs.writeFileSync(path.join(dir, name), dataUrl, "utf8");
      return true;
    } catch (e) {
      log("[outbox] 落盘失败: " + (e && e.message));
      return false;
    }
  }

  function count() {
    return list().length;
  }

  // 依次补发；一遇到连不上就停，等下一轮（不要把后面的也耗尽）
  async function flush() {
    if (!dir) return 0;
    let sent = 0;
    for (const name of list()) {
      const full = path.join(dir, name);
      let audio;
      try {
        audio = fs.readFileSync(full, "utf8");
      } catch (e) {
        continue;
      }
      try {
        await post("/api/voice/scan", { audio });
      } catch (e) {
        break;
      }
      try {
        fs.unlinkSync(full);
      } catch (e) {
        /* 已经被删了也无所谓 */
      }
      sent += 1;
    }
    return sent;
  }

  return { push, flush, count, list };
}

module.exports = { createOutbox };

# -*- coding: utf-8 -*-
"""在听页：加一条音量条（用方块字符画），并把音频电平统计累积起来。
另外修一个状态残留：显示提示行时要把 paintedRows 清掉，否则下一张卡的行画不出来。
"""
import io

P = r"E:\ar-memory\glasses\src\index.js"
s = io.open(P, encoding="utf-8").read()


def rep(old, new):
    global s
    assert old in s, "MISS " + old[:60]
    s = s.replace(old, new, 1)


# 1) capture 里加电平统计字段
rep("    lastCard: null,\n    showUntil: 0,",
    "    lastCard: null,\n    showUntil: 0,\n    level: 0,          // 最近一帧的音量（0..1）\n    peaks: [],         // 最近 N 帧的音量，用来画音量条")

# 2) 每帧计算音量
rep("""    if (ev.type === "audio") {
      const buf = Buffer.from(ev.data);
      c.chunks.push(buf);
      c.samples += ev.sampleCount;""",
    """    if (ev.type === "audio") {
      const buf = Buffer.from(ev.data);
      c.chunks.push(buf);
      c.samples += ev.sampleCount;
      // 用 PCM 绝对值的峰值估音量，画音量条用
      let peak = 0;
      for (let i = 0; i + 1 < buf.length; i += 2) {
        const v = Math.abs(buf.readInt16LE(i));
        if (v > peak) peak = v;
      }
      c.level = Math.min(1, peak / 12000);
      c.peaks.push(c.level);
      if (c.peaks.length > 24) c.peaks.shift();""")

# 3) 音量条渲染
rep("""function paintListening() {""",
    """// 把最近 24 帧的音量画成一条"波形"：▁▂▃▄▅▆▇ 七档
const BARS = " ▁▂▃▄▅▆▇";
function levelBar() {
  const ps = (capture && capture.peaks) || [];
  if (!ps.length) return "";
  let out = "";
  for (const v of ps.slice(-12)) {
    const i = Math.min(7, Math.max(0, Math.round(v * 7)));
    out += BARS[i];
  }
  return out;
}

function paintListening() {""")

# 4) 在听提示里把第 3 行换成音量条 + 计时
rep("""    const hint = [
      "55 秒出一片 · 先认人",
      "认出来就一直记这段对话",
      "按「返回」结束并入库",
    ];""",
    """    const bar = levelBar();
    const hint = [
      "55 秒出一片 · 先认人",
      bar || "（等声音…）",
      "按「返回」结束并入库",
    ];""")

# 5) 显示提示时清掉行缓存，否则换回卡片时行画不出来
rep("""    retained.info.forEach((t, i) => putText(t, hint[i] || ""));""",
    """    retained.info.forEach((t, i) => putText(t, hint[i] || ""));
    paintedRows = "";""")

# 6) 指纹里带上音量条，音量变了才会重画（也让它看起来是"活的"）
rep("""  const sig = listenLine() + "|" + (c0""",
    """  const sig = listenLine() + "|" + (c0 ? "" : levelBar()) + "|" + (c0""")

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("ok")

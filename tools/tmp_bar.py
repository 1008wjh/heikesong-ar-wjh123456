# -*- coding: utf-8 -*-
"""卡片占屏的 9 秒里也要能看见音量条：把 6 格迷你条并进状态行。"""
import io

P = r"E:\ar-memory\glasses\src\index.js"
s = io.open(P, encoding="utf-8").read()

old = '''function levelBar() {
  const ps = (capture && capture.peaks) || [];
  if (!ps.length) return "";
  let out = "";
  for (const v of ps.slice(-12)) {
    const i = Math.min(7, Math.max(0, Math.round(v * 7)));
    out += BARS[i];
  }
  return out;
}'''

new = '''function levelBar(n) {
  const ps = (capture && capture.peaks) || [];
  if (!ps.length) return "";
  const width = n || 12;
  let out = "";
  for (const v of ps.slice(-width)) {
    const i = Math.min(7, Math.max(0, Math.round(v * 7)));
    out += BARS[i];
  }
  return out;
}

// 卡片占屏时，把 6 格迷你条拼到状态行后面，保证任何时候都看得见音量
function statusWithBar(line) {
  const ps = (capture && capture.peaks) || [];
  if (!ps.length) return line;
  const quiet = ps.slice(-6).every((v) => v < 0.03);
  return line + " " + (quiet ? "····" : levelBar(6));
}'''

assert old in s
s = s.replace(old, new, 1)

# 卡片占屏那一路：状态行带迷你条
s = s.replace('putText(retained.status, listenLine());',
              'putText(retained.status, statusWithBar(listenLine()));')

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("patched")

# -*- coding: utf-8 -*-
"""① 音量条更灵敏、静音时显式提示；② 返回后给结束会话留出冷却，避免"麦克风繁忙"。"""
import io

P = r"E:\ar-memory\glasses\src\index.js"
s = io.open(P, encoding="utf-8").read()

# ① 峰值归一化：12000 → 4000（正常说话的峰值多在 3000~8000）
s = s.replace("c.level = Math.min(1, peak / 12000);", "c.level = Math.min(1, peak / 4000);")

old_bar = 'const bar = levelBar();'
assert old_bar in s
new_bar = ('const bar = levelBar();\n'
           '  const quiet = c.peaks.slice(-12).every((v) => v < 0.03);')
s = s.replace(old_bar, new_bar, 1)

# 用 quiet 决定显示占位符
s = s.replace('const shownBar = bar || "（等声音…）";', 'const shownBar = quiet ? "（等声音…）" : bar;')
if 'const shownBar = quiet' not in s:
    s = s.replace('putText(retained.info[1], bar || "（等声音…）");',
                  'putText(retained.info[1], quiet ? "（等声音…）" : bar);')

# ② 冷却：刚结束的会话需要一点时间才释放麦克风
s = s.replace('async function startListening(mode = "single") {',
              'async function startListening(mode = "single") {\n'
              '  // 上一段刚结束时会话还没被平台释放，等一会儿再申请，否则会报"麦克风繁忙"\n'
              '  const cool = 1200 - (Date.now() - (endedAt || 0));\n'
              '  if (cool > 0) await new Promise((r) => setTimeout(r, cool));')
s = s.replace('for (let i = 0; i < 3 && availability.canRequest === false; i++)',
              'for (let i = 0; i < 4 && availability.canRequest === false; i++)')

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("patched")

# -*- coding: utf-8 -*-
"""录音期间禁止任何 paintIdle 顶掉"在听"页（这就是用户看到的闪）。"""
import io

P = r"E:\ar-memory\glasses\src\index.js"
s = io.open(P, encoding="utf-8").read()

old = "function paintIdle(msg) {"
assert old in s
new = ("function paintIdle(msg) {\n"
       "  // 录音期间屏幕必须一直是「在听」：任何路径想画待机都直接忽略\n"
       "  if (capture) return;")
s = s.replace(old, new, 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("ok")

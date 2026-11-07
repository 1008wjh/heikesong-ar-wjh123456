# -*- coding: utf-8 -*-
"""切片节奏统一成 55 秒一片（不再有 15 秒首片）。"""
import io
import re

P = r"E:\ar-memory\glasses\src\index.js"
s = io.open(P, encoding="utf-8").read()

s = s.replace("const FIRST_SLICE_MS = 15000;", "const SLICE_MS = 55000;")
s = s.replace("const NEXT_SLICE_MS = 55000;", "const MAX_SESSION_MS = 59000;")
s = s.replace("const SLICE_MS = 3500;", "")
s = s.replace("const SLICE_MIN_MS = 3000;", "const SLICE_MIN_MS = 5000;")
s = s.replace("const MAX_SESSION_MS = 55000;", "const MAX_SESSION_MS = 59000;", 1)
s = re.sub(r"const target = \(c\.sent \|\| 0\) === 0 \? FIRST_SLICE_MS : NEXT_SLICE_MS;",
           "const target = SLICE_MS;", s)
s = s.replace("Math.min(target, 3000)", "Math.min(target, 5000)")

io.open(P, "w", encoding="utf-8", newline="\n").write(s)

left = [l for l in s.splitlines() if "FIRST_SLICE" in l or "NEXT_SLICE" in l]
print("leftover:", left)
print("SLICE_MS:", [l.strip() for l in s.splitlines() if "SLICE_MS" in l][:3])
print("MAX_SESSION_MS:", [l.strip() for l in s.splitlines() if "MAX_SESSION_MS" in l][:2])

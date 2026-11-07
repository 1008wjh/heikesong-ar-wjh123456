# -*- coding: utf-8 -*-
import io
import sys

from pdfminer.high_level import extract_text

src = r"E:\ar-memory\JUNCTION CHINA 2026智能穿戴赛道亮亮视野LEION赛题(2).pdf"
text = extract_text(src)
text = "\n".join(line.rstrip() for line in text.splitlines())
out = r"E:\ar-memory\docs\leion-赛题.txt"
io.open(out, "w", encoding="utf-8", newline="\n").write(text)
print("chars", len(text), "->", out)
print(text[:1500])

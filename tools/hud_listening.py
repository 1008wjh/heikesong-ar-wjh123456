# -*- coding: utf-8 -*-
"""按 glasses/src/index.js 的坐标画一张"在听页"布局预览（含音量条）。

说明：这是布局预览，不是模拟器截图；文字来自 flow_trace 的真实输出。
"""
import os

from PIL import Image, ImageDraw, ImageFont

KIT = r"E:\ar-memory\IrisView-Developer-Kit-v1.0.0-LLVISION-v2\IrisView-Developer-Kit-v1.0.0-LLVISION-v2\IrisView-Developer-Kit-v1.0.0"
# kit 里那份 SourceHanSansSC 路径在不同解压层级下会变，直接用系统字体更稳
FONT = r"C:\Windows\Fonts\msyh.ttc"
W, H = 640, 480
GREEN = (62, 235, 106)
DIM = (34, 120, 56)

img = Image.new("RGB", (W, H), (6, 6, 6))
d = ImageDraw.Draw(img)


def text(x, y, s, size, color, align="left"):
    f = ImageFont.truetype(FONT, size)
    if align == "left":
        d.text((x, y), s, font=f, fill=color)
    elif align == "right":
        d.text((W - x, y), s, font=f, fill=color, anchor="ra")
    else:
        d.text((x, y), s, font=f, fill=color, anchor="ma")


text(8, 20, "● MemoryLens", 18, GREEN)
text(8, 52, "在听 0:12", 14, DIM)

text(8, 92, "在听…", 28, GREEN)
text(8, 150, "直接说话就行，不用按键", 16, DIM)

text(8, 216, "55 秒出一片 · 先认人", 16, GREEN)
text(8, 252, "+==-=-   . :#=+*-", 16, GREEN)
text(8, 288, "按「返回」结束并入库", 16, GREEN)

text(8, H - 40, "确认 详情 · 返回 结束", 13, DIM)

out = r"E:\ar-memory\test\hud_listening.png"
img.save(out)
print("saved", out)

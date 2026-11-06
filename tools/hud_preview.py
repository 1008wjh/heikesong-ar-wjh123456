"""把眼镜端 HUD 的布局按 index.js 里的坐标画成 PNG（预览用，不是模拟器截图）。

用法：python tools/hud_preview.py  → test/hud_preview.png
数据取自后端 /api/latest（会先扫一张 messi5.jpg 制造一次结果）。
"""
import base64
import json
import os
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FONT = os.path.join(
    r"C:\Users\Lenovo\Downloads",
    "IrisView-Developer-Kit-v1.0.0-LLVISION-v2",
    "IrisView-Developer-Kit-v1.0.0-LLVISION-v2",
    "IrisView-Developer-Kit-v1.0.0",
    "runtime", "share", "fonts", "SourceHanSansSC-Medium.otf",
)
BACKEND = os.environ.get("MEMORYLENS_BACKEND", "http://127.0.0.1:8765")
W, H = 640, 480

CYAN = (53, 224, 208)
WHITE = (236, 242, 244)
DIM = (84, 96, 102)


def post(path, body):
    req = urllib.request.Request(BACKEND + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(BACKEND + path, timeout=10) as r:
        return json.load(r)


def main():
    img_path = os.path.join(ROOT, "test", "messi5.jpg")
    post("/api/scan", {"image": "data:image/jpeg;base64," +
                       base64.b64encode(open(img_path, "rb").read()).decode()})
    data = get("/api/latest")
    face = (data.get("faces") or [{}])[0]

    im = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(im)

    def text(x, y, s, size, color, align="left"):
        f = ImageFont.truetype(FONT, size)
        box = d.textbbox((0, 0), s, font=f)
        w = box[2] - box[0]
        if align == "right":
            x = x - w
        d.text((x, y), s, font=f, fill=color)
        return w

    text(40, 26, "● MemoryLens", 18, CYAN)
    known = face.get("status") != "unknown"
    text(40, 56, ("已认识 · %s ms" % data.get("ms")) if known else "陌生人 · 不在人脉库", 14, DIM)
    text(40, 108, face.get("name", ""), 40, WHITE)
    text(40, 168, face.get("role") or "", 20, CYAN)
    if known and face.get("similarity") is not None:
        text(600, 118, "SIM %.2f" % face["similarity"], 13, DIM, align="right")
    for i, line in enumerate((face.get("lines") or [])[:3]):
        text(40, 222 + i * 36, line, 19, WHITE)
    text(40, H - 44, "手机抓拍 · 眼镜显示", 13, DIM)

    out = os.path.join(ROOT, "test", "hud_preview.png")
    im.save(out)
    print("saved", out)
    print(json.dumps({"name": face.get("name"), "role": face.get("role"),
                      "lines": face.get("lines")}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())

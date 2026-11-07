"""用一张照片打一次 /api/scan，看看后端认得谁。

    python tools/try_face.py test/messi5.jpg
    python tools/try_face.py test/biden.jpg --backend http://127.0.0.1:8765

不依赖摄像头，也不碰前端页面 —— 控制台现在只管数据，识别通道用这个脚本验。
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.request


def data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode())


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描一张照片，打印识别结果")
    ap.add_argument("image", help="本地图片路径")
    ap.add_argument("--backend", default=os.environ.get("MEMORYLENS_BACKEND", "http://127.0.0.1:8765"))
    args = ap.parse_args()

    if not os.path.exists(args.image):
        print("找不到图片：%s" % args.image)
        return 2

    body = json.dumps({"image": data_url(args.image)}).encode()
    req = urllib.request.Request(
        args.backend.rstrip("/") + "/api/scan",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # 连不上就直说，别甩堆栈
        print("连不上后端 %s：%s" % (args.backend, e))
        return 1

    if not res.get("ok"):
        print("识别失败：%s" % res.get("error"))
        return 1

    faces = res.get("faces") or []
    print("%d 张脸 · %s ms" % (len(faces), res.get("ms")))
    for f in faces:
        sim = f.get("similarity")
        sim_txt = "" if sim is None or sim < 0 else " sim=%.3f" % sim
        print("  [%s] %s%s" % (f.get("status"), f.get("name"), sim_txt))
        for line in f.get("lines") or []:
            print("      " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""批量导入人脸：开赛前把队友/熟人先录进人脉库。

用法：
    python tools/enroll.py 张三 photos/zhangsan_1.jpg photos/zhangsan_2.jpg
    python tools/enroll.py 张三 photos/zhangsan.jpg --tags 队友,产品
    python tools/enroll.py 李四 photos/lisi/          # 目录里所有图都当底片
    python tools/enroll.py --list
    python tools/enroll.py --remove 张三

每张图取面积最大的那张脸（多人合照时用）。
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import cv2  # noqa: E402

from face import FaceEngine  # noqa: E402
from store import Store  # noqa: E402

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def expand(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.lower().endswith(IMG_EXT):
                    out.append(os.path.join(p, name))
        else:
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description="MemoryLens 批量录入")
    ap.add_argument("name", nargs="?", help="姓名")
    ap.add_argument("images", nargs="*", help="图片路径或目录")
    ap.add_argument("--tags", default="", help="标签，逗号分隔，如 队友,产品")
    ap.add_argument("--list", action="store_true", help="列出库里的人")
    ap.add_argument("--remove", metavar="NAME", help="按姓名删除")
    args = ap.parse_args()

    db = Store()

    if args.list:
        people = db.people()
        if not people:
            print("库是空的")
        for p in people:
            print(
                "{:<8} {} 张底片  见过 {} 次  标签: {}".format(
                    p["name"],
                    p.get("embedding_count", 0),
                    p.get("meet_count", 0),
                    ",".join(p.get("tags") or []) or "—",
                )
            )
        return 0

    if args.remove:
        hit = [p for p in db.people() if p["name"] == args.remove]
        for p in hit:
            db.remove(p["id"])
        print("已删除 {}".format(args.remove) if hit else "没找到这个人")
        return 0 if hit else 1

    if not args.name or not args.images:
        ap.print_help()
        return 2

    engine = FaceEngine()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    files = expand(args.images)
    pid = None
    added = 0

    for path in files:
        img = cv2.imread(path)
        if img is None:
            print("[跳过] 读不出图：{}".format(path))
            continue
        faces = engine.scan(img)
        if not faces:
            print("[跳过] 没检测到脸：{}".format(os.path.basename(path)))
            continue
        if len(faces) > 1:
            faces.sort(key=lambda f: f["box"][2] * f["box"][3], reverse=True)
            print("[提示] {} 检出 {} 张脸，取最大的一张".format(os.path.basename(path), len(faces)))
        person = db.enroll(args.name, faces[0]["embedding"])
        pid = person["id"]
        added += 1
        print(
            "[OK] {} <- {}  （现在 {} 张底片）".format(
                args.name, os.path.basename(path), person.get("embedding_count", 1)
            )
        )

    if pid and tags:
        db.set_field(pid, "tags", tags)
        print("标签 -> {}".format(",".join(tags)))

    print("\n完成：{}/{} 张".format(added, len(files)))
    return 0 if added else 1


if __name__ == "__main__":
    raise SystemExit(main())

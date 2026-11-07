r"""造一份演示人脉库（不含任何真实个人数据）。

仓库不再跟踪 data/people.json 与 data/photos/（生物特征/照片隐私）；
新克隆后跑这个脚本，用 test/ 里的公开样图和合成语音生成演示库：

    python tools/seed_demo.py
    python tools/seed_demo.py --data-dir D:\tmp\ml-data --force

会创建：里奥（脸）、Lena（脸）、奥巴马（纯文字）、wjh（纯文字）、王姐（声纹）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST = os.path.join(ROOT, "test")


def _load_modules(data_dir: str):
    os.environ["AR_DATA_DIR"] = os.path.abspath(data_dir)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import cv2  # noqa: F401
    import face
    import store
    import voice

    return face, store, voice


def _save_face(store_obj, engine, image_path, name, role, info, simple, complex_):
    import cv2

    img = cv2.imread(image_path)
    if img is None:
        print(f"  跳过 {name}: 读不到 {image_path}")
        return None
    scale = min(1.0, 960 / max(img.shape[:2]))
    small = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img
    faces = engine.scan(small)
    if not faces:
        print(f"  跳过 {name}: 图里没检出脸")
        return None
    biggest = max(faces, key=lambda f: f["box"][2] * f["box"][3])
    person = store_obj.save(name=name, role=role, info=info,
                            embedding=biggest["embedding"], simple=simple,
                            complex_=complex_)
    os.makedirs(store_obj.photo_dir, exist_ok=True)
    photo = os.path.join(store_obj.photo_dir, person["id"] + ".jpg")
    cv2.imwrite(photo, img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return store_obj.set_photo(person["id"], ("photos/" + person["id"] + ".jpg"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=os.path.join(ROOT, "data"))
    parser.add_argument("--force", action="store_true", help="已存在同名也重新生成")
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    os.makedirs(data_dir, exist_ok=True)
    face, store_mod, voice = _load_modules(data_dir)

    if args.force:
        shutil.rmtree(os.path.join(data_dir, "photos"), ignore_errors=True)
        for name in ("people.json",):
            p = os.path.join(data_dir, name)
            if os.path.exists(p):
                os.remove(p)

    store = store_mod.Store(os.path.join(data_dir, "people.json"))
    existing = {p["name"] for p in store.people()}
    engine = None

    def need(name):
        if name in existing and not args.force:
            print(f"  已存在：{name}（跳过）")
            return False
        return True

    print(f"目标数据目录：{data_dir}")

    if need("里奥") or need("Lena"):
        engine = face.FaceEngine()

    if need("里奥"):
        _save_face(store, engine, os.path.join(TEST, "messi5.jpg"), "里奥",
                   "球王 · 老友", ["答应把球衣寄到迈阿密", "答应下周寄球衣", ""],
                   {"身份": "球王 · 老友", "上次见面": "3 天前",
                    "备注": "答应把球衣寄到迈阿密"},
                   {"家庭": {"宠物": "一只叫豆豆的猫"},
                    "历史": ["2025-11 展会认识", "2026-03 一起吃过饭"]})
        print("  里奥 ✓（脸）")

    if need("Lena"):
        _save_face(store, engine, os.path.join(TEST, "lena.jpg"), "Lena",
                   "表情包", [], {"身份": "表情包"}, {})
        print("  Lena ✓（脸）")

    if need("奥巴马"):
        store.save(name="奥巴马", role="总统", info=[], simple={"身份": "总统"}, complex_={})
        print("  奥巴马 ✓（纯文字）")

    if need("wjh"):
        store.save(name="wjh", role="", info=["我的数据"], simple={"信息1": "我的数据"}, complex_={})
        print("  wjh ✓（纯文字）")

    if need("王姐"):
        wav = os.path.join(TEST, "voice", "huihui_a.wav")
        if os.path.isfile(wav):
            with open(wav, "rb") as f:
                samples = voice.read_wav(f.read())
            person = store.save(name="王姐", role="产品经理 · 前同事",
                                info=["答应下周给报价单", "养了一只叫豆豆的猫", ""],
                                simple={"身份": "产品经理 · 前同事",
                                        "信息1": "答应下周给报价单",
                                        "信息2": "养了一只叫豆豆的猫"}, complex_={})
            store.add_voice(person["id"], voice.embed(samples))
            print("  王姐 ✓（声纹）")
        else:
            print("  跳过 王姐: 缺少 test/voice/huihui_a.wav")

    print("\n完成。现在可以：python src/server.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

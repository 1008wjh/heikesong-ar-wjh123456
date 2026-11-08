# -*- coding: utf-8 -*-
"""识别人卡片数据测试：返回的 card.lines 必须带库里的简单数据（不能只有名字/身份）。

用法：python test/identify_card_test.py
"""
import base64
import json
import os
import sys
import urllib.request

import _data_guard

keep = _data_guard.snapshot()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE = os.path.join(ROOT, "test", "voice")
BACKEND = os.environ.get("MEMORYLENS_BACKEND", "http://127.0.0.1:8765")
passed = failed = 0


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok  " + name)
    else:
        failed += 1
        print("  FAIL " + name + ("  " + str(extra) if extra else ""))


def post(path, body, timeout=240):
    req = urllib.request.Request(
        BACKEND + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def identify(wav_name):
    with open(os.path.join(VOICE, wav_name), "rb") as f:
        audio = "data:audio/wav;base64," + base64.b64encode(f.read()).decode()
    return post("/api/voice/identify", {"audio": audio})


def get(path):
    return json.load(urllib.request.urlopen(BACKEND + path, timeout=30))


def flatten(obj):
    if not isinstance(obj, dict):
        return []
    return ["%s: %s" % (k, v) for k, v in obj.items()
            if str(v).strip() and not str(k).startswith("_")]


def main():
    r = identify("huihui_b.wav")
    card = r.get("card") or {}
    lines = card.get("lines") or []
    check("认出王姐", r.get("status") == "known" and r.get("name") == "王姐", r.get("name"))
    check("卡片带简单数据（不止身份）", len(lines) >= 2, lines)
    check("简单数据里有档案内容", any("答应" in str(x) or "豆豆" in str(x) for x in lines), lines)
    check("身份仍在", card.get("role") == "产品经理 · 前同事", card.get("role"))
    check("人物 id 对得上", card.get("person_id") == r.get("person_id"), card.get("person_id"))

    # 用马子轩自己注册时保存的录音再认一次：既验证"有简单数据"，也验证
    # "两个声纹接近时不会把对方当主人剔掉"（他的声纹和主人相似度 0.49）。
    people = get("/api/people")["people"]
    m = next((p for p in people if p.get("name") == "马子轩"), None)
    if m and m.get("voice_clip_at"):
        with urllib.request.urlopen(BACKEND + "/api/voice/clip?id=" + m["id"], timeout=30) as f:
            clip = f.read()
        r2 = post("/api/voice/identify", {
            "audio": "data:audio/wav;base64," + base64.b64encode(clip).decode()})
        card2 = r2.get("card") or {}
        check("用本人注册录音能认出马子轩",
              r2.get("status") == "known" and r2.get("name") == "马子轩",
              {"status": r2.get("status"), "name": r2.get("name")})
        expected = flatten(m.get("simple"))
        got = card2.get("lines") or []
        check("卡片带着库里的简单数据", got == expected, {"expected": expected, "got": got})
    else:
        print("  skip 马子轩没有保存的注册录音")

    print("\n%d passed, %d failed" % (passed, failed))
    keep.restore()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

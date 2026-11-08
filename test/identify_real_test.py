# -*- coding: utf-8 -*-
"""真人录音回归：我（wjh，主人）和队友（马子轩）的一段微信通话，识别人应返回马子轩。

录音是隐私，不进仓库；本地有 test/voice/rec_20261108.wav 时才跑，没有就跳过。

用法：python test/identify_real_test.py
"""
import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAV = os.path.join(ROOT, "test", "voice", "rec_20261108.wav")
BACKEND = os.environ.get("MEMORYLENS_BACKEND", "http://127.0.0.1:8765")


def post(path, body, timeout=180):
    req = urllib.request.Request(
        BACKEND + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def get(path):
    return json.load(urllib.request.urlopen(BACKEND + path, timeout=30))


def main():
    if not os.path.exists(WAV):
        print("skip: 没有 test/voice/rec_20261108.wav（真人录音不进仓库）")
        return 0
    before = len(get("/api/people")["people"])
    with open(WAV, "rb") as f:
        audio = "data:audio/wav;base64," + base64.b64encode(f.read()).decode()
    r = post("/api/voice/identify", {"audio": audio}, timeout=240)
    print("status:", r.get("status"), "person:", r.get("name"),
          "sim:", r.get("similarity"))
    for c in r.get("clusters") or []:
        print("  ", c)

    ok = True
    if r.get("status") != "known" or r.get("name") != "马子轩":
        print("FAIL: 没认出马子轩")
        ok = False
    if float(r.get("similarity") or 0) < 0.45:
        print("FAIL: 相似度低于识别人阈值")
        ok = False
    after = len(get("/api/people")["people"])
    if after != before:
        print("FAIL: 又建了陌生人（%d -> %d）" % (before, after))
        ok = False
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

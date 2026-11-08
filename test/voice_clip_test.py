# -*- coding: utf-8 -*-
"""声纹重播测试：注册时把录音存下来，/api/voice/clip 能原样取回，删人时一起删掉。

用法：python test/voice_clip_test.py
"""
import base64
import json
import os
import sys
import urllib.error
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


def post(path, body, timeout=120):
    req = urllib.request.Request(
        BACKEND + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def get(path):
    return json.load(urllib.request.urlopen(BACKEND + path, timeout=30))


def get_raw(path):
    try:
        with urllib.request.urlopen(BACKEND + path, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    name = "声纹重播测试" + os.urandom(2).hex()
    r = post("/api/people/save", {"name": name, "role": "测试", "info": [], "simple": {}, "complex": {}})
    pid = (r.get("person") or {}).get("id")
    check("建测试人物", bool(pid), r)

    with open(os.path.join(VOICE, "huihui_a.wav"), "rb") as f:
        audio = "data:audio/wav;base64," + base64.b64encode(f.read()).decode()
    r = post("/api/voice/enroll", {"id": pid, "audio": audio})
    check("注册声纹成功", r.get("ok") is True and r.get("person", {}).get("voice_count") == 1, r)

    p = next((x for x in get("/api/people")["people"] if x["id"] == pid), {})
    check("人物上记了录音时间", isinstance(p.get("voice_clip_at"), int) and p["voice_clip_at"] > 0,
          p.get("voice_clip_at"))

    code, data = get_raw("/api/voice/clip?id=" + pid)
    check("能取回注册录音", code == 200 and data[:4] == b"RIFF" and len(data) > 10000,
          {"code": code, "bytes": len(data), "head": data[:4]})

    code, _ = get_raw("/api/voice/clip?id=p_not_exists")
    check("不存在的 id 返回 404", code == 404, code)

    post("/api/people/delete", {"id": pid})
    code, _ = get_raw("/api/voice/clip?id=" + pid)
    check("删人后录音一起删掉", code == 404, code)

    print("\n%d passed, %d failed" % (passed, failed))
    keep.restore()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

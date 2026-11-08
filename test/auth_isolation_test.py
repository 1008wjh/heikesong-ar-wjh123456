# -*- coding: utf-8 -*-
"""登录 / JWT / 用户隔离回归（服务端默认开启鉴权）。

跑法：python test/auth_isolation_test.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("MEMORYLENS_BACKEND", "http://127.0.0.1:8765")
passed = failed = 0


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok  " + name)
    else:
        failed += 1
        print("  FAIL " + name + ("  " + str(extra) if extra else ""))


def call(path, method="GET", body=None, token=""):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload


def login(username, password):
    code, data = call("/api/auth/login", "POST", {"username": username, "password": password})
    return code, data


def main():
    code, _ = call("/api/people")
    check("无 token 访问 /api/people -> 401", code == 401, code)

    code, data = login("demo", "wrong")
    check("密码错误 -> 401", code == 401, code)

    code, demo = login("demo", "demo123")
    check("demo 登录成功", code == 200 and bool(demo.get("token")), data.get("error"))
    code, guest = login("guest", "guest123")
    check("guest 登录成功", code == 200 and bool(guest.get("token")), guest.get("error"))
    dt, gt = demo.get("token", ""), guest.get("token", "")

    code, me = call("/api/auth/me", token=dt)
    check("me 返回当前用户", code == 200 and me.get("user", {}).get("username") == "demo", me)

    name = "隔离测试" + os.urandom(2).hex()
    code, saved = call("/api/people/save", "POST", {"name": name, "role": "测试",
                                                   "info": [], "simple": {}, "complex": {}}, token=dt)
    pid = (saved.get("person") or {}).get("id")
    check("demo 新建人物", code == 200 and bool(pid), saved)

    code, dpeople = call("/api/people", token=dt)
    check("demo 能看到新人物", any(p["name"] == name for p in dpeople.get("people", [])), len(dpeople.get("people", [])))
    code, gpeople = call("/api/people", token=gt)
    check("guest 看不到 demo 的人物",
          not any(p["name"] == name for p in gpeople.get("people", [])), len(gpeople.get("people", [])))

    code, _ = call("/api/latest")
    check("眼镜端 /api/latest 不带 token 也能用", code == 200, code)

    code, _ = call("/api/people/delete", "POST", {"id": pid}, token=dt)
    check("清理测试人物", code == 200, code)

    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""MemoryLens 验收测试（可提交版）：登录 -> /api/selftest/run -> 保存报告。

用法：
  python run_selftest.py
  python run_selftest.py --base http://127.0.0.1:8765 --user demo --pass demo123 --out report.json
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def call(base, path, body=None, token=""):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--user", default="demo")
    ap.add_argument("--pass", dest="password", default="demo123")
    ap.add_argument("--out", default=os.path.join(HERE, "report.json"))
    ap.add_argument("--no-latency", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    login = call(base, "/api/auth/login", {"username": args.user, "password": args.password})
    if not login.get("ok"):
        print("登录失败：", login.get("error"))
        return 2

    print("跑 8 项验收 + 10 次延迟 ...")
    report = call(base, "/api/selftest/run", {"latency": not args.no_latency},
                  token=login["token"])
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    for c in report.get("cases") or []:
        print("  [%s] #%s %s" % ("PASS" if c.get("passed") else "FAIL", c.get("id"), c.get("title")))
        if not c.get("passed"):
            print("      预期：", c.get("expected"))
            print("      实际：", json.dumps(c.get("actual"), ensure_ascii=False))
    lat = report.get("latency") or {}
    if lat:
        print("  延迟：min %s / p50 %s / p95 %s / max %s ms" % (
            lat.get("min"), lat.get("p50"), lat.get("p95"), lat.get("max")))
    print("通过 %s / %s，用时 %ss，报告：%s" % (
        report.get("passed"), len(report.get("cases") or []),
        round((report.get("elapsed_ms") or 0) / 1000), args.out))

    expected = {}
    try:
        with open(os.path.join(HERE, "expected.json"), encoding="utf-8") as f:
            expected = json.load(f)
    except OSError:
        pass
    ok = report.get("failed", 1) == 0
    lim = (expected.get("latency") or {})
    if lat and lim:
        if lat.get("p50", 0) > lim.get("p50_max_ms", 10 ** 9):
            ok = False
            print("  FAIL 延迟 p50 超过阈值", lim.get("p50_max_ms"))
        if lat.get("p95", 0) > lim.get("p95_max_ms", 10 ** 9):
            ok = False
            print("  FAIL 延迟 p95 超过阈值", lim.get("p95_max_ms"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

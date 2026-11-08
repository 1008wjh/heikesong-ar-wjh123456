# -*- coding: utf-8 -*-
"""连续延迟测量：10 次「提词」+ 10 次文本指令，输出 p50/p95。

赛题要求"至少进行 10 次连续延迟测量，附预期结果、实际结果和证据索引"。

用法：
  python test/latency_10.py
  python test/latency_10.py --n 10 --backend http://127.0.0.1:8765

产出：test/latency_report.json
"""
import argparse
import base64
import json
import math
import os
import sys
import time
import urllib.request

import _data_guard

keep = _data_guard.snapshot()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE = os.path.join(ROOT, "test", "voice")
REPORT = os.path.join(ROOT, "test", "latency_report.json")


def post(base, path, body, timeout=120):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    data = json.load(urllib.request.urlopen(req, timeout=timeout))
    return data, int((time.time() - t0) * 1000)


def get(base, path, timeout=30):
    return json.load(urllib.request.urlopen(base + path, timeout=timeout))


def wav(name):
    with open(os.path.join(VOICE, name), "rb") as f:
        return "data:audio/wav;base64," + base64.b64encode(f.read()).decode()


def pct(values, p):
    if not values:
        return 0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(math.ceil(p / 100.0 * len(s))) - 1))
    return s[idx]


def summarize(values):
    return {
        "n": len(values),
        "min": min(values) if values else 0,
        "p50": pct(values, 50),
        "p95": pct(values, 95),
        "max": max(values) if values else 0,
        "avg": round(sum(values) / len(values), 1) if values else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    base = args.backend.rstrip("/")
    n = max(1, args.n)

    status = get(base, "/api/agent/status")
    print("model:", status.get("model"), "asr:", status.get("asr"))

    # 用一个临时人物承接这 10 次请求，跑完删掉，别脏真实档案
    temp, _ = post(base, "/api/people/save", {
        "name": "延迟测试" + os.urandom(2).hex(),
        "role": "测试用", "info": ["延迟测量"], "simple": {}, "complex": {},
    })
    pid = (temp.get("person") or {}).get("id")
    if not pid:
        print("建临时人物失败：", temp)
        return 2

    session = "lat-" + os.urandom(3).hex()
    audio = wav("huihui_b.wav")
    post(base, "/api/agent/feed", {"session": session, "audio": audio,
                                   "person_id": pid, "auto_add": False, "mode": "single"})

    hint_ms = []
    hint_server = []
    print("\n== 提词（音频 + 模型），连续 %d 次 ==" % n)
    for i in range(n):
        r, ms = post(base, "/api/agent/feed", {
            "session": session, "audio": audio, "person_id": pid,
            "auto_add": False, "mode": "single", "manual": True, "preset": "hint",
        })
        server = int((r.get("ms") or 0))
        hint_ms.append(ms)
        hint_server.append(server)
        rows = len((r.get("view") or {}).get("rows") or [])
        print("  #%-2d  wall %5d ms   server %5d ms   rows=%d" % (i + 1, ms, server, rows))

    cmd_ms = []
    print("\n== 文本指令（不调模型），连续 %d 次 ==" % n)
    texts = ["只保留待办", "优先日期", "显示全部"]
    for i in range(n):
        r, ms = post(base, "/api/agent/feed", {
            "session": session, "text": texts[i % len(texts)],
            "mode": "single", "manual": True,
        })
        cmd_ms.append(ms)
        print("  #%-2d  wall %5d ms   %s" % (i + 1, ms, (r.get("view") or {}).get("state")))

    report = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backend": base,
        "model": status.get("model"),
        "asr": status.get("asr"),
        "hint": {"samples_ms": hint_ms, "server_ms": hint_server,
                 "wall": summarize(hint_ms), "server": summarize(hint_server)},
        "text_command": {"samples_ms": cmd_ms, "wall": summarize(cmd_ms)},
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    def show(name, s):
        print("  %-8s n=%d  min=%d  p50=%d  p95=%d  max=%d  avg=%.1f ms"
              % (name, s["n"], s["min"], s["p50"], s["p95"], s["max"], s["avg"]))

    print("\n== 汇总（含网络与客户端 HTTP 往返）==")
    show("提词 wall", report["hint"]["wall"])
    show("提词 server", report["hint"]["server"])
    show("文本指令", report["text_command"]["wall"])
    print("report:", REPORT)

    # 清理
    try:
        post(base, "/api/people/delete", {"id": pid})
    except Exception:                          # noqa: BLE001
        pass
    keep.restore()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""模拟"眼镜听到一句话"：直接把文本喂给智能体，镜片上就会出现卡片。

用法：
    python tools\\say.py "王姐：报价单我下周三之前发你"
    python tools\\say.py --session a1 "我和王姐聊了报价单"     # 同一段对话用同一个 session
    python tools\\say.py --audio test\\voice\\huihui_b.wav     # 也可以喂一段 wav（走 ASR）

每调用一次 = 一片 15/55 秒的录音，镜片会立刻更新。
"""
import argparse
import base64
import json
import os
import sys
import urllib.request

BACKEND = os.environ.get("MEMORYLENS_BACKEND", "http://127.0.0.1:8765")


def post(path, body):
    req = urllib.request.Request(
        BACKEND + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="", help="这句话的内容")
    ap.add_argument("--session", default="demo", help="同一段对话用同一个 session")
    ap.add_argument("--audio", default="", help="换成一段 wav 文件路径")
    ap.add_argument("--auto-add", action="store_true", help="陌生人自动建号")
    args = ap.parse_args()

    body = {"session": args.session}
    if args.audio:
        raw = open(args.audio, "rb").read()
        body["audio"] = "data:audio/wav;base64," + base64.b64encode(raw).decode()
    elif args.text:
        body["text"] = args.text
    else:
        ap.error("要么给一句话，要么给 --audio")

    if args.auto_add:
        body["auto_add"] = True

    out = post("/api/agent/feed", body)
    view = out.get("view") or {}
    print(f"[{view.get('state')}] {view.get('person') or '（没认人）'}"
          f"  sim={view.get('similarity', 0):.2f}  {view.get('ms', 0)}ms")
    for row in view.get("rows") or []:
        print("  " + str(row))
    if view.get("options"):
        print("  建议: " + " / ".join(str(o) for o in view["options"]))
    if view.get("degraded"):
        print("  （模型不可用，走规则兜底）")


if __name__ == "__main__":
    sys.exit(main())

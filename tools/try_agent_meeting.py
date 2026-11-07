"""手动试 LangGraph 智能体：把一段 wav 当成眼镜切片发给 /api/agent/feed。

先起服务：
    python src/server.py

再跑（多人会议）：
    python tools/try_agent_meeting.py test/demo_meeting.wav
    python tools/try_agent_meeting.py test/demo_meeting.wav --archive

单人：
    python tools/try_agent_meeting.py test/voice/huihui_b.wav --mode single
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def post(base, path, body):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav_pos", nargs="?", default=None, help="wav 路径（也可用 --wav）")
    ap.add_argument("--wav", default=os.path.join(ROOT, "test", "demo_meeting.wav"))
    ap.add_argument("--mode", default="meeting", choices=["meeting", "single"])
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    ap.add_argument("--session", default="try-" + os.urandom(3).hex())
    ap.add_argument("--archive", action="store_true", help="结束后写会议 JSON")
    ap.add_argument("--text", default="", help="不用音频，直接发文本")
    args = ap.parse_args()
    if args.wav_pos:
        args.wav = args.wav_pos

    if not args.text:
        with open(args.wav, "rb") as f:
            audio = "data:audio/wav;base64," + base64.b64encode(f.read()).decode()
    else:
        audio = ""

    print("session:", args.session, " mode:", args.mode, " audio:", bool(audio))
    r = post(args.backend.rstrip("/"), "/api/agent/feed",
             {"session": args.session, "audio": audio, "text": args.text,
              "mode": args.mode, "auto_add": False})
    view = r.get("view") or {}
    print(json.dumps(view, ensure_ascii=False, indent=1))

    if args.archive or args.mode == "meeting":
        a = post(args.backend.rstrip("/"), "/api/agent/archive", {"session": args.session})
        print("\n归档:", json.dumps(
            {k: a.get(k) for k in ("ok", "speaker_count", "action_items", "path")},
            ensure_ascii=False, indent=1))


if __name__ == "__main__":
    raise SystemExit(main())

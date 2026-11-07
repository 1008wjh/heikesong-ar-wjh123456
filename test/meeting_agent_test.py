# -*- coding: utf-8 -*-
"""多人会议智能体最小回归：分人 → 逐段转写 → 归档。

跑法：
    python test/meeting_agent_test.py            # 需要 8765 服务
    python test/meeting_agent_test.py --backend http://127.0.0.1:8765
"""
import argparse
import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "test", "demo_meeting.wav")


def post(base, path, body, timeout=180):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    base = args.backend.rstrip("/")

    if not os.path.isfile(DEMO):
        print("缺少 test/demo_meeting.wav")
        return 1
    with open(DEMO, "rb") as f:
        audio = "data:audio/wav;base64," + base64.b64encode(f.read()).decode()

    session = "meeting-test-" + os.urandom(3).hex()
    v = post(base, "/api/agent/feed", {"session": session, "audio": audio, "mode": "meeting"})
    view = v.get("view") or {}
    assert view.get("mode") == "meeting", view
    assert int(view.get("speaker_count") or 0) >= 3, view
    assert isinstance(view.get("rows"), list) and view["rows"], view
    print("ok 首片分人：%s 人 · %s" % (view["speaker_count"], " / ".join(view["rows"][:1])))

    # 同一 session 第二片：发言人注册表不能重复长人
    v2 = post(base, "/api/agent/feed", {"session": session, "audio": audio, "mode": "meeting"})
    view2 = v2.get("view") or {}
    assert int(view2.get("speaker_count") or 0) == int(view.get("speaker_count") or 0), view2
    print("ok 第二片人数不重复：%s 人" % view2["speaker_count"])

    a = post(base, "/api/agent/archive", {"session": session})
    assert a.get("ok") is True, a
    path = a.get("path") or ""
    assert path and os.path.isfile(path), a
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    assert int(meta.get("speaker_count") or 0) >= 3, meta
    assert len(meta.get("segments") or []) >= 5, meta
    print("ok 归档：%s 人 / %s 段 / %s" % (meta["speaker_count"], len(meta["segments"]), path))
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print("FAIL:", e)
        sys.exit(1)

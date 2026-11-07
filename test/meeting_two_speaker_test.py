# -*- coding: utf-8 -*-
"""两人会议回归：从 3 人 demo 截前两位，确认 2 人 → 确认人数 → 归档。

跑法：
    python test/meeting_two_speaker_test.py            # 需要 8765 服务
    python test/meeting_two_speaker_test.py --backend http://127.0.0.1:8765
"""
import argparse
import base64
import json
import os
import sys
import tempfile
import urllib.request
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "test", "demo_meeting.wav")


def post(base, path, body, timeout=180):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def feed(base, session, text):
    return post(base, "/api/agent/feed",
                {"session": session, "text": text, "mode": "meeting"}).get("view") or {}


def two_speaker_wav(path):
    """demo 的前 12.63s 正好是 A、B 两位；另存成临时 wav，避免提交音频。"""
    with wave.open(DEMO, "rb") as w:
        params = w.getparams()
        frames = w.readframes(int(12.63 * w.getframerate()))
    with wave.open(path, "wb") as o:
        o.setparams(params)
        o.writeframes(frames)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    base = args.backend.rstrip("/")

    if not os.path.isfile(DEMO):
        print("缺少 test/demo_meeting.wav")
        return 1

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        two_speaker_wav(tmp.name)
        with open(tmp.name, "rb") as f:
            audio = "data:audio/wav;base64," + base64.b64encode(f.read()).decode()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    session = "meeting-two-" + os.urandom(3).hex()
    view = post(base, "/api/agent/feed",
                {"session": session, "audio": audio, "mode": "meeting"}).get("view") or {}
    assert view.get("mode") == "meeting", view
    assert int(view.get("speaker_count") or 0) == 2, view
    assert view.get("state") == "待确认", view
    print("ok 两人首片：%s / %s 人" % (view["state"], view["speaker_count"]))

    v = feed(base, session, "改人数 2")
    assert int(v.get("expected_speakers") or 0) == 2, v
    print("ok 改人数：expected=%s" % v["expected_speakers"])

    v = feed(base, session, "确认人数")
    assert v.get("confirmed") is True and v.get("state") == "完成", v
    print("ok 确认人数：%s" % v["state"])

    a = post(base, "/api/agent/archive", {"session": session})
    assert a.get("ok") is True, a
    assert int(a.get("speaker_count") or 0) == 2, a
    path = a.get("path") or ""
    assert path and os.path.isfile(path), a
    print("ok 归档：%s 人 / %s" % (a["speaker_count"], path))
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print("FAIL:", e)
        sys.exit(1)

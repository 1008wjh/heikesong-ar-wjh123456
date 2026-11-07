# -*- coding: utf-8 -*-
"""多人会议 PRD 后端回归：会话 / 音频留存 / 转写落库 / AI 问答 / 列表详情 / 离线归集。

跑法（需要 8765 服务）：
    python test/meeting_prd_test.py
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "test", "demo_meeting.wav")


def post(base, path, body, timeout=180):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(base, path, timeout=30):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wav_part(path, start, end):
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        w.setpos(int(start * rate))
        frames = w.readframes(int((end - start) * rate))
        params = w.getparams()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams(params)
        out.writeframes(frames)
    return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    base = args.backend.rstrip("/")

    assert os.path.isfile(DEMO), "缺少 test/demo_meeting.wav"
    health = get(base, "/api/health")
    assert health.get("ok") is True, health

    started = post(base, "/api/meeting/start", {})
    session = started["session"]
    assert session and started.get("is_recording") is True, started
    print("ok 建会话：%s / %s" % (session, started.get("meeting_id")))

    r1 = post(base, "/api/meeting/feed", {"session": session,
                                         "audio": wav_part(DEMO, 0, 18)})
    assert r1.get("ok") is True, r1
    assert len(r1.get("segments") or []) >= 2, r1
    assert len(r1.get("speakers") or []) >= 2, r1
    n1 = len(r1["segments"])
    print("ok 第一片：%s 段 / %s 人 / %sms" % (n1, len(r1["speakers"]), r1.get("ms")))

    r2 = post(base, "/api/meeting/feed", {"session": session,
                                         "audio": wav_part(DEMO, 18, 35.7)})
    assert r2.get("ok") is True, r2
    assert len(r2.get("segments") or []) > n1, r2
    assert r2.get("transcript_tail"), r2
    print("ok 第二片：%s 段，字幕尾 %s 条" % (len(r2["segments"]),
                                          len(r2["transcript_tail"])))

    audio = os.path.join(ROOT, "audio_storage", session + ".pcm")
    assert os.path.isfile(audio) and os.path.getsize(audio) > 100000, audio
    print("ok 原始音频留存：%s（%.1f KB）" % (os.path.basename(audio),
                                         os.path.getsize(audio) / 1024))

    ask = post(base, "/api/meeting/ask", {"session": session})
    assert ask.get("ok") is True, ask
    assert len(ask.get("answer") or "") > 10, ask
    assert ask.get("context_snapshot"), ask
    print("ok AI 问答：%sms degraded=%s" % (ask.get("ms"), ask.get("degraded")))
    print("   " + ask["answer"].replace("\n", " | ")[:120])

    state = get(base, "/api/meeting/state?session=" + session)
    assert state.get("answer") == ask["answer"], state
    assert state.get("segments"), state
    print("ok 会议状态：%s 段 / %s 条问答" % (len(state["segments"]), len(state["answers"])))

    listing = get(base, "/api/meetings")
    assert any(item["session_id"] == session for item in listing["items"]), listing
    detail = get(base, "/api/meetings/" + session)
    assert detail.get("meeting", {}).get("session_id") == session, detail
    assert detail.get("answers"), detail
    print("ok 管理端列表/详情：%s 场会议，详情 %s 段" %
          (len(listing["items"]), len(detail["segments"])))

    stopped = post(base, "/api/meeting/stop", {"session": session})
    assert stopped.get("ok") is True, stopped
    assert stopped.get("segment_count", 0) >= 3, stopped
    print("ok 停止录制：%s 人 / %s 段 / %.1fs" %
          (stopped.get("speaker_count"), stopped.get("segment_count"),
           stopped.get("duration_sec") or 0))
    state2 = get(base, "/api/meeting/state?session=" + session)
    assert state2.get("is_recording") is False, state2

    off = post(base, "/api/meeting/offline", {"session": session})
    assert off.get("ok") is True, off
    print("ok 离线归集已提交：status=%s" % off.get("status"))
    status = 1
    detail2 = {}
    deadline = time.time() + 240
    while time.time() < deadline:
        detail2 = get(base, "/api/meetings/" + session)
        status = int(detail2["meeting"].get("offline_diarization_status") or 0)
        if status != 1:
            break
        time.sleep(2)
    assert status == 2, "离线归集超时/失败：%s" % json.dumps(detail2, ensure_ascii=False)[:300]
    groups = (detail2.get("offline_summary") or {}).get("groups") or []
    assert groups, detail2.get("offline_summary")
    print("ok 离线归集完成：%s 组说话人" % len(groups))
    for g in groups[:3]:
        print("   %s · %.1fs · %s" % (g.get("name"), g.get("seconds") or 0,
                                      (g.get("points") or [""])[0][:30]))

    print("\n全部通过 · session=%s" % session)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print("FAIL:", e)
        sys.exit(1)

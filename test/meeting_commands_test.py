# -*- coding: utf-8 -*-
"""多人会议文本指令回归：人数、绑定、五状态、四种布局、归档。

跑法：
    python test/meeting_commands_test.py            # 需要 8765 服务
    python test/meeting_commands_test.py --backend http://127.0.0.1:8765
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


def feed(base, session, text):
    return post(base, "/api/agent/feed",
                {"session": session, "text": text, "mode": "meeting"}).get("view") or {}


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

    session = "meeting-cmd-" + os.urandom(3).hex()
    view = post(base, "/api/agent/feed",
                {"session": session, "audio": audio, "mode": "meeting"}).get("view") or {}
    assert view.get("mode") == "meeting", view
    assert view.get("state") == "待确认", view
    assert view.get("layout") == "meeting", view
    assert int(view.get("speaker_count") or 0) >= 3, view
    print("ok 首片：%s / %s / %s 人" % (view["state"], view["layout"], view["speaker_count"]))

    v = feed(base, session, "改人数 3")
    assert int(v.get("expected_speakers") or 0) == 3, v
    print("ok 改人数：expected=%s" % v["expected_speakers"])

    v = feed(base, session, "只看待确认")
    assert v.get("layout") == "meeting_focus", v
    print("ok 布局 focus：%s" % v["layout"])

    v = feed(base, session, "按负责人")
    assert v.get("layout") == "meeting_compact", v
    assert any("按负责人" in r for r in (v.get("rows") or [])), v
    print("ok 布局 compact：%s" % v["layout"])

    v = feed(base, session, "给我建议")
    assert v.get("layout") == "meeting_advice", v
    assert (v.get("rows") or [""])[0] == "会议建议", v
    print("ok 布局 advice：%s" % v["layout"])

    v = feed(base, session, "回到全部")
    assert v.get("layout") == "meeting", v
    print("ok 回到全部：%s" % v["layout"])

    v = feed(base, session, "第二位是王姐")
    speakers = v.get("speakers") or []
    assert len(speakers) >= 2, v
    assert speakers[1].get("name") == "王姐", v
    print("ok 绑定第二位：%s" % speakers[1].get("name"))

    v = feed(base, session, "确认人数")
    assert v.get("confirmed") is True, v
    assert v.get("state") == "完成", v
    print("ok 确认人数：%s / confirmed=%s" % (v["state"], v["confirmed"]))

    v = feed(base, session, "随便说点什么")
    assert any("没听懂" in n for n in (v.get("notes") or [])), v
    assert any("没听懂" in r for r in (v.get("rows") or [])), v
    print("ok 未知指令提示：%s" % (v.get("notes") or [""])[-1])

    a = post(base, "/api/agent/archive", {"session": session})
    assert a.get("ok") is True, a
    assert a.get("summary"), a
    path = a.get("path") or ""
    assert path and os.path.isfile(path), a
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    assert int(meta.get("speaker_count") or 0) >= 3, meta
    assert meta.get("summary"), meta
    print("ok 归档：%s 人 / %s 段 / %s" % (meta["speaker_count"],
                                         len(meta.get("segments") or []), path))
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print("FAIL:", e)
        sys.exit(1)

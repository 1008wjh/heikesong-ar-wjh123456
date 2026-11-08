# -*- coding: utf-8 -*-
"""构造数据做一次端到端：新建人物 + 声纹 + 识别卡片 + 文本指令 + 构造多人会议。

数据都是现场构造的：
  - 人物 赵启明（客户），声纹用 SAPI Hazel 生成的英文语音
  - 会议音频 test/voice/constructed_meeting.wav：Huihui(我方) / Hazel(赵总) 交替 4 段

跑法：python test/constructed_e2e.py
结束后还原 people.json，并删掉这次产生的会议目录。
"""
import base64
import json
import os
import shutil
import sys
import urllib.request
import uuid

import _data_guard

keep = _data_guard.snapshot()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE = os.path.join(ROOT, "test", "voice")
MEETINGS = os.path.join(ROOT, "data", "meetings")
BACKEND = os.environ.get("MEMORYLENS_BACKEND", "http://127.0.0.1:8765")
passed = failed = 0
created_person = None
created_meeting = None


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok  " + name)
    else:
        failed += 1
        print("  FAIL " + name + ("  " + str(extra) if extra else ""))


def post(path, body, timeout=240):
    req = urllib.request.Request(
        BACKEND + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def get(path, timeout=60):
    return json.load(urllib.request.urlopen(BACKEND + path, timeout=timeout))


def data_url(path):
    with open(path, "rb") as f:
        return "data:audio/wav;base64," + base64.b64encode(f.read()).decode()


def flatten(simple):
    return ["%s: %s" % (k, v) for k, v in (simple or {}).items()
            if str(v).strip() and not str(k).startswith("_")]


def main():
    global created_person, created_meeting

    # ---------- 1) 构造人物 + 声纹 ----------
    simple = {"身份": "星海科技 · 采购总监", "信息1": "下周三前要正式报价",
              "信息2": "质保条款要写进合同"}
    complex_ = {"决策": {"预算": "40 万以内", "关注": ["交付周期", "质保"]},
                "关系": {"称呼": "赵总"}}
    r = post("/api/people/save", {
        "name": "赵启明", "role": "客户", "info": ["星海科技 · 采购总监"],
        "simple": simple, "complex": complex_,
    })
    created_person = (r.get("person") or {}).get("id")
    check("构造人物：赵启明", bool(created_person), r)

    zhao_wav = os.path.join(ROOT, "test", "constructed", "z1.wav")
    r = post("/api/voice/enroll", {"id": created_person, "audio": data_url(zhao_wav)})
    p = r.get("person") or {}
    check("构造声纹：入库 1 条", r.get("ok") is True and p.get("voice_count") == 1, r)
    check("声纹重播：录音已保存", bool(p.get("voice_clip_at")), p.get("voice_clip_at"))
    with urllib.request.urlopen(BACKEND + "/api/voice/clip?id=" + created_person) as f:
        clip = f.read()
    check("声纹重播：能取回 RIFF", clip[:4] == b"RIFF" and len(clip) > 10000, len(clip))

    # ---------- 2) 识别人：卡片要带简单数据 ----------
    r = post("/api/voice/identify", {"audio": data_url(zhao_wav)})
    card = r.get("card") or {}
    check("识别人认出赵启明", r.get("status") == "known" and r.get("name") == "赵启明", r.get("name"))
    check("卡片带构造的简单数据", (card.get("lines") or []) == flatten(simple),
          {"want": flatten(simple), "got": card.get("lines")})
    check("卡片身份是客户", card.get("role") == "客户", card.get("role"))

    # ---------- 3) 文本指令（同一 agent session） ----------
    s1 = "constructed-single-" + uuid.uuid4().hex[:6]
    v = post("/api/agent/feed", {"session": s1, "audio": data_url(zhao_wav),
                                 "auto_add": False, "mode": "single"})
    check("agent 那条也认成赵启明", (v.get("view") or {}).get("person") == "赵启明",
          (v.get("view") or {}).get("person"))
    v = post("/api/agent/feed", {"session": s1, "text": "只看待办", "manual": True,
                                 "mode": "single"})["view"]
    check("文本指令：只看待办 -> focus", v.get("layout") == "focus"
          and bool(v.get("rows")), v)
    v = post("/api/agent/feed", {"session": s1, "text": "显示全部", "manual": True,
                                 "mode": "single"})["view"]
    check("文本指令：显示全部 -> 档案卡", bool(v.get("rows")) and v.get("person") == "赵启明",
          v.get("person"))

    # ---------- 4) 构造的多人会议 ----------
    session = "constructed-meeting-" + uuid.uuid4().hex[:6]
    meeting_wav = os.path.join(VOICE, "constructed_meeting.wav")
    v = post("/api/agent/feed", {"session": session, "audio": data_url(meeting_wav),
                                 "mode": "meeting", "auto_add": False})["view"]
    check("会议：进入多人模式", v.get("mode") == "meeting", v.get("mode"))
    check("会议：分出 2 个说话人", int(v.get("speaker_count") or 0) >= 2, v.get("speaker_count"))
    check("会议：镜片行非空", bool(v.get("rows")), v.get("rows"))

    a = post("/api/agent/archive", {"session": session})
    created_meeting = session
    check("会议：归档成功", a.get("ok") is True, a)
    meta_path = a.get("path") or ""
    meta = {}
    if meta_path and os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    check("会议：meta 有 2 个说话人", int(meta.get("speaker_count") or 0) >= 2,
          meta.get("speaker_count"))
    check("会议：逐段转写 >= 4", len(meta.get("segments") or []) >= 4,
          len(meta.get("segments") or []))

    # 网页会议（PRD 服务）用同一段构造音频再走一遍
    prd = "constructed-prd-" + uuid.uuid4().hex[:6]
    st = post("/api/meeting/start", {"session": prd})
    check("网页会议：开始", st.get("ok") is True and st.get("is_recording") is True, st)
    f = post("/api/meeting/feed", {"session": prd, "audio": data_url(meeting_wav)})
    fv = f.get("view") or {}
    nspk = int(fv.get("speaker_count") or 0) or len(f.get("speakers") or [])
    check("网页会议：接收音频并分人", f.get("ok") is True and nspk >= 2, nspk)
    check("网页会议：有字幕", bool(f.get("transcript_tail")), len(f.get("transcript_tail") or []))
    sp = post("/api/meeting/stop", {"session": prd})
    check("网页会议：停止并生成摘要", sp.get("ok") is True and bool(sp.get("summary_text")),
          sp.get("summary_text"))
    d = get("/api/meetings/" + prd)
    segs = d.get("segments") or []
    check("网页会议详情：分段 >= 4", d.get("ok") is True and len(segs) >= 4, len(segs))
    names = [x.get("name") for x in (d.get("speakers") or [])]
    check("网页会议详情：说话人 >= 2", len(names) >= 2, names)
    check("网页会议：认出已入库的赵启明", "赵启明" in names, names)
    lst = get("/api/meetings")
    ids = [x.get("meeting_id") for x in (lst.get("items") or [])]
    check("网页会议列表：能看到这场", ("m_" + prd) in ids, ids[:5])

    # ---------- 5) 清理 ----------
    post("/api/people/delete", {"id": created_person})
    created_person = None
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        import meeting_db
        meeting_db.delete_session(prd)
    except Exception:                         # noqa: BLE001
        pass
    if created_meeting:
        mdir = os.path.join(MEETINGS, created_meeting)
        if os.path.isdir(mdir):
            shutil.rmtree(mdir, ignore_errors=True)
        # 眼镜侧的归档目录带 agent_ 前缀
        mdir2 = os.path.join(MEETINGS, "agent_" + created_meeting)
        if os.path.isdir(mdir2):
            shutil.rmtree(mdir2, ignore_errors=True)
        created_meeting = None

    print("\n%d passed, %d failed" % (passed, failed))
    keep.restore()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:                    # noqa: BLE001
        print("ERROR:", e)
        if created_person:
            try:
                post("/api/people/delete", {"id": created_person})
            except Exception:                 # noqa: BLE001
                pass
        if created_meeting:
            shutil.rmtree(os.path.join(MEETINGS, created_meeting), ignore_errors=True)
        keep.restore()
        rc = 1
    sys.exit(rc)

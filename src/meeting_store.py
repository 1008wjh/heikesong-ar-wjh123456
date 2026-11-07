"""会议冷数据存储：每个 session 一个 JSON，原子写。

热状态在 LangGraph checkpoint（内存）；这里只做“切片快照 + 结束归档”。
没有数据库依赖，评审现场能直接打开看。
"""
from __future__ import annotations

import json
import os
import re
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.environ.get("AR_DATA_DIR", os.path.join(ROOT, "data"))
MEETINGS_DIR = os.path.join(DATA_DIR, "meetings")


def _safe_session(session: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "meeting"))
    return s[:80] or "meeting"


def meeting_dir(session: str) -> str:
    return os.path.join(MEETINGS_DIR, "agent_" + _safe_session(session))


def meta_path(session: str) -> str:
    return os.path.join(meeting_dir(session), "meta.json")


def _write_atomic(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _payload(session: str, state: dict, status: str) -> dict:
    speakers = []
    for s in (state.get("speakers") or []):
        speakers.append({
            "spk_id": s.get("id") or s.get("spk_id"),
            "name": s.get("name") or "发言人",
            "person_id": s.get("person_id") or "",
            "seconds": round(float(s.get("seconds") or 0), 2),
            "similarity": s.get("similarity"),
        })
    return {
        "schemaVersion": 1,
        "meeting_id": state.get("meeting_id") or ("m_" + _safe_session(session)),
        "session": session,
        "status": status,
        "updated_at": int(time.time()),
        "duration_sec": round(float(state.get("offset_sec") or 0), 2),
        "slice_no": int(state.get("slice_no") or 0),
        "speaker_count": len(speakers),
        "speakers": speakers,
        "segments": list(state.get("segments") or [])[-400:],
        "actions": list(state.get("actions") or []),
        "summary": state.get("summary") or "",
        "degraded": bool(state.get("degraded")),
    }


def save_snapshot(session: str, state: dict) -> str:
    """每片结束写一次；服务重启后可从 meta.json 恢复。"""
    path = meta_path(session)
    _write_atomic(path, _payload(session, state, "live"))
    return path


def archive(session: str, state: dict) -> dict:
    """会议结束：写最终 meta，返回给眼镜端/控制台。"""
    path = meta_path(session)
    obj = _payload(session, state, "archived")
    _write_atomic(path, obj)
    return {
        "ok": True,
        "meeting_id": obj["meeting_id"],
        "speaker_count": obj["speaker_count"],
        "action_items": obj["actions"],
        "summary": obj["summary"],
        "path": path,
    }


def load(session: str) -> dict | None:
    path = meta_path(session)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

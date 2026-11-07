"""多人会议智能体（独立 LangGraph）。

与单人图的边界：
  - 单人图 = 认人 + 档案；会议图 = 分人 + 逐段转写 + 会议时间线 + 待办 + 归档。
  - 只复用 voice / llm / diarize / meeting_store 这些叶子能力，不混用 AgentState。

每个 session 一个 checkpoint；每片写一次 data/meetings/agent_<session>/meta.json。
"""
from __future__ import annotations

import base64
import io
import os
import re
import threading
import time
import wave
from typing import Any, TypedDict

import numpy as np
from langgraph.graph import END, START, StateGraph

import diarize
import llm
import meeting_store
import voice

MAX_SPEAKERS = 8
SPEAKER_MATCH = 0.72
MAX_SEGMENTS = 400
MAX_ASR_SEGMENTS = 12


class MeetingState(TypedDict, total=False):
    session: str
    meeting_id: str
    slice_no: int
    offset_sec: float
    audio: str
    text: str
    manual: bool
    people: list

    samples: Any
    slice_sec: float
    audio_peak: int
    diarize: dict
    mapped: list

    speakers: list
    segments: list
    actions: list
    transcript: str
    summary: str

    view: dict
    rev: int
    ms: int
    degraded: bool
    notes: list


def _decode_audio(payload: str) -> bytes:
    if not payload:
        return b""
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    if os.path.exists(payload):
        with open(payload, "rb") as f:
            return f.read()
    return base64.b64decode(payload)


def _wav_bytes(samples: np.ndarray) -> bytes:
    x = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice.SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def _safe_session(session: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "meeting"))[:80] or "meeting"


def _norm(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _best_speaker(centroid: np.ndarray, speakers: list[dict]):
    best, best_sim = None, -1.0
    c = _norm(centroid)
    for sp in speakers:
        raw = sp.get("centroid")
        if not raw:
            continue
        sim = float(np.dot(c, _norm(np.asarray(raw, dtype=np.float32))))
        if sim > best_sim:
            best, best_sim = sp, sim
    return (best, best_sim) if best is not None and best_sim >= SPEAKER_MATCH else (None, best_sim)


def _next_speaker_id(speakers: list[dict]) -> str:
    used = {s.get("id") for s in speakers}
    i = 1
    while ("spk_mtg_%d" % i) in used:
        i += 1
    return "spk_mtg_%d" % i


def _speaker_name(sp: dict, index: int) -> str:
    name = (sp.get("name") or "").strip()
    pid = sp.get("person_id")
    if pid and name and name != "陌生人":
        return name
    return "发言人%d" % (index + 1)


def _rule_actions(segments: list[dict]) -> list[dict]:
    out = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if re.search(r"答应|承诺|保证|会发|会给|负责|待办|记得|下周|明天|之前|截止", text):
            out.append({
                "owner": seg.get("name") or "",
                "task": text[:40],
                "deadline": "",
                "status": "pending",
                "evidence": seg.get("start"),
            })
    # 去重，最多 6 条
    seen, dedup = set(), []
    for a in out:
        key = re.sub(r"\s+", "", a["task"])[:24]
        if key in seen:
            continue
        seen.add(key)
        dedup.append(a)
    return dedup[:6]


# ---------------------------------------------------------------- 节点


def node_ingest(state: MeetingState) -> dict:
    audio = state.get("audio") or ""
    text = (state.get("text") or "").strip()
    session = state.get("session") or "meeting"
    out = {
        "session": session,
        "meeting_id": state.get("meeting_id") or ("m_" + _safe_session(session)),
        "slice_no": int(state.get("slice_no") or 0) + 1,
        "degraded": False,
        "notes": [],
    }
    if not audio:
        return out
    try:
        raw = _decode_audio(audio)
        samples = voice.read_wav(raw)
    except Exception as e:
        return {**out, "degraded": True, "notes": ["音频解析失败：%s" % e], "samples": None,
                "slice_sec": 0.0, "audio_peak": 0}
    limit = 60 * voice.SAMPLE_RATE
    if samples.size > limit:
        samples = samples[:limit]
    sec = float(samples.size) / voice.SAMPLE_RATE
    peak = int(np.max(np.abs(samples)) * 32768.0) if samples.size else 0
    return {**out, "samples": samples, "slice_sec": sec, "audio_peak": peak,
            "offset_sec": float(state.get("offset_sec") or 0)}


def node_diarize(state: MeetingState) -> dict:
    samples = state.get("samples")
    if samples is None or not getattr(samples, "size", 0):
        return {}
    try:
        res = diarize.diarize(samples, state.get("people") or [], per_speaker=True)
    except Exception as e:
        sec = float(state.get("slice_sec") or 0)
        res = {
            "speakers": [{"spk_id": "spk_0", "person_id": None, "name": "发言人1",
                          "seconds": round(sec, 2), "similarity": None}],
            "segments": [{"start": 0.0, "end": sec, "spk_id": "spk_0"}],
            "speaker_count": 1 if sec else 0,
            "_centroids": [],
            "reason": "分人失败，按单说话人兜底：%s" % e,
            "ms": 0,
        }
        return {"diarize": res, "degraded": True,
                "notes": (state.get("notes") or []) + [res["reason"]]}
    return {"diarize": res}


def node_merge(state: MeetingState) -> dict:
    res = state.get("diarize") or {}
    if not res.get("segments"):
        return {}
    centroids = res.get("_centroids") or []
    speakers = [dict(s) for s in (state.get("speakers") or [])]
    id_map: dict[str, str] = {}

    for i, sp in enumerate(res.get("speakers") or []):
        centroid = centroids[i] if i < len(centroids) else None
        hit, sim = (None, -1.0)
        if centroid is not None:
            hit, sim = _best_speaker(np.asarray(centroid, dtype=np.float32), speakers)
        if hit is not None:
            sid = hit["id"]
            hit["centroid"] = _norm(0.7 * _norm(np.asarray(hit["centroid"], dtype=np.float32))
                                    + 0.3 * _norm(np.asarray(centroid, dtype=np.float32))).tolist()
            hit["seconds"] = round(float(hit.get("seconds") or 0) + float(sp.get("seconds") or 0), 2)
            if sp.get("person_id"):
                hit["person_id"] = sp["person_id"]
                hit["name"] = sp.get("name") or hit.get("name")
                hit["similarity"] = sp.get("similarity")
        else:
            sid = _next_speaker_id(speakers)
            speakers.append({
                "id": sid,
                "name": _speaker_name(sp, len(speakers)),
                "person_id": sp.get("person_id") or "",
                "centroid": _norm(np.asarray(centroid, dtype=np.float32)).tolist() if centroid is not None else [],
                "seconds": round(float(sp.get("seconds") or 0), 2),
                "similarity": sp.get("similarity"),
            })
        id_map[sp.get("spk_id")] = sid

    mapped = []
    for seg in res.get("segments") or []:
        sid = id_map.get(seg.get("spk_id"))
        if not sid:
            continue
        mapped.append({"start": float(seg.get("start") or 0),
                       "end": float(seg.get("end") or 0), "spk": sid})
    return {"speakers": speakers, "mapped": mapped}


def node_asr(state: MeetingState) -> dict:
    samples = state.get("samples")
    mapped = state.get("mapped") or []
    if samples is None or not mapped:
        return {"mapped": []}

    offset = float(state.get("offset_sec") or 0)
    names = {s["id"]: s.get("name") or "发言人" for s in (state.get("speakers") or [])}
    out = []
    texts = []
    for seg in mapped[:MAX_ASR_SEGMENTS]:
        a = max(0, int(float(seg["start"]) * voice.SAMPLE_RATE))
        b = min(samples.size, int(float(seg["end"]) * voice.SAMPLE_RATE))
        text = ""
        if b - a >= int(0.2 * voice.SAMPLE_RATE):
            try:
                text = llm.transcribe(_wav_bytes(samples[a:b])) or ""
            except Exception:
                text = ""
        item = {
            "spk": seg["spk"],
            "name": names.get(seg["spk"]) or "发言人",
            "start": round(offset + float(seg["start"]), 2),
            "end": round(offset + float(seg["end"]), 2),
            "text": text[:200],
        }
        out.append(item)
        if text:
            texts.append("%s：%s" % (item["name"], text))
    return {
        "mapped": out,
        "segments": ((state.get("segments") or []) + out)[-MAX_SEGMENTS:],
        "transcript": ((state.get("transcript") or "") + " " + " ".join(texts)).strip()[-6000:],
        "offset_sec": round(offset + float(state.get("slice_sec") or 0), 2),
    }


def node_actions(state: MeetingState) -> dict:
    new = state.get("mapped") or []
    if not new:
        return {}
    actions = list(state.get("actions") or [])
    existing = {re.sub(r"\s+", "", a.get("task") or "")[:24] for a in actions}
    for a in _rule_actions(new):
        key = re.sub(r"\s+", "", a["task"])[:24]
        if key and key not in existing:
            existing.add(key)
            actions.append(a)
    return {"actions": actions[-6:]}


def node_view(state: MeetingState) -> dict:
    speakers = state.get("speakers") or []
    actions = state.get("actions") or []
    segments = state.get("segments") or []
    rows = []
    if speakers:
        names = [s.get("name") or "发言人" for s in speakers]
        rows.append("%d 人 · %s" % (len(speakers), " / ".join(names[:3])))
        for s in speakers[:2]:
            rows.append("%s · %ds" % (s.get("name") or "发言人", int(float(s.get("seconds") or 0))))
        if actions:
            rows.append("待办: " + str(actions[0].get("task") or "")[:18])
        if segments:
            rows.append("已转写 %d 段" % len(segments))
        rows.append("按「返回」结束并归档")
        state_name = "待确认" if int(state.get("slice_no") or 0) <= 1 else "完成"
    else:
        rows = ["会议中…", "还没听到明确的人声", "再说几句，或按返回结束"]
        state_name = "处理中"

    view = {
        "state": state_name,
        "layout": "meeting",
        "rows": rows[:6],
        "person": "会议",
        "person_id": state.get("meeting_id") or "",
        "role": "多人会议 · %d 人" % len(speakers),
        "similarity": 0.0,
        "options": ["保存", "只看待确认", "给我建议"],
        "mode": "meeting",
        "speaker_count": len(speakers),
        "action_count": len(actions),
        "actions": actions[:6],
        "degraded": bool(state.get("degraded")),
    }
    return {"view": view}


def build(memory=None):
    g = StateGraph(MeetingState)
    g.add_node("ingest", node_ingest)
    g.add_node("diarize", node_diarize)
    g.add_node("merge", node_merge)
    g.add_node("asr", node_asr)
    g.add_node("actions", node_actions)
    g.add_node("view", node_view)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "diarize")
    g.add_edge("diarize", "merge")
    g.add_edge("merge", "asr")
    g.add_edge("asr", "actions")
    g.add_edge("actions", "view")
    g.add_edge("view", END)
    return g.compile(checkpointer=memory) if memory else g.compile()


_GRAPH = None
_LOCK = threading.Lock()


def graph():
    global _GRAPH
    with _LOCK:
        if _GRAPH is None:
            try:
                from langgraph.checkpoint.memory import MemorySaver
                _GRAPH = build(MemorySaver())
            except Exception:
                _GRAPH = build()
        return _GRAPH


def _cfg(session: str) -> dict:
    return {"configurable": {"thread_id": session}}


def run_feed(session: str, text: str = "", audio: str = "", auto_add: bool = False,
             manual: bool = False, people: list | None = None) -> dict:
    """跑一片会议音频；session 相同会落到同一 checkpoint。"""
    t0 = time.time()
    state: MeetingState = {
        "session": session, "text": text, "audio": audio, "manual": bool(manual),
        "people": people or [], "degraded": False, "notes": [],
    }
    try:
        out = graph().invoke(state, _cfg(session))
    except Exception as e:
        view = {"state": "异常", "layout": "meeting",
                "rows": ["会议智能体出错了", str(e)[:40]], "person": "会议",
                "mode": "meeting", "speaker_count": 0, "action_count": 0}
        view["rev"] = int(time.time() * 1000) % 1000000
        view["ms"] = int((time.time() - t0) * 1000)
        return view

    view = out.get("view") or {"state": "处理中", "layout": "meeting",
                               "rows": ["会议中…"], "person": "会议", "mode": "meeting"}
    view["rev"] = int(time.time() * 1000) % 1000000
    view["ms"] = int((time.time() - t0) * 1000)
    view["notes"] = out.get("notes") or []
    try:
        meeting_store.save_snapshot(session, out)
    except Exception as e:
        view["notes"] = (view.get("notes") or []) + ["会议快照写盘失败：%s" % e]
    return view


def archive(session: str) -> dict:
    """结束会议：把 checkpoint 里的状态落成 meta.json。"""
    try:
        snap = graph().get_state(_cfg(session))
        state = dict(snap.values or {})
    except Exception as e:
        return {"ok": False, "error": "读取会议状态失败：%s" % e}
    if not state:
        return {"ok": False, "error": "没有这段会议记录"}
    try:
        return meeting_store.archive(session, state)
    except Exception as e:
        return {"ok": False, "error": "写会议归档失败：%s" % e}

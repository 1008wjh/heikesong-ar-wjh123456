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
# 55 秒会议通常只有十几段；24 是上限，避免长段会议把后半段整段丢掉。
MAX_ASR_SEGMENTS = 24
MAX_ACTIONS = 8

LAYOUT_FULL = "meeting"
LAYOUT_FOCUS = "meeting_focus"
LAYOUT_COMPACT = "meeting_compact"
LAYOUT_ADVICE = "meeting_advice"


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

    # 会议交互：五状态里的“待确认”不是靠首片猜，而是先问用户再锁人数。
    layout: str
    confirmed: bool
    expected_speakers: int
    command: str

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


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_int(value: str) -> int:
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    if value in _CN_NUM:
        return _CN_NUM[value]
    if value.startswith("十") and len(value) == 1:
        return 10
    if value.startswith("十") and value[1:] in _CN_NUM:
        return 10 + _CN_NUM[value[1:]]
    if len(value) == 2 and value[0] in _CN_NUM and value[1] == "十":
        return _CN_NUM[value[0]] * 10
    return 0


def _find_person(name: str, people: list[dict]):
    name = (name or "").strip()
    if not name:
        return None
    exact = [p for p in (people or []) if (p.get("name") or "").strip() == name]
    if exact:
        return exact[0]
    # “王姐 / 王姐老师”这种称呼允许包含匹配，但至少两个字，避免“王”误配。
    if len(name) >= 2:
        for p in (people or []):
            pn = (p.get("name") or "").strip()
            if len(pn) >= 2 and (pn in name or name in pn):
                return p
    return None


def _pending_actions(actions: list[dict]) -> list[dict]:
    return [a for a in actions if (a.get("status") or "pending") == "pending"]


def _advice(speakers: list[dict], actions: list[dict], expected: int) -> list[str]:
    pending = _pending_actions(actions)
    out = []
    if expected and len(speakers) != expected:
        out.append("人数对不上：检测到 %d 人，你说 %d 人" % (len(speakers), expected))
    if len(speakers) >= 2:
        out.append("先确认人数，再把「这是谁」绑到真实姓名")
    if pending:
        first = pending[0]
        owner = first.get("owner") or "未指定"
        out.append("先追：%s 的「%s」" % (owner, (first.get("task") or "")[:18]))
        out.append("会后逐条确认负责人和截止时间")
    else:
        out.append("还没听到明确待办：可以问「谁负责、什么时候」")
    out.append("说「只看待确认」只看待办，说「保存」锁定本场")
    return out[:5]


def _summary(speakers: list[dict], segments: list[dict], actions: list[dict]) -> str:
    bits = []
    if speakers:
        bits.append("%d 人" % len(speakers))
    if segments:
        bits.append("%d 段" % len(segments))
    if actions:
        bits.append("%d 条待办" % len(actions))
    return " · ".join(bits) if bits else "会议还没听到内容"


# ---------------------------------------------------------------- 节点


def node_ingest(state: MeetingState) -> dict:
    audio = state.get("audio") or ""
    text = (state.get("text") or "").strip()
    session = state.get("session") or "meeting"
    has_audio = bool(audio)
    out = {
        "session": session,
        "meeting_id": state.get("meeting_id") or ("m_" + _safe_session(session)),
        # 文本指令不是“新的一片”，不能推进 slice_no，否则待确认状态会被跳过。
        "slice_no": int(state.get("slice_no") or 0) + (1 if has_audio else 0),
        "degraded": False,
        "notes": [],
    }
    if not audio:
        # 清掉上一片的采样/分人，否则文本指令会拿旧音频再跑一遍。
        return {**out, "audio": "", "samples": None, "mapped": [], "diarize": {},
                "slice_sec": 0.0, "audio_peak": 0,
                "offset_sec": float(state.get("offset_sec") or 0)}
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


def node_commands(state: MeetingState) -> dict:
    """会议文本指令。

    眼镜只有上下左右/确认/返回，所以会议里的文本不是“再开一段对话”，
    而是对当前会议的窄指令：确认人数、绑人、切布局、看建议、保存标记。
    """
    raw = (state.get("text") or "").strip()
    if not raw:
        return {}
    text = raw.replace(" ", "")
    speakers = [dict(s) for s in (state.get("speakers") or [])]
    layout = state.get("layout") or LAYOUT_FULL
    confirmed = bool(state.get("confirmed"))
    expected = int(state.get("expected_speakers") or 0)
    notes: list[str] = []

    # 1) 绑人：这是王姐 / 第二位是王姐 / 换成王姐
    bind = re.search(
        r"(?:第([一二三四五六七八九十\d]+)位|第([一二三四五六七八九十\d]+)个)?"
        r"(?:是|叫|换成)([\u4e00-\u9fffA-Za-z·]{1,12})", text)
    if bind and bind.group(3) not in ("谁", "哪位", "什么", "会议", "这样"):
        name = bind.group(3).strip("，。！？、")
        idx = _cn_int(bind.group(1) or bind.group(2) or "1") - 1
        if not speakers:
            notes.append("还没分人，先说几句再绑定")
        elif idx < 0 or idx >= len(speakers):
            notes.append("没有第 %d 位发言人" % (idx + 1))
        else:
            person = _find_person(name, state.get("people") or [])
            sp = speakers[idx]
            sp["name"] = (person or {}).get("name") or name
            if person:
                sp["person_id"] = person.get("id") or ""
                notes.append("第 %d 位已绑定 %s" % (idx + 1, sp["name"]))
            else:
                sp["person_id"] = ""
                notes.append("本场标记为 %s（通讯录没有，未写人脉）" % name)
            return {"speakers": speakers, "notes": notes, "command": "bind"}

    # 2) 人数：改人数 3 / 3 人 / 三人
    m = (re.search(r"改人数\s*(\d+)", text)
         or re.search(r"(\d+)\s*人", text)
         or re.search(r"改人数\s*([一二两三四五六七八九十])", text)
         or re.search(r"([一二两三四五六七八九十])\s*人", text))
    if m:
        expected = _cn_int(m.group(1))
        notes.append("本场按 %d 人记录；检测到 %d 人" % (expected, len(speakers)))
        return {"expected_speakers": expected, "notes": notes, "command": "count"}

    # 3) 布局：必须放在“确认”之前，否则“只看待确认”会先命中确认。
    if re.search(r"回到全部|看全部|取消筛选|全部", text):
        return {"layout": LAYOUT_FULL, "notes": ["已回到全部"], "command": "layout"}
    if re.search(r"只看待确认|待确认|待办", text):
        return {"layout": LAYOUT_FOCUS, "notes": ["只看待确认"], "command": "layout"}
    if re.search(r"按负责人|负责人分组|分组", text):
        return {"layout": LAYOUT_COMPACT, "notes": ["按负责人分组"], "command": "layout"}
    if re.search(r"给我建议|建议|怎么办", text):
        return {"layout": LAYOUT_ADVICE, "notes": ["会议建议"], "command": "layout"}

    # 4) 确认/保存：只锁定本场，不在这里归档（归档由返回键触发）。
    if re.search(r"确认人数|确认参会|确认|锁定|保存", text):
        confirmed = True
        notes.append("已确认 %d 人；按返回归档" % len(speakers) if speakers else "还没有分人结果")
        return {"confirmed": confirmed, "notes": notes, "command": "confirm"}

    # 5) 未知指令：明确提示可用词，避免用户以为没反应
    return {"notes": ["没听懂「%s」；可用：确认人数 / 只看待确认 / 按负责人 / 给我建议 / 保存"
                      % raw[:18]], "command": "unknown"}


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
    # 24 段是硬上限；超过时保留长段（信息更多），短视频噪声先让位，再按时间还原。
    selected = sorted(
        mapped,
        key=lambda s: float(s.get("end") or 0) - float(s.get("start") or 0),
        reverse=True,
    )[:MAX_ASR_SEGMENTS]
    selected.sort(key=lambda s: float(s.get("start") or 0))
    for seg in selected:
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
    return {"actions": actions[-MAX_ACTIONS:]}


def node_summary(state: MeetingState) -> dict:
    return {"summary": _summary(state.get("speakers") or [],
                                state.get("segments") or [],
                                state.get("actions") or [])}


def node_view(state: MeetingState) -> dict:
    speakers = state.get("speakers") or []
    actions = state.get("actions") or []
    segments = state.get("segments") or []
    notes = state.get("notes") or []
    layout = state.get("layout") or LAYOUT_FULL
    confirmed = bool(state.get("confirmed"))
    expected = int(state.get("expected_speakers") or 0)
    has_audio = bool(state.get("audio"))
    summary = state.get("summary") or _summary(speakers, segments, actions)

    # 五状态：待命 → 处理中 → 待确认 → 完成；异常由 run_feed 兜底。
    # 2 人以上必须由用户确认一次人数；不按切片数退回“完成”，否则第二片自动
    # 切完就把“待确认”冲掉了。
    waiting = bool(speakers) and not confirmed and len(speakers) >= 2
    if speakers:
        state_name = "待确认" if waiting else "完成"
    elif has_audio:
        state_name = "处理中"
    else:
        state_name = "待命"

    names = [s.get("name") or "发言人" for s in speakers]
    rows: list[str] = []
    if layout == LAYOUT_FOCUS:
        pending = _pending_actions(actions)
        rows.append("待确认待办 · %d 条" % len(pending))
        for a in pending[:4]:
            rows.append("%s · %s" % ((a.get("owner") or "未指定"), (a.get("task") or "")[:18]))
        if not pending:
            rows.append("没有待确认待办")
        if waiting:
            rows.append("人数待确认 · 说「确认人数」")
        rows.append("说「回到全部」恢复")
    elif layout == LAYOUT_COMPACT:
        groups: dict[str, list[dict]] = {}
        for a in actions:
            groups.setdefault(a.get("owner") or "未指定", []).append(a)
        rows.append("按负责人 · %d 条待办" % len(actions))
        for owner, items in list(groups.items())[:4]:
            rows.append("%s · %d 条" % (owner, len(items)))
        if not actions:
            rows.append("还没有待办")
        rows.append("说「回到全部」恢复")
    elif layout == LAYOUT_ADVICE:
        rows.append("会议建议")
        rows.extend(_advice(speakers, actions, expected)[:4])
        rows.append("说「回到全部」恢复")
    else:
        if speakers:
            if waiting:
                rows.append("检测到 %d 人 · 确认参会人" % len(speakers))
            else:
                rows.append("%d 人 · %s" % (len(speakers), " / ".join(names[:3])))
            for s in speakers[:2]:
                rows.append("%s · %ds" % (s.get("name") or "发言人",
                                          int(float(s.get("seconds") or 0))))
            if expected and expected != len(speakers):
                rows.append("人数 %d≠%d · 说「改人数 %d」" % (len(speakers), expected, len(speakers)))
            if actions:
                rows.append("待办: " + str(actions[0].get("task") or "")[:18])
            if segments:
                rows.append("已转写 %d 段" % len(segments))
            rows.append("按「返回」结束并归档")
        elif has_audio:
            rows = ["会议中…", "还没听到明确的人声", "再说几句，或按返回结束"]
        else:
            # 文本指令先于第一片音频：保持“待命”，但把可用词和错误提示露出来。
            if notes:
                rows = [str(notes[-1])[:24], "可用：确认人数 / 只看待确认 / 给我建议"]
            else:
                rows = ["会议待命", "按一下开始听 · 左/右选模式",
                        "可用：确认人数 / 只看待确认 / 给我建议"]

    # 所有布局都要把“绑定成功/人数已改/没听懂”露出来，否则文本指令像没反应。
    # full 卡把提示插在标题后，避免被底部“按返回”挤掉。
    if notes:
        note = str(notes[-1])[:24]
        if note not in rows:
            if layout == LAYOUT_FULL and rows:
                rows.insert(1, note)
            else:
                rows.append(note)

    # 布局优先，但“待确认”时任何布局都要能一键确认人数。
    if layout == LAYOUT_FOCUS:
        options = ["回到全部", "保存"] + (["确认人数"] if waiting else [])
    elif layout == LAYOUT_COMPACT:
        options = ["回到全部", "只看待确认", "保存"] + (["确认人数"] if waiting else [])
    elif layout == LAYOUT_ADVICE:
        options = ["回到全部", "保存"] + (["确认人数"] if waiting else [])
    elif waiting:
        options = ["确认人数", "只看待确认", "给我建议"]
    else:
        options = ["只看待确认", "按负责人", "给我建议", "保存"]

    role = "多人会议 · %d 人" % len(speakers)
    if state_name == "待确认":
        role += " · 待确认"
    elif state_name == "待命":
        role = "多人会议 · 待命"

    view = {
        "state": state_name,
        "layout": layout,
        "rows": rows[:6],
        "person": "会议",
        "person_id": state.get("meeting_id") or "",
        "role": role,
        "similarity": 0.0,
        "options": options,
        "mode": "meeting",
        "speaker_count": len(speakers),
        "speakers": [{"id": s.get("id"), "name": s.get("name") or "发言人",
                      "person_id": s.get("person_id") or "",
                      "seconds": s.get("seconds") or 0,
                      "similarity": s.get("similarity")} for s in speakers],
        "action_count": len(actions),
        "actions": actions[:MAX_ACTIONS],
        "summary": summary,
        "confirmed": confirmed,
        "expected_speakers": expected,
        "degraded": bool(state.get("degraded")),
    }
    # PRD 的多人模块需要"底部实时字幕"和"AI 上下文"，所以把逐段结果也放进 view。
    # 时间戳统一用毫秒（相对会议开始），和 speech_record 表保持一致。
    tail = []
    for seg in segments[-24:]:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        tail.append({
            "spk_id": seg.get("spk") or "",
            "name": seg.get("name") or "",
            "start_ts": int(round(float(seg.get("start") or 0) * 1000)),
            "end_ts": int(round(float(seg.get("end") or 0) * 1000)),
            "content": text,
        })
    view["segments"] = tail
    view["transcript_tail"] = tail[-4:]
    return {"view": view}


def build(memory=None):
    g = StateGraph(MeetingState)
    g.add_node("ingest", node_ingest)
    g.add_node("commands", node_commands)
    g.add_node("diarize", node_diarize)
    g.add_node("merge", node_merge)
    g.add_node("asr", node_asr)
    g.add_node("actions", node_actions)
    g.add_node("summary", node_summary)
    g.add_node("view", node_view)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "commands")
    g.add_edge("commands", "diarize")
    g.add_edge("diarize", "merge")
    g.add_edge("merge", "asr")
    g.add_edge("asr", "actions")
    g.add_edge("actions", "summary")
    g.add_edge("summary", "view")
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
        view = {"state": "异常", "layout": LAYOUT_FULL,
                "rows": ["会议智能体出错了", str(e)[:40]], "person": "会议",
                "mode": "meeting", "speaker_count": 0, "action_count": 0,
                "options": ["回到全部", "保存"], "summary": "会议处理失败",
                "confirmed": False, "expected_speakers": 0,
                "degraded": True}
        view["rev"] = int(time.time() * 1000) % 1000000
        view["ms"] = int((time.time() - t0) * 1000)
        view["notes"] = ["会议智能体异常：%s" % e]
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

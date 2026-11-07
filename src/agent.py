# -*- coding: utf-8 -*-
"""单人对话智能体（LangGraph）。

思路：
- 眼镜端只负责"按一下 → 每 15 秒切一片 → 上传"，不做任何判断。
- 判断都在这里：这一段是单人对谈还是多人会议？这人是谁（声纹还是语义）？
  说了哪些要点？界面该长什么样？要不要写进档案？
- 大模型挂了不影响主流程：每个 LLM 节点都有规则兜底，并在 view 里标 degraded，
  这就是"模型调用失败时的兜底"。
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import cards
import diarize
import llm
import meeting
import voice

MAX_TRANSCRIPT = 4000
AGENT_MEETING_ROOT = os.environ.get("AR_MEETING_DIR", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "meetings"))


class AgentState(TypedDict, total=False):
    session: str
    text: str            # 这一片送来的文本（可能为空，靠转写补）
    audio: str           # dataURL / base64 / 本地路径，只有首片需要
    auto_add: bool
    mode: str            # single | meeting | unknown
    person_id: str
    person_name: str
    matched_by: str      # voice | semantic | none
    similarity: float
    items: list
    summary: str
    degraded: bool       # 走了规则兜底（模型不可用）
    notes: list          # 给界面的诊断行（比如"模型不可用，用规则抽取"）
    view: dict
    rev: int
    ms: int
    # ---- 多人会议 ----
    meeting_id: str
    meeting_offset: float        # 已处理音频的秒数，给分段时间轴用
    meeting_slice_no: int
    meeting_speakers: list       # [{person_id,name,seconds,similarity}]
    meeting_segments: list       # [{spk,start,end,text}]
    meeting_text: str            # 累积转写，供行动项抽取
    meeting_actions: list        # [{owner,task,deadline,status}]
    meeting_layout: str          # full | focus | compact | advice
    meeting_advice: list         # 建议问题（给"给我建议"用）
    meeting_attitude: str        # 建议态度
    meeting_confirmed: bool
    meeting_archived: bool
    # ---- 对话建议 ----
    advice: dict


# ---------- 工具：匹配、抽取 ----------

def _people() -> list[dict]:
    return store_ref.people()


store_ref = None      # 由 server 注入，避免循环 import


def tool_voice_match(state: AgentState) -> dict:
    """首片：用声纹认人。返回 person_id/name/similarity。"""
    audio = state.get("audio") or ""
    if not audio:
        return {"matched_by": "none"}
    try:
        emb = voice.embed_wav(decode_audio_ref(audio))
    except Exception as e:                                    # 音频坏了不该中断整段
        return {"matched_by": "none", "notes": ["音频解析失败：%s" % e]}
    person, sim, _margin = voice.match(emb, store_ref.voice_index())
    if person:
        return {"person_id": person["id"], "person_name": person["name"],
                "similarity": round(float(sim), 3), "matched_by": "voice", "_emb": emb}
    if state.get("auto_add"):
        name = next_stranger_name(store_ref)
        created = store_ref.save(name=name, role="陌生人 · 眼镜端自动建的",
                                 info=["语音自动入库，回头改个名字", "", ""])
        store_ref.add_voice(created["id"], emb)
        return {"person_id": created["id"], "person_name": name, "similarity": 1.0,
                "matched_by": "voice", "notes": ["新面孔，已建号 %s" % name]}
    return {"matched_by": "none", "similarity": round(float(sim), 3)}


def tool_semantic_match(state: AgentState) -> dict:
    """没有声纹命中时，让模型从库里挑一个人（"刚才那个产品经理"这种指代）。"""
    if state.get("person_id"):
        return {}
    text = (state.get("text") or "").strip()
    if not text or not llm.available():
        return {}
    names = [p["name"] for p in _people() if p.get("name")]
    if not names:
        return {}
    try:
        pick = llm.pick_person(text, names)
    except llm.LLMUnavailable:
        return {"degraded": True}
    if pick and pick in names:
        person = [p for p in _people() if p["name"] == pick][0]
        return {"person_id": person["id"], "person_name": person["name"],
                "matched_by": "semantic", "similarity": 0.0}
    return {}


def tool_extract(state: AgentState) -> dict:
    """抽要点。模型不可用就退回关键词规则，保证界面照样有东西。"""
    text = state.get("text") or ""
    person = {"name": state.get("person_name") or "这个人", "role": ""}
    try:
        got = llm.analyze(text, person)
        return {"items": got.get("items") or [], "summary": got.get("summary") or ""}
    except llm.LLMUnavailable as e:
        return {"items": rule_extract(text), "summary": "",
                "degraded": True, "notes": ["模型不可用（%s），用关键词兜底" % e]}


def rule_extract(text: str) -> list[dict]:
    """没有模型时的兜底抽取：找承诺、时间、待办。"""
    out = []
    for raw in re.split(r"[。！？!?\n]", text):
        line = raw.strip(" ，,")
        if not line:
            continue
        key = None
        if re.search(r"答应|一定|保证|承诺|会发|会寄|会给", line):
            key = "答应的事"
        elif re.search(r"下周|明天|后天|月底|周[一二三四五六日天]|\d+[点时]", line):
            key = "日期"
        elif re.search(r"记得|别忘|要带|带上", line):
            key = "跟进"
        if key and len(out) < 6:
            out.append({"key": key, "value": line[:40], "quote": line[:60]})
    return out


# ---------- 图 ----------

def node_ingest(state: AgentState) -> dict:
    text = (state.get("text") or "").strip()
    audio = state.get("audio") or ""
    if not text and audio and (state.get("mode") or "").lower() != "meeting":
        try:
            text = llm.transcribe(decode_audio_ref(audio))
        except Exception as e:
            return {"text": "", "notes": (state.get("notes") or []) + ["转写失败：%s" % e]}
    window = ((state.get("window") or "") + " " + text).strip()[-2000:]
    return {"text": text[:MAX_TRANSCRIPT], "window": window,
            "slice_no": int(state.get("slice_no") or 0) + 1}


def node_route(state: AgentState) -> dict:
    """单人还是多人。用户自己点按钮选的优先；没选才用规则/模型兜底。"""
    picked = (state.get("mode") or "").lower()
    if picked in ("single", "meeting"):
        return {"mode": picked}
    text = state.get("text") or ""
    speakers = len({m.group(1) for m in re.finditer(r"(?:^|\n)\s*([^：:\n]{1,6})[：:]", text)})
    # 只看"有几个说话人"，别因为内容里出现"开会"就当成会议
    if speakers >= 3 or re.search(r"大家|各位|我们三个人|多方", text):
        return {"mode": "meeting"}
    if not llm.available():
        return {"mode": "single"}
    try:
        mode = llm.classify_mode(text)
        return {"mode": "meeting" if mode == "meeting" else "single"}
    except llm.LLMUnavailable:
        return {"mode": "single", "degraded": True}


def node_identify(state: AgentState) -> dict:
    """先声纹，再语义。声纹是本地 40ms，语义要模型。"""
    out = tool_voice_match(state)
    merged = {k: v for k, v in out.items() if not k.startswith("_")}
    if not merged.get("person_id"):
        merged.update(tool_semantic_match({**state, **merged}))
    return merged


def node_extract(state: AgentState) -> dict:
    return tool_extract(state)


def suggest_options(state_name, person_name):
    """给眼镜端三条快捷指令（文本通道的入口之一）。"""
    if state_name == "待确认":
        return ["就是我说的这个人", "换一个人", "先别入库"]
    if state_name == "待命":
        return ["开始记录", "我刚才说了什么", "帮我总结一下"]
    if state_name == "异常":
        return ["再试一次", "换成手打", "先存着"]
    return ["只留待办", "再说细一点", "不是他"]


def node_render(state: AgentState) -> dict:
    """把"人 + 要点"渲染成镜片视图模型（五状态之一 + 布局）。"""
    if state.get("mode") == "meeting":
        return {"view": {
            "state": "待确认",
            "layout": "notice",
            "rows": ["这是多人谈话", "多人模块由另一位同学负责", "本段只做了转写，没入库"],
            "person": "",
            "degraded": state.get("degraded", False),
        }}
    name = state.get("person_name") or ""
    items = state.get("items") or []
    if not items:
        rows = ["没听到明确的要点", "再说一段，或者对着他说说看"]
        state_name = "完成" if name else "待命"
    else:
        rows = ["%s: %s" % (it.get("key", "其他"), it.get("value", "")) for it in items[:6]]
        state_name = "完成" if name else "待确认"
    if state.get("degraded"):
        rows = rows + ["（模型不可用，规则兜底）"]
    advice = state.get("advice") if isinstance(state.get("advice"), dict) else {}
    options = state.get("next_actions") or []
    if advice:
        att = advice.get("attitude") or {}
        if att.get("recommended"):
            rows = rows + ["态度: " + str(att.get("recommended"))]
        qs = advice.get("questions") or []
        rows = rows + ["问: " + str(q.get("text")) for q in qs[:2]]
        options = [str(q.get("text")) for q in qs[:3] if q.get("text")] or options
    return {"view": {
        "state": state_name,
        "layout": "card",
        "rows": rows[:6],
        "person": name,
        "person_id": state.get("person_id") or "",
        "matched_by": state.get("matched_by") or "none",
        "similarity": state.get("similarity") or 0.0,
        "options": (options or suggest_options(state_name, name))[:3],
        "advice": advice,
        "degraded": state.get("degraded", False),
    }}


def node_recommend(state: AgentState) -> dict:
    """下一步做什么：把档案 + 这段对话交给模型，让它给三条可点的建议。"""
    if state.get("mode") == "meeting":
        return {}
    # 第一片只做"认人 + 要点"；下一步建议从第二片起（也就是 55 秒那一片）才调模型
    if int(state.get("slice_no") or 1) <= 1:
        return {}
    person = store_ref.get(state.get("person_id") or "") if state.get("person_id") else None
    profile = ""
    if person:
        lines = cards.simple_lines(person, 6)
        facts = (person.get("complex") or {}).get("对话要点") or {}
        extra = ["%s: %s" % (k, v) for k, v in list(facts.items())[:6]]
        profile = " / ".join([x for x in lines + extra if x])[:600]
    try:
        got = llm.recommend(state.get("window") or state.get("text") or "",
                            state.get("person_name") or "", profile, state.get("items") or [])
        nxt = got.get("next") or []
        advice = {}
        try:
            advice = llm.advise(state.get("window") or state.get("text") or "",
                                state.get("person_name") or "", profile,
                                state.get("items") or [], "single")
        except llm.LLMUnavailable:
            pass
        return {"next_actions": nxt, "advice": advice,
                "summary": state.get("summary") or got.get("summary") or ""}
    except llm.LLMUnavailable:
        return {"degraded": True,
                "notes": (state.get("notes") or []) + ["给建议时模型不可用，用固定三条"]}


def node_commit(state: AgentState) -> dict:
    """有主、有要点才写档案；多人或没认出人就只留在界面上。"""
    pid = state.get("person_id")
    items = state.get("items") or []
    if pid and items and state.get("mode") != "meeting":
        try:
            store_ref.merge_facts(pid, items, state.get("text", ""), state.get("summary", ""))
        except Exception as e:
            return {"notes": (state.get("notes") or []) + ["写档案失败：%s" % e]}
    return {}


def _safe_session(session: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session or "session")[:64]


def _meeting_dir(session: str) -> str:
    return os.path.join(AGENT_MEETING_ROOT, "agent_" + _safe_session(session))


def _save_slice(session: str, seq: int, wav: bytes) -> str:
    d = _meeting_dir(session)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "slice_%03d.wav" % seq)
    with open(p, "wb") as f:
        f.write(wav)
    return p


def _merge_speakers(old: list, new: list) -> list:
    """跨切片合并说话人：认识的按 person_id 合并，陌生人按 spk_id 分开。"""
    out = [dict(s) for s in (old or [])]
    index = {}
    for s in out:
        key = s.get("person_id") or ("u:" + str(s.get("spk_key") or s.get("name") or "陌生人"))
        s["_key"] = key
        index[key] = s
    for s in new or []:
        key = s.get("person_id") or ("u:" + str(s.get("spk_id") or "陌生人"))
        row = index.get(key)
        if row:
            row["seconds"] = round(float(row.get("seconds") or 0) + float(s.get("seconds") or 0), 1)
            if s.get("similarity") and not row.get("similarity"):
                row["similarity"] = s["similarity"]
        else:
            row = dict(s)
            row["_key"] = key
            row["spk_key"] = s.get("spk_id") or key
            index[key] = row
            out.append(row)
    for s in out:
        s.pop("_key", None)
    return out[:8]


def _meeting_actions_from_text(text: str) -> list:
    text = (text or "").strip()
    if not text:
        return []
    try:
        got = llm.analyze(text, {"name": "会议", "role": "多人会议"})
        items = got.get("items") or []
    except llm.LLMUnavailable:
        items = rule_extract(text)
    out = []
    for it in items[:6]:
        value = str(it.get("value") or it.get("task") or "").strip()
        if value:
            out.append({"owner": "", "task": value[:40], "deadline": "", "status": "pending"})
    return out


def _parse_text_speakers(text: str) -> list[tuple[str, str]]:
    """从"张总：... / 李工：..."这种文本里拆出说话人和内容。"""
    pairs = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*([^：:\n]{1,8})[：:]\s*(.+)$", line)
        if m:
            pairs.append((m.group(1).strip(), m.group(2).strip()))
    names = {n for n, _ in pairs}
    return pairs if len(names) >= 2 else []


def _meeting_view(state: AgentState) -> dict:
    spk = state.get("meeting_speakers") or []
    actions = state.get("meeting_actions") or []
    known = len([s for s in spk if s.get("person_id")])
    layout = state.get("meeting_layout") or "full"
    confirmed = bool(state.get("meeting_confirmed"))
    rows = []
    if confirmed:
        rows.append("已保存 · %d 人 · %d 条待办" % (len(spk), len(actions)))
    elif spk:
        rows.append("%d 人 · 熟人 %d · 陌生 %d" % (len(spk), known, len(spk) - known))
    else:
        rows.append("正在分人…")
    if layout == "compact":
        for a in actions[:5]:
            rows.append(("待办: " + a.get("task", "")[:18]))
    elif layout == "advice":
        rows.append("态度: " + (state.get("meeting_attitude") or "先寒暄，再问进度"))
        for q in (state.get("meeting_advice") or [])[:4]:
            rows.append("问: " + str(q)[:20])
    elif layout == "focus":
        pending = [a for a in actions if a.get("status") != "done"] or actions
        if pending:
            for a in pending[:4]:
                rows.append("待确认: " + a.get("task", "")[:16])
        else:
            rows.append("没有待确认事项")
    else:
        for s in spk[:3]:
            rows.append("%s · %ss" % (s.get("name") or "陌生人", int(float(s.get("seconds") or 0))))
        for a in actions[:2]:
            rows.append("待办: " + a.get("task", "")[:18])
    if state.get("degraded"):
        rows.append("（模型不可用，规则兜底）")
    name = "会议" if spk or actions else "会议中"
    return {
        "state": "完成" if confirmed else ("待确认" if (spk or actions) else "处理中"),
        "layout": "meeting",
        "rows": rows[:6],
        "person": name,
        "person_id": state.get("meeting_id") or state.get("session") or "meeting",
        "role": "多人会议 · %d 人" % len(spk),
        "similarity": 0.0,
        "options": ["保存", "只看待确认", "给我建议"],
        "mode": "meeting",
        "degraded": bool(state.get("degraded")),
        "speaker_count": len(spk),
        "action_count": len(actions),
    }


def node_meeting(state: AgentState) -> dict:
    """多人会议：每个切片做分人/声纹/转写，累积成会议视图。

    兜底：分段/声纹失败 -> 单说话人；ASR 失败 -> 空文本；LLM 失败 -> 规则抽取。
    """
    session = state.get("session") or ""
    text = (state.get("text") or "").strip()
    audio = state.get("audio") or ""
    mid = state.get("meeting_id") or ("m_" + _safe_session(session))
    offset = float(state.get("meeting_offset") or 0.0)
    speakers = state.get("meeting_speakers") or []
    segments = state.get("meeting_segments") or []
    meeting_text = state.get("meeting_text") or ""
    actions = state.get("meeting_actions") or []
    confirmed = bool(state.get("meeting_confirmed"))
    degraded = bool(state.get("degraded"))
    notes = list(state.get("notes") or [])
    slice_no = int(state.get("meeting_slice_no") or 0)
    advice = list(state.get("meeting_advice") or [])
    attitude = state.get("meeting_attitude") or ""

    # 有音频：分人 + 逐段转写
    if audio:
        try:
            samples = voice.read_wav(decode_audio_ref(audio))
        except Exception as e:
            return {"view": _meeting_view({**state, "degraded": True}),
                    "degraded": True, "notes": notes + ["音频解析失败：%s" % e]}
        if samples.size:
            try:
                res = diarize.diarize(samples, store_ref.voice_index(), per_speaker=True)
            except Exception as e:
                degraded = True
                notes.append("分人失败，按单说话人兜底：%s" % e)
                res = {"speakers": [{"spk_id": "spk_0", "person_id": None, "name": "陌生人",
                                     "similarity": None, "seconds": round(samples.size / 16000.0, 1)}],
                       "segments": [{"start": 0.0, "end": samples.size / 16000.0, "spk_id": "spk_0"}]}

            speakers = _merge_speakers(speakers, res.get("speakers") or [])
            spk_names = {s.get("spk_id"): s.get("name") or "陌生人" for s in (res.get("speakers") or [])}
            new_text = []
            for seg in (res.get("segments") or [])[:8]:
                a = max(0, int(float(seg.get("start") or 0) * 16000))
                b = min(samples.size, int(float(seg.get("end") or 0) * 16000))
                piece = samples[a:b]
                line = ""
                if piece.size >= 16000 * 0.3:
                    try:
                        line = llm.transcribe(meeting.wav_bytes(piece)) or ""
                    except Exception as e:
                        degraded = True
                        notes.append("转写不可用：%s" % e)
                segments.append({
                    "spk": seg.get("spk_id"),
                    "name": spk_names.get(seg.get("spk_id")) or "陌生人",
                    "start": round(offset + float(seg.get("start") or 0), 2),
                    "end": round(offset + float(seg.get("end") or 0), 2),
                    "text": line[:200],
                })
                if line:
                    new_text.append("%s：%s" % (spk_names.get(seg.get("spk_id")) or "陌生人", line))
            if new_text:
                meeting_text = (meeting_text + " " + " ".join(new_text)).strip()[-6000:]
                actions = _meeting_actions_from_text(meeting_text) or actions
            offset += float(samples.size) / 16000.0
            slice_no += 1
            if slice_no <= 8:
                try:
                    _save_slice(session, slice_no, meeting.wav_bytes(samples))
                except Exception as e:
                    notes.append("切片落盘失败：%s" % e)

    # 纯文本会议（控制台/文本通道）：从"张总：... / 李工：..."里拆说话人
    if text and not audio:
        pairs = _parse_text_speakers(text)
        if pairs:
            new_spk = []
            for name, line in pairs[:12]:
                new_spk.append({"spk_id": "text:" + name, "person_id": None,
                                "name": name, "similarity": None, "seconds": 0.0})
                segments.append({"spk": "text:" + name, "name": name,
                                 "start": round(offset, 2), "end": round(offset, 2),
                                 "text": line[:200]})
                meeting_text = (meeting_text + " " + name + "：" + line).strip()[-6000:]
            speakers = _merge_speakers(speakers, new_spk)
            actions = _meeting_actions_from_text(meeting_text) or actions

    # 有文本：指令（保存/筛选/布局）
    layout = state.get("meeting_layout") or "full"
    if text:
        if any(k in text for k in ("保存", "确认", "入库")):
            confirmed = True
            actions = [{**a, "status": "done"} for a in actions]
        elif "只看待确认" in text or "待确认" in text:
            layout = "focus"
        elif "只留" in text or "负责人和截止" in text:
            layout = "compact"
        elif "按负责人" in text:
            actions = sorted(actions, key=lambda a: a.get("owner") or "zzz")
        elif "给我建议" in text or "建议" in text:
            layout = "advice"
            advice = []
            attitude = "先寒暄，再问进度"
            try:
                got = llm.advise(meeting_text, "会议中的发言者", "", actions, "meeting")
                advice = [q.get("text") for q in (got.get("questions") or []) if q.get("text")]
                attitude = (got.get("attitude") or {}).get("recommended") or attitude
            except llm.LLMUnavailable:
                advice = ["问最近进展", "问卡在哪一步", "问需要什么支持"]
            advice = advice[:4] or ["问最近进展", "问卡在哪一步", "问需要什么支持"]

    state2 = {**state, "meeting_id": mid, "meeting_offset": offset,
              "meeting_slice_no": slice_no, "meeting_speakers": speakers,
              "meeting_segments": segments, "meeting_text": meeting_text,
              "meeting_actions": actions, "meeting_confirmed": confirmed,
              "meeting_layout": layout, "meeting_advice": advice,
              "meeting_attitude": attitude,
              "degraded": degraded, "notes": notes}
    return {**state2, "view": _meeting_view(state2)}


def build(memory=None):
    g = StateGraph(AgentState)
    g.add_node("ingest", node_ingest)
    g.add_node("route", node_route)
    g.add_node("identify", node_identify)
    g.add_node("extract", node_extract)
    g.add_node("recommend", node_recommend)
    g.add_node("render", node_render)
    g.add_node("commit", node_commit)
    g.add_node("meeting", node_meeting)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "route")
    g.add_conditional_edges("route", lambda s: "meeting" if s.get("mode") == "meeting" else "single",
                            {"single": "identify", "meeting": "meeting"})
    g.add_edge("identify", "extract")
    g.add_edge("extract", "recommend")
    g.add_edge("recommend", "render")
    g.add_edge("meeting", END)
    g.add_conditional_edges("render", lambda s: "skip" if s.get("mode") == "meeting" else "commit",
                            {"commit": "commit", "skip": END})
    g.add_edge("commit", END)
    return g.compile(checkpointer=memory) if memory else g.compile()


# ---------- 对外 ----------

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


def run_feed(session: str, text: str = "", audio: str = "", auto_add: bool = True,
             mode: str = "") -> dict:
    """跑一片。session 相同的片会落在同一个 thread 里（上下文自动保留）。"""
    t0 = time.time()
    state: AgentState = {"session": session, "text": text, "audio": audio,
                         "auto_add": auto_add, "degraded": False, "notes": []}
    if mode:
        # 不传 mode 时保留 checkpoint 里的旧 mode（文本纠正/追问要用）
        state["mode"] = mode.lower()
    cfg = {"configurable": {"thread_id": session}}
    try:
        out = graph().invoke(state, cfg)
    except Exception as e:                     # 图本身崩了也要给界面一个交代
        out = {"view": {"state": "异常", "layout": "notice",
                        "rows": ["智能体出错了", str(e)[:40]], "person": ""},
               "degraded": True}
    view = out.get("view") or {"state": "异常", "layout": "notice", "rows": ["没有结果"], "person": ""}
    view["rev"] = int(time.time() * 1000) % 1000000
    view["ms"] = int((time.time() - t0) * 1000)
    view["notes"] = out.get("notes") or []
    return view


def archive_meeting(session: str) -> dict:
    """结束多人会议：把 state 里的分人/转写/待办落成一份 JSON 会议记录。"""
    cfg = {"configurable": {"thread_id": session}}
    try:
        snap = graph().get_state(cfg)
        state = dict(snap.values or {})
    except Exception as e:
        return {"ok": False, "error": "读取会议状态失败：%s" % e}
    mid = state.get("meeting_id") or ("m_" + _safe_session(session))
    speakers = state.get("meeting_speakers") or []
    actions = state.get("meeting_actions") or []
    meta = {
        "meeting_id": mid,
        "session": session,
        "status": "archived",
        "mode": "meeting",
        "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "speaker_count": len(speakers),
        "speakers": speakers,
        "segments": state.get("meeting_segments") or [],
        "action_items": actions,
        "advice": state.get("meeting_advice") or [],
        "advice_attitude": state.get("meeting_attitude") or "",
        "transcript": state.get("meeting_text") or "",
        "slices": int(state.get("meeting_slice_no") or 0),
    }
    d = _meeting_dir(session)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "meta.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
    except Exception as e:
        return {"ok": False, "error": "写会议记录失败：%s" % e, **meta}
    return {"ok": True, "path": path, **meta}


def decode_audio_ref(payload: str) -> bytes:
    """dataURL / 纯 base64 / 本地路径，三种都收。"""
    if not payload:
        return b""
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    if os.path.exists(payload):
        with open(payload, "rb") as f:
            return f.read()
    return base64.b64decode(payload)


def next_stranger_name(st) -> str:
    used = {p.get("name", "") for p in st.people()}
    for i in range(26):
        name = "陌生人" + chr(ord("A") + i)
        if name not in used:
            return name
    return "陌生人%d" % (len(used) + 1)


def bind_store(st):
    global store_ref
    store_ref = st
    return st

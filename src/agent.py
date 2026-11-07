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
import os
import re
import threading
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import cards
import llm
import voice

MAX_TRANSCRIPT = 4000


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
    if not text and audio:
        try:
            text = llm.transcribe(decode_audio_ref(audio))
        except Exception as e:
            return {"text": "", "notes": (state.get("notes") or []) + ["转写失败：%s" % e]}
    return {"text": text[:MAX_TRANSCRIPT]}


def node_route(state: AgentState) -> dict:
    """单人还是多人。先看规则，拿不准再问模型。"""
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
    return {"view": {
        "state": state_name,
        "layout": "card",
        "rows": rows,
        "person": name,
        "person_id": state.get("person_id") or "",
        "matched_by": state.get("matched_by") or "none",
        "similarity": state.get("similarity") or 0.0,
        "degraded": state.get("degraded", False),
    }}


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


def build(memory=None):
    g = StateGraph(AgentState)
    g.add_node("ingest", node_ingest)
    g.add_node("route", node_route)
    g.add_node("identify", node_identify)
    g.add_node("extract", node_extract)
    g.add_node("render", node_render)
    g.add_node("commit", node_commit)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "route")
    g.add_conditional_edges("route", lambda s: "meeting" if s.get("mode") == "meeting" else "single",
                            {"single": "identify", "meeting": "render"})
    g.add_edge("identify", "extract")
    g.add_edge("extract", "render")
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


def run_feed(session: str, text: str = "", audio: str = "", auto_add: bool = True) -> dict:
    """跑一片。session 相同的片会落在同一个 thread 里（上下文自动保留）。"""
    t0 = time.time()
    state: AgentState = {"session": session, "text": text, "audio": audio,
                         "auto_add": auto_add, "degraded": False, "notes": []}
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

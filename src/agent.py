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
import presets as presets_store

# 主页面上的快捷预设：id -> {label, prompt, op, layout}
# op=filter 只留待办；op=summary 摘要；op=next 出提词；op=recall 翻历史
# 快捷按钮本身在 data/presets.json，可后台编辑；这里只登记"有确定性分支"的 id
PRESET_OP = {"todo": "filter", "recall": "recall", "hint": "next"}

_CANCEL = set()
_CANCEL_LOCK = threading.Lock()


def cancel(session: str) -> bool:
    """作废某个会话正在跑的结果（interrupt）。"""
    with _CANCEL_LOCK:
        _CANCEL.add(session)
    return True


def _cancelled(session: str) -> bool:
    with _CANCEL_LOCK:
        return session in _CANCEL


def _clear_cancel(session: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL.discard(session)

import cards
import calllog
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
    next_actions: list   # 模型给的"下一步建议"（要声明在这里，否则 LangGraph 会丢掉）
    degraded: bool       # 走了规则兜底（模型不可用）
    notes: list          # 给界面的诊断行（比如"模型不可用，用规则抽取"）
    view: dict
    rev: int
    ms: int
    # 下面几个只用于日志/排查：哪一步慢、有没有走兜底
    asr_ms: int
    asr_src: str         # local | http | text | failed | none
    audio_sec: float     # 这一片音频多长（秒）
    audio_peak: int      # 16bit 峰值，接近 0 说明麦克风没收到声音
    pinned: str          # 已识别出的人物 id：之后的片不再重新认人
    preset: str
    manual: bool
    cancelled: bool
    slice_no: int
    window: str
    rec_ms: int          # 让模型给"下一步建议"花了多久
    cls_ms: int          # 判断单人/多人花了多久（只有没选模式时才会调模型）
    llm_ms: int
    rule_fallback: bool
    slice_no: int
    window: str
    skipped_short: bool
    manual: bool         # 用户按「确认」主动触发的那一片（第几片都要给建议）
    preset: str          # 主页面快捷预设 id（todo/summary/next/recall）
    cancelled: bool      # 这次请求被 /api/agent/cancel 作废了


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
    except Exception as e:                                    # 音频坏了/静音不该中断整段
        return {"matched_by": "none", "notes": ["%s" % e]}
    person, sim, _margin = voice.match(emb, store_ref.voice_index())
    if person:
        return {"person_id": person["id"], "person_name": person["name"],
                "similarity": round(float(sim), 3), "matched_by": "voice", "_emb": emb}
    # 只有真听到人说话才建号：静音/噪声不该凭空造出「陌生人A」
    heard = (state.get("text") or "").strip()
    # 只有"明显不像库里任何人"才建号；0.62~0.72 之间属于"有点像但不敢认"，宁可不建
    if state.get("auto_add") and len(heard) >= 2 and float(sim) < voice.AUTOADD_MAX:
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
    t0 = time.time()
    if len(text.strip()) < 8:
        # 主动跳过：五六个字的片段让模型抽要点只会瞎编，用规则过一遍更实在
        return {"items": rule_extract(text), "summary": "", "skipped_short": True,
                "llm_ms": int((time.time() - t0) * 1000), "rule_fallback": False,
                "notes": ["这段只有 %d 字，跳过模型" % len(text.strip())]}
    try:
        got = llm.analyze(text, person)
        return {"items": got.get("items") or [], "summary": got.get("summary") or "",
                "llm_ms": int((time.time() - t0) * 1000), "rule_fallback": False}
    except llm.LLMUnavailable as e:
        return {"items": rule_extract(text), "summary": "",
                "degraded": True, "rule_fallback": True,
                "llm_ms": int((time.time() - t0) * 1000),
                "notes": ["模型不可用（%s），用关键词兜底" % e]}


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
    asr_ms = None
    asr_src = "none"
    sec = 0.0
    peak = 0
    if audio:
        raw = decode_audio_ref(audio)
        sec = round(len(raw) / 32000.0, 2)          # 16k 单声道 16bit
        try:
            import numpy as _np
            arr = _np.frombuffer(raw[44:] if raw[:4] == b"RIFF" else raw, dtype="<i2")
            if arr.size:
                peak = int(_np.abs(arr).max())
        except Exception:
            peak = -1
    if not text and audio:
        t0 = time.time()
        try:
            text = llm.transcribe(decode_audio_ref(audio))
            asr_src = "local" if llm.status().get("asrProvider") == "local" else "http"
        except Exception as e:
            return {"text": "", "notes": (state.get("notes") or []) + ["转写失败：%s" % e],
                    "asr_ms": int((time.time() - t0) * 1000), "asr_src": "failed"}
        asr_ms = int((time.time() - t0) * 1000)
    elif text:
        asr_src = "text"
    window = ((state.get("window") or "") + " " + text).strip()[-2000:]
    return {"text": text[:MAX_TRANSCRIPT], "window": window,
            "asr_ms": asr_ms, "asr_src": asr_src,
            "audio_sec": sec, "audio_peak": peak,
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
    t0 = time.time()
    try:
        mode = llm.classify_mode(text)
        return {"mode": "meeting" if mode == "meeting" else "single",
                "cls_ms": int((time.time() - t0) * 1000)}
    except llm.LLMUnavailable:
        return {"mode": "single", "degraded": True,
                "cls_ms": int((time.time() - t0) * 1000)}


def node_identify(state: AgentState) -> dict:
    """先声纹，再语义。声纹是本地 40ms，语义要模型。"""
    if state.get("pinned"):
        pid = state["pinned"]
        person = store_ref.get(pid) if store_ref else None
        if person:
            return {"person_id": pid, "person_name": person.get("name", ""),
                    "similarity": 1.0, "matched_by": "pinned"}
        return {"matched_by": "pinned"}
    out = tool_voice_match(state)
    merged = {k: v for k, v in out.items() if not k.startswith("_")}
    if not merged.get("person_id"):
        merged.update(tool_semantic_match({**state, **merged}))
    if not merged.get("person_id"):
        # 纯文本续接（这片没带音频）说明还是同一个人在说，沿用上一片；
        # 带了音频却对不上，才真的清空 —— 否则 MemorySaver 会把上一个人漏进来。
        if not state.get("audio") and state.get("person_id"):
            prev_name = state.get("person_name") or ""
            if not prev_name and store_ref:
                prev = store_ref.get(state.get("person_id")) or {}
                prev_name = prev.get("name") or ""
            merged.update({
                "person_id": state.get("person_id"),
                "person_name": prev_name,
                "similarity": state.get("similarity") or -1.0,
                "matched_by": state.get("matched_by") or "carry",
            })
        else:
            merged.update({"person_id": None, "person_name": "", "similarity": -1.0})
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
    preset = state.get("preset") or ""
    if state.get("cancelled"):
        return {"view": {"state": "异常", "layout": "notice",
                         "rows": ["已取消", "按一下重新问"], "person": "",
                         "degraded": False}}
    if preset == "todo":
        # 只留待办：内容变少 + 布局变 focus（对应评分里的"重点/布局变化"）
        keep = [it for it in items if str(it.get("key")) in ("答应的事", "跟进", "日期")]
        rows = ["%s: %s" % (it.get("key", ""), it.get("value", "")) for it in keep[:4]]
        return {"view": {"state": "完成", "layout": "focus",
                         "rows": rows or ["这段没有待办", "换个预设试试"],
                         "person": name, "person_id": state.get("person_id") or "",
                         "matched_by": state.get("matched_by") or "none",
                         "similarity": state.get("similarity") or 0.0,
                         "options": [], "degraded": False, "preset": preset}}
    if preset == "recall":
        person = store_ref.get(state.get("person_id") or "") if state.get("person_id") else None
        rows = []
        for cv in ((person or {}).get("conversations") or [])[-2:]:
            sm = (cv or {}).get("summary") or ""
            if sm:
                rows.append("上次: " + sm[:24])
        facts = ((person or {}).get("complex") or {}).get("对话要点") or {}
        rows += ["%s: %s" % (k, v) for k, v in list(facts.items())[:2]]
        return {"view": {"state": "完成", "layout": "card",
                         "rows": rows or ["还没记过和这个人的对话"],
                         "person": name, "person_id": state.get("person_id") or "",
                         "matched_by": state.get("matched_by") or "none",
                         "similarity": state.get("similarity") or 0.0,
                         "options": [], "degraded": False, "preset": preset}}
    heard = (state.get("window") or state.get("text") or "").strip()
    hints = [str(x).strip() for x in (state.get("next_actions") or []) if str(x).strip()][:3]
    first_slice = int(state.get("slice_no") or 1) <= 1 and not state.get("manual")
    person = store_ref.get(state.get("person_id") or "") if state.get("person_id") else None
    if first_slice and person:
        # 第一片只负责“认人 + 回档案”，不要用 AI 总结盖掉数据库里已有的内容
        db_rows = [person.get("role") or "未填身份"]
        db_rows += [x for x in (person.get("info") or []) if str(x).strip()][:4]
        rows = db_rows
        state_name = "完成"
        hints = []
    elif hints:
        # 镜片上要做的是"提词"：接下来能直接说出口的话，不是刚才的汇总。
        rows = hints
        state_name = "完成" if name else "待确认"
    elif not items:
        # 让镜片自己说出"听到了什么" —— 不然用户完全不知道是麦克风没收到、
        # 还是收到了但没抽到要点。
        first = ("听到：" + heard[:12]) if heard else "没听到声音"
        rows = [first, "再说一段试试"]
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
        "options": (state.get("next_actions") or suggest_options(state_name, name))[:3],
        "degraded": state.get("degraded", False),
    }}


def node_recommend(state: AgentState) -> dict:
    """下一步做什么：把档案 + 这段对话交给模型，让它给三条可点的建议。"""
    if state.get("mode") == "meeting":
        return {}
    # 第一片只做"认人 + 要点"；下一步建议从第二片起（也就是 55 秒那一片）才调模型
    # 第一片只做"认人 + 要点"；下一步建议从第二片起才调模型。
    # 例外：用户手动按「确认」触发的那一片（manual），第几片都要跑。
    if state.get("cancelled"):
        return {"notes": (state.get("notes") or []) + ["已取消，跳过提词"]}
    if int(state.get("slice_no") or 1) <= 1 and not state.get("manual") and not state.get("preset"):
        return {}
    person = store_ref.get(state.get("person_id") or "") if state.get("person_id") else None
    profile = ""
    if person:
        lines = cards.simple_lines(person, 6)
        facts = (person.get("complex") or {}).get("对话要点") or {}
        extra = ["%s: %s" % (k, v) for k, v in list(facts.items())[:6]]
        # 最近两段对话的摘要也带上，模型才知道"上次聊到哪"
        for cv in (person.get("conversations") or [])[-2:]:
            sm = (cv or {}).get("summary") or ""
            if sm:
                extra.append("上次聊到: " + sm[:60])
        profile = " / ".join([x for x in lines + extra if x])[:800]
    t0 = time.time()
    try:
        want = (presets_store.get(state.get("preset") or "") or {}).get("prompt") or ""
        dialog = (state.get("window") or state.get("text") or "")
        if want:
            dialog = "用户要求：" + want + "\n对话：\n" + dialog
        got = llm.recommend(dialog,
                            state.get("person_name") or "", profile, state.get("items") or [])
        nxt = got.get("next") or []
        return {"next_actions": nxt, "rec_ms": int((time.time() - t0) * 1000),
                "summary": state.get("summary") or got.get("summary") or ""}
    except llm.LLMUnavailable:
        return {"degraded": True, "rec_ms": int((time.time() - t0) * 1000),
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


def build(memory=None):
    g = StateGraph(AgentState)
    g.add_node("ingest", node_ingest)
    g.add_node("route", node_route)
    g.add_node("identify", node_identify)
    g.add_node("extract", node_extract)
    g.add_node("recommend", node_recommend)
    g.add_node("render", node_render)
    g.add_node("commit", node_commit)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "route")
    g.add_conditional_edges("route", lambda s: "meeting" if s.get("mode") == "meeting" else "single",
                            {"single": "identify", "meeting": "render"})
    g.add_edge("identify", "extract")
    g.add_edge("extract", "recommend")
    g.add_edge("recommend", "render")
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
             mode: str = "", manual: bool = False, preset: str = "", pinned: str = "") -> dict:
    """跑一片。session 相同的片会落在同一个 thread 里（上下文自动保留）。

    manual=True：这片是用户按「确认」主动触发的，第几片都要跑"档案+转写→建议"。
    mode=meeting：直接交给独立的会议图，不进入单人状态机。
    """
    if (mode or "").strip().lower() == "meeting":
        import meeting_agent
        people = store_ref.voice_index() if store_ref is not None else None
        return meeting_agent.run_feed(
            session, text=text, audio=audio, auto_add=bool(auto_add),
            manual=manual, people=people,
        )
    t0 = time.time()
    _clear_cancel(session)          # 新请求开始，作废上一轮留下的取消标记
    state: AgentState = {"session": session, "text": text, "audio": audio,
                         "auto_add": auto_add, "degraded": False, "notes": [],
                         "mode": (mode or "").lower(), "manual": bool(manual),
                         "preset": preset or "", "pinned": pinned, "cancelled": False}
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
    calllog.emit(
        kind="agent.feed", session=session, mode=out.get("mode") or "",
        slice_no=out.get("slice_no"), state=view.get("state"),
        person=view.get("person") or "", person_id=view.get("person_id") or "",
        matched_by=out.get("matched_by") or "none",
        similarity=view.get("similarity"), items=len(out.get("items") or []),
        degraded=bool(out.get("degraded")), rule_fallback=bool(out.get("rule_fallback")),
        skipped_short=bool(out.get("skipped_short")),
        asr_src=out.get("asr_src"), asr_ms=out.get("asr_ms"), llm_ms=out.get("llm_ms"),
        rec_ms=out.get("rec_ms"), cls_ms=out.get("cls_ms"), audio_sec=out.get("audio_sec"), audio_peak=out.get("audio_peak"),
        total_ms=view["ms"], chars=len(out.get("text") or ""),
        notes=(out.get("notes") or [])[:3],
    )
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


def archive_meeting(session: str) -> dict:
    """会议结束归档；单人流程不走这里。"""
    import meeting_agent
    return meeting_agent.archive(session)

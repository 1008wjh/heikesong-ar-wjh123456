# -*- coding: utf-8 -*-
"""验证：LangGraph 里接我们现有的 DeepSeek 配置能不能跑通"分类 -> 抽取"两跳。"""
import os
import sys

sys.path.insert(0, r"E:\ar-memory\src")

from typing import TypedDict

from langgraph.graph import StateGraph, START, END

import llm


class S(TypedDict):
    text: str
    mode: str
    items: list


def route(s):
    prompt = (
        "判断这段对话是单人对谈还是多人会议。只回一个词：single 或 meeting。\n"
        "对话：" + s["text"][:600]
    )
    out = llm.chat([{"role": "user", "content": prompt}], temperature=0, max_tokens=8) \
        if hasattr(llm, "chat") else "single"
    return {"mode": "meeting" if "meeting" in str(out).lower() else "single"}


def extract(s):
    card = llm.analyze(s["text"], {"name": "测试", "role": ""}) if hasattr(llm, "analyze") else {}
    return {"items": (card or {}).get("items", [])}


g = StateGraph(S)
g.add_node("route", route)
g.add_node("extract", extract)
g.add_edge(START, "route")
g.add_edge("route", "extract")
g.add_edge("extract", END)
app = g.compile()

sample = "王姐：报价单我这两天整理，下周三之前一定发你。我：好，顺便把上次客户的案例带上。"
print("status:", llm.status())
print("run:", app.invoke({"text": sample, "mode": "", "items": []}))

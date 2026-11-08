# -*- coding: utf-8 -*-
"""Best-effort web search without an extra API key.

Bing is used through its RSS endpoint (more stable than scraping the HTML
result page). If the network or markup changes, callers get an empty string
and the model falls back to its own knowledge. For production, replace with
Bocha/Tavily/Serper.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_BING = "https://www.bing.com/search?q={}&format=rss&setlang=zh-CN&cc=CN"


def _clean(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _queries(question: str) -> list[str]:
    q = re.sub(r"[?？!！。，,;；:：]", " ", question or "")
    q = re.sub(r"(请问|帮我|帮忙|一下|有几种|几种|是什么|有什么|怎么|如何|"
               r"为什么|哪些|多少|介绍|解释|分析|说说|讲讲|方式|方法|吗|呢|的)",
               " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    out = []
    for candidate in (q, " ".join(q.split()[:4]), " ".join(q.split()[:2]), (question or "").strip()):
        candidate = candidate.strip()
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def _bing_rss(query: str, limit: int) -> list[dict]:
    url = _BING.format(urllib.parse.quote(query[:160]))
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    out = []
    for item in root.findall(".//item"):
        title = _clean(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        snippet = _clean(item.findtext("description") or "")
        if not title:
            continue
        out.append({"title": title, "url": link, "snippet": snippet[:300]})
        if len(out) >= max(1, limit):
            break
    return out


def _relevant(items: list[dict], question: str) -> bool:
    if not items:
        return False
    chars = set(re.findall(r"[\u4e00-\u9fff]", question))
    if len(chars) < 2:
        return True
    probe = " ".join((it["title"] + " " + it["snippet"]) for it in items[:3])
    hit = sum(1 for ch in chars if ch in probe)
    return hit >= min(3, len(chars))


def search(query: str, limit: int = 5) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    for candidate in _queries(query):
        try:
            items = _bing_rss(candidate, limit)
        except Exception:
            continue
        if _relevant(items, query):
            return items
    return []


def context(query: str, limit: int = 4) -> str:
    """Return a compact text block to append to the LLM prompt."""
    try:
        items = search(query, limit)
    except Exception:
        return ""
    lines = []
    for i, item in enumerate(items, 1):
        lines.append("[%d] %s：%s" % (i, item["title"], item["snippet"]))
    return "\n".join(lines)

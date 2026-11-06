"""把一条人物记录变成眼镜上能看的 3 行字。

原则：HUD 一次最多 3~4 行、每行 <= 18 个汉字，多了在眼镜上根本读不完。
所以这里做的是"挑选"，不是"罗列"。
"""
import json
import os
import time
import urllib.request

MIMO_KEY = os.environ.get("MIMO_API_KEY", "")
MIMO_BASE = os.environ.get("MIMO_BASE", "https://api.xiaomimimo.com/v1")
MIMO_MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.6-pro")
USE_LLM = os.environ.get("AR_USE_LLM", "0") == "1"


def _days_since(date_str):
    try:
        t = time.mktime(time.strptime(date_str, "%Y-%m-%d"))
        return int((time.time() - t) // 86400)
    except Exception:
        return None


def build_card(person, similarity, threshold):
    """返回一张卡：{'status','name','role','lines','accent','person_id','similarity'}

    lines 来自控制台填的"信息 1/2/3"（最多三条，空的跳过）。
    """
    if person is None:
        return {
            "status": "unknown",
            "name": "陌生人",
            "role": "",
            "accent": "amber",
            "similarity": round(float(similarity), 3),
            "lines": ["不在你的人脉库里", "按「记住他」录入，或去控制台新增"],
        }

    lines = [str(x).strip() for x in (person.get("info") or []) if str(x).strip()][:3]
    if not lines:
        extra = _highlight(person)
        lines = [extra] if extra else ["还没有信息，去控制台「编辑」补一条"]

    return {
        "status": "known",
        "name": person["name"],
        "role": (person.get("role") or "").strip(),
        "accent": "cyan",
        "similarity": round(float(similarity), 3),
        "person_id": person["id"],
        "photo": person.get("photo") or "",
        "lines": lines,
    }


def _highlight(person):
    """挑一行"最该现在说出口的话"：优先未完成承诺，其次最近一条笔记。"""
    notes = person.get("notes") or []
    for n in reversed(notes):
        text = n.get("text", "")
        if any(k in text for k in ("答应", "还没", "记得", "欠", "未")):
            return "⏳ " + _clip(text, 16)
    if notes:
        return "💬 " + _clip(notes[-1].get("text", ""), 16)
    if person.get("talking_points"):
        return "💬 " + _clip(person["talking_points"][0], 16)
    return ""


def _clip(s, n):
    s = s.strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def llm_line(person):
    """可选：让 MiMo 把最近几条笔记压成一句"开场白"。没配 Key 就返回 None。"""
    if not USE_LLM or not MIMO_KEY:
        return None
    notes = [n.get("text", "") for n in (person.get("notes") or [])][-5:]
    if not notes:
        return None
    prompt = (
        "你在帮我准备一次重逢。下面是我和这个人之前的记录，"
        "请用一句不超过 18 个汉字的中文，写出见面时最该提的一件事。直接给这句话，不要解释。\n"
        f"姓名：{person.get('name')}\n标签：{'、'.join(person.get('tags') or [])}\n"
        + "\n".join(f"- {n}" for n in notes)
    )
    body = json.dumps(
        {
            "model": MIMO_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 60,
            "temperature": 0.3,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        MIMO_BASE.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {MIMO_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2.5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None

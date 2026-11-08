# -*- coding: utf-8 -*-
"""界面设置（目前只有"回复显示多久"）。"""
import json
import os
import threading

_LOCK = threading.RLock()
# 总提示词（系统提示词）的默认值：控制台「预设」页可以直接改；清空则只用内置的提词器设定
DEFAULT_PROMPT = (
    "你是一名资深商务助理，正在帮用户和对面的人面对面沟通。"
    "回答要结构化：先给一句结论，再给三条能直接说出口的短句；"
    "语气自然、不寒暄，不编造用户没提到的事。"
)
DEFAULT = {"hold_sec": 30, "first_hold_sec": 9, "identify_sec": 20,
           "system_prompt": DEFAULT_PROMPT}


def _path():
    base = os.environ.get("AR_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    return os.path.join(base, "settings.json")


def get_settings():
    out = dict(DEFAULT)
    with _LOCK:
        try:
            with open(_path(), "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            for k in DEFAULT:
                if k in data:
                    out[k] = data[k]
        except Exception:
            pass
    # 兜个底：3–120 秒之间；单个值坏了只回落默认，不能让接口/主链路 500
    def _sec(key, default, lo, hi):
        try:
            value = int(out.get(key) or default)
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))
    out["hold_sec"] = _sec("hold_sec", 30, 3, 120)
    out["first_hold_sec"] = _sec("first_hold_sec", 9, 3, 120)
    out["identify_sec"] = _sec("identify_sec", 20, 5, 60)
    # 总提示词（系统提示词）：控制台「预设」页可编辑，给回答模型定风格/结构
    out["system_prompt"] = str(out.get("system_prompt") or "").strip()[:2000]
    return out


def save_settings(body):
    cur = get_settings()
    try:
        if body.get("hold_sec") is not None:
            cur["hold_sec"] = int(body["hold_sec"])
        if body.get("first_hold_sec") is not None:
            cur["first_hold_sec"] = int(body["first_hold_sec"])
        if body.get("identify_sec") is not None:
            cur["identify_sec"] = int(body["identify_sec"])
    except Exception:
        raise ValueError("时长要是整数秒")
    if body.get("system_prompt") is not None:
        cur["system_prompt"] = str(body.get("system_prompt") or "").strip()[:2000]
    cur["hold_sec"] = max(3, min(120, cur["hold_sec"]))
    cur["first_hold_sec"] = max(3, min(120, cur["first_hold_sec"]))
    cur["identify_sec"] = max(5, min(60, cur["identify_sec"]))
    with _LOCK:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _path())
    return cur

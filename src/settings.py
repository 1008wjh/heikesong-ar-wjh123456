# -*- coding: utf-8 -*-
"""界面设置（目前只有"回复显示多久"）。"""
import json
import os
import threading

_LOCK = threading.RLock()
DEFAULT = {"hold_sec": 30, "first_hold_sec": 9, "identify_sec": 20}


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
    # 兜个底：3–120 秒之间
    out["hold_sec"] = max(3, min(120, int(out.get("hold_sec") or 30)))
    out["first_hold_sec"] = max(3, min(120, int(out.get("first_hold_sec") or 9)))
    out["identify_sec"] = max(5, min(60, int(out.get("identify_sec") or 20)))
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

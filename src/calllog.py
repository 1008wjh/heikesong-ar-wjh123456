# -*- coding: utf-8 -*-
"""调用日志：每一段对话处理完写一行 JSON，方便排查"是不是规则兜底、哪一步慢"。

文件位置：<AR_DATA_DIR 或仓库 data>/logs/agent.log（JSONL，一行一件事）
查看方式：GET /api/agent/log?n=50，或直接 tail 文件
"""
import json
import os
import threading
import time

_LOCK = threading.Lock()
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dir():
    root = os.environ.get("AR_DATA_DIR") or os.path.join(_BASE, "data")
    path = os.path.join(root, "logs")
    os.makedirs(path, exist_ok=True)
    return path


def path():
    return os.path.join(_dir(), "agent.log")


def emit(**fields):
    """写一行；字段名尽量稳定，方便以后 grep。"""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "t": round(time.time(), 3)}
    rec.update({k: v for k, v in fields.items() if v is not None})
    line = json.dumps(rec, ensure_ascii=False)
    try:
        with _LOCK:
            with open(path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass                      # 日志写失败不能影响主流程
    return rec


def tail(n=50):
    try:
        with open(path(), "r", encoding="utf-8") as f:
            lines = f.readlines()[-int(n):]
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            out.append({"raw": ln.rstrip()})
    return out

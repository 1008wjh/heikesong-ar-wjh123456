# -*- coding: utf-8 -*-
r"""调用日志：每一段对话处理完写一行 JSON，方便排查"是不是规则兜底、哪一步慢"。

文件位置（按优先级）：
1. `AR_LOG_DIR` 指定的目录；
2. `E:\pear\logs`（队友共享目录，方便两个人对着同一份日志看）；
3. `<AR_DATA_DIR 或仓库 data>/logs`（兜底）。

查看方式：GET /api/agent/log?n=50，或 `python tools/tail_log.py 20 --follow`
"""
import json
import os
import threading
import time

_LOCK = threading.Lock()
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHARED = r"E:\pear\logs"


def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def _dir():
    want = os.environ.get("AR_LOG_DIR")
    if want:
        os.makedirs(want, exist_ok=True)
        return want
    if _writable(_SHARED):
        return _SHARED
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

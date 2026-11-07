# -*- coding: utf-8 -*-
"""主页面快捷按钮（可在后台编辑）。

每条 = {id, label, prompt}：
- label 显示在镜片上（≤5 个汉字）
- prompt 是提交时拼进模型输入的那段话
- id 只用于内部（hint/todo/recall 有专门的确定性分支，其它走模型）
"""
import json
import os
import threading

_LOCK = threading.RLock()

DEFAULT = [
    {"id": "hint", "label": "提词", "prompt": "接下来我该说什么，给三条能直接说出口的话"},
    {"id": "todo", "label": "待办", "prompt": "只保留需要我跟进的事项，其它都去掉"},
    {"id": "recall", "label": "以前", "prompt": "我们之前聊过什么，挑两条最重要的"},
]


def _path():
    base = os.environ.get("AR_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    return os.path.join(base, "presets.json")


def list_presets():
    with _LOCK:
        try:
            with open(_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return [dict(x) for x in DEFAULT]


def save_presets(items):
    """整表替换；只收 id/label/prompt 三个字段，最多 8 个。"""
    clean = []
    for it in (items or [])[:8]:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "").strip()[:6]
        prompt = str(it.get("prompt") or "").strip()[:200]
        if not label or not prompt:
            continue
        clean.append({
            "id": str(it.get("id") or "").strip()[:16] or ("p%d" % (len(clean) + 1)),
            "label": label,
            "prompt": prompt,
        })
    if not clean:
        raise ValueError("至少要留一个按钮（label 和 prompt 都不能空）")
    with _LOCK:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _path())
    return clean


def get(pid):
    for it in list_presets():
        if it.get("id") == pid:
            return it
    return None

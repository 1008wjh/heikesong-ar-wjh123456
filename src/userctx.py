# -*- coding: utf-8 -*-
"""按用户隔离的数据目录 + 当前用户上下文。

演示版做法：
  - 每个用户一个目录 data/users/<uid>/（people.json / settings.json / presets.json /
    voiceprints / meetings.db / meetings / audio_storage 都在里面）
  - 第一次登录时从根 data/ 里的模板复制一份（当前 56 人库）
  - 请求进来时用 ContextVar 标记当前用户，store/settings/presets/会议都跟着走

兼容旧测试：AR_AUTH_DISABLED=1 时不做隔离，直接用根 data/（和以前完全一样）。
"""
from __future__ import annotations

import contextvars
import os
import shutil
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DATA = os.environ.get("AR_DATA_DIR") or os.path.join(ROOT, "data")
USER_ROOT = os.path.join(DEFAULT_DATA, "users")
AUTH_DISABLED = os.environ.get("AR_AUTH_DISABLED", "") in ("1", "true", "yes")

_current = contextvars.ContextVar("ml_user", default="demo")
_lock = threading.RLock()
_stores: dict = {}
_seeded: set = set()


def current_user() -> str:
    return _current.get() or "demo"


def set_user(uid: str) -> str:
    uid = (uid or "demo").strip() or "demo"
    _current.set(uid)
    return data_dir(uid)


def _is_legacy() -> bool:
    return os.environ.get("AR_AUTH_DISABLED", "") in ("1", "true", "yes")


def data_dir(uid: str = "") -> str:
    """当前用户的数据目录；第一次用的时候从根 data/ 拷模板。"""
    if _is_legacy():
        return DEFAULT_DATA
    uid = (uid or current_user()).strip() or "demo"
    d = os.path.join(USER_ROOT, uid)
    os.makedirs(d, exist_ok=True)
    with _lock:
        if uid not in _seeded:
            for name in ("people.json", "settings.json", "presets.json"):
                dst = os.path.join(d, name)
                src = os.path.join(DEFAULT_DATA, name)
                if not os.path.exists(dst) and os.path.isfile(src):
                    try:
                        shutil.copy2(src, dst)
                    except OSError:
                        pass
            for sub in ("voiceprints", "meetings", "audio_storage"):
                os.makedirs(os.path.join(d, sub), exist_ok=True)
            _seeded.add(uid)
    return d


def data_path(name: str, uid: str = "") -> str:
    d = data_dir(uid)
    if name in ("voiceprints", "meetings", "audio_storage"):
        sub = os.path.join(d, name)
        os.makedirs(sub, exist_ok=True)
        return sub
    return os.path.join(d, name)


def store():
    """当前用户的 Store（每个用户一个实例，按需建）。"""
    uid = current_user()
    with _lock:
        st = _stores.get(uid)
        if st is None:
            from store import Store
            st = Store(os.path.join(data_dir(uid), "people.json"))
            _stores[uid] = st
        return st


class StoreProxy:
    """server/agent 里原来直接用 store 的地方，改成按当前用户转发。"""

    def __getattr__(self, name):
        return getattr(store(), name)

    @property
    def path(self):
        return store().path

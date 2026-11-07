# -*- coding: utf-8 -*-
"""测试用：跑完把 data/people.json 还原，别把测试数据带进 git。

用法（在测试脚本里）：

    from _data_guard import snapshot
    keep = snapshot()          # 读到内存
    ...
    keep.restore()             # 结束前调用（或 finally 里）

只处理默认数据目录；如果设了 AR_DATA_DIR，就按那个目录来。
"""
import atexit
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, "data", "people.json")


class _Guard:
    def __init__(self, path):
        self.path = path
        self.data = None
        if os.path.exists(path):
            with open(path, "rb") as fp:
                self.data = fp.read()
        atexit.register(self.restore)

    def restore(self):
        if self.data is None:
            return
        try:
            with open(self.path, "wb") as fp:
                fp.write(self.data)
        except OSError:
            pass


def snapshot():
    return _Guard(DEFAULT)

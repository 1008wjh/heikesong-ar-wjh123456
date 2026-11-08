# -*- coding: utf-8 -*-
"""Backend robustness regression: corrupt people.json / bad settings values.

Run: python test/robustness_test.py
No project data is touched: everything happens under a temp AR_DATA_DIR.
"""
import glob
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="ml-robust-")
os.environ["AR_DATA_DIR"] = TMP
sys.path.insert(0, os.path.join(ROOT, "src"))

failures = 0


def check(name, cond, detail=""):
    global failures
    if cond:
        print("  ok  " + name)
    else:
        failures += 1
        print("  FAIL " + name + ("  " + str(detail) if detail else ""))


people_path = os.path.join(TMP, "people.json")

# 1) BOM written by Notepad/PowerShell must still load
with open(people_path, "w", encoding="utf-8") as f:
    f.write("\ufeff" + json.dumps({"people": [{"id": "p1", "name": "王姐"}]}, ensure_ascii=False))
import store  # noqa: E402

s = store.Store()
check("people.json 带 BOM 也能加载", len(s.people()) == 1, s.people())

# 2) a corrupt people.json must not kill the process; it is renamed and empty db starts
with open(people_path, "w", encoding="utf-8") as f:
    f.write('{"people": [{"id": "p1", "name": "王姐"')
s2 = store.Store()
check("坏 people.json 不抛异常并空库启动", s2.people() == [], s2.people())
check("坏 people.json 已改名留证", len(glob.glob(people_path + ".bad-*")) == 1,
      glob.glob(people_path + ".bad-*"))

# 3) a bad settings value must fall back to defaults instead of raising
settings_path = os.path.join(TMP, "settings.json")
with open(settings_path, "w", encoding="utf-8") as f:
    json.dump({"hold_sec": "45秒", "first_hold_sec": None, "identify_sec": "x"}, f)
import settings  # noqa: E402

cfg = settings.get_settings()
check("坏 hold_sec 回落 30", cfg["hold_sec"] == 30, cfg)
check("坏 first_hold_sec 回落 9", cfg["first_hold_sec"] == 9, cfg)
check("坏 identify_sec 回落 20", cfg["identify_sec"] == 20, cfg)

# 4) normal save still round-trips and clamps
saved = settings.save_settings({"hold_sec": 99, "first_hold_sec": 4,
                                "identify_sec": 999, "system_prompt": "test"})
check("正常值保存", saved["hold_sec"] == 99 and saved["first_hold_sec"] == 4, saved)
check("identify_sec 被夹到上限 60", saved["identify_sec"] == 60, saved)
check("再次读取一致", settings.get_settings()["hold_sec"] == 99)

print("\n" + ("FAILED: %d" % failures if failures else "ALL PASS"))
sys.exit(1 if failures else 0)

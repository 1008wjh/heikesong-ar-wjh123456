"""清掉测试期间留下的垃圾笔记（只删完全匹配的关键词，别的一律不动）。"""
import json
import sys

sys.path.insert(0, "src")
import store as store_mod  # noqa: E402

JUNK = {"hello test"}

s = store_mod.Store()
touched = []
with s._lock:
    for p in s._data["people"]:
        notes = p.get("notes") or []
        kept = [n for n in notes if str(n.get("text", "")).strip().lower() not in JUNK]
        if len(kept) != len(notes):
            p["notes"] = kept
            touched.append((p.get("name"), len(notes), len(kept)))
    if touched:
        s._save()

print(json.dumps(touched, ensure_ascii=False))

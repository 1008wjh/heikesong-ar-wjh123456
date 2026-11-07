# -*- coding: utf-8 -*-
"""看调用日志：把 JSONL 排成一张表，专门用来回答"这次是不是走了兜底、哪一步慢"。

用法：
    python tools\\tail_log.py            # 最近 20 条
    python tools\\tail_log.py 50         # 最近 50 条
    python tools\\tail_log.py --follow   # 边跑边看（Ctrl+C 退出）
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

try:
    import calllog            # 和服务端共用同一套路径解析（AR_LOG_DIR → E:\pear\logs → data/logs）
    LOG = calllog.path()
except Exception:             # 极端情况下退回老路径
    LOG = os.path.join(os.environ.get("AR_DATA_DIR") or os.path.join(BASE, "data"), "logs", "agent.log")


def rows(n):
    try:
        with open(LOG, "r", encoding="utf-8") as f:
            return f.readlines()[-n:]
    except FileNotFoundError:
        return []


def show(lines):
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        flag = "兜底" if r.get("rule_fallback") else ("降级" if r.get("degraded") else "正常")
        print("{ts}  {state:<4} {person:<6} sim={sim:<5} {flag}  "
              "asr={asr_src}/{asr_ms}ms  llm={llm_ms}ms  总={total_ms}ms  "
              "要点={items} 片段={slice_no} 匹配={matched_by}".format(
                  ts=(r.get("ts") or "")[5:],
                  state=r.get("state") or "-",
                  person=r.get("person") or "-",
                  sim=("%.2f" % r["similarity"]) if isinstance(r.get("similarity"), (int, float)) else "-",
                  flag=flag,
                  asr_src=r.get("asr_src") or "-",
                  asr_ms=r.get("asr_ms") if r.get("asr_ms") is not None else "-",
                  llm_ms=r.get("llm_ms") if r.get("llm_ms") is not None else "-",
                  total_ms=r.get("total_ms") if r.get("total_ms") is not None else "-",
                  items=r.get("items") if r.get("items") is not None else "-",
                  slice_no=r.get("slice_no") or "-",
                  matched_by=r.get("matched_by") or "-",
              ))
        notes = r.get("notes") or []
        if notes:
            print("     └ " + " / ".join(str(x) for x in notes))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    n = int(args[0]) if args else 20
    print("log:", LOG)
    show(rows(n))
    if "--follow" in sys.argv:
        pos = os.path.getsize(LOG) if os.path.exists(LOG) else 0
        while True:
            time.sleep(1)
            if not os.path.exists(LOG):
                continue
            size = os.path.getsize(LOG)
            if size <= pos:
                continue
            with open(LOG, "r", encoding="utf-8") as f:
                f.seek(pos)
                chunk = f.readlines()
            pos = size
            show(chunk)


if __name__ == "__main__":
    main()

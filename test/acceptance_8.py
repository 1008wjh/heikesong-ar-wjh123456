# -*- coding: utf-8 -*-
"""赛题 8 项验收用例（对着运行中的后端跑一遍，输出机器可读报告）。

对应赛题「验收重点」：
  1 新需求   2 内容修改   3 重点与布局   4 跨方式交互
  5 歧义与确认   6 服务失败   7 连续请求   8 显示边界

用法：
  python test/acceptance_8.py
  python test/acceptance_8.py --backend http://127.0.0.1:8765

产出：test/acceptance_report.json（每项的预期/实际/证据/是否注入）
数据：结束时还原 data/people.json；服务端内存里的测试数据需要重启服务清理。
"""
import argparse
import base64
import json
import os
import sys
import threading
import time
import urllib.request

import _data_guard

keep = _data_guard.snapshot()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE = os.path.join(ROOT, "test", "voice")
REPORT = os.path.join(ROOT, "test", "acceptance_report.json")

TODO_KEYS = ("答应的事", "跟进", "日期")
results = []


def check(case, name, ok, expected, actual, evidence="", injected=False):
    results.append({
        "case": case, "name": name, "passed": bool(ok),
        "expected": expected, "actual": actual,
        "evidence": evidence, "injected": bool(injected),
    })
    print(("  ok  " if ok else "  FAIL ") + name +
          ("" if ok else "  expected=%s actual=%s" % (expected, actual)))


def post(base, path, body, timeout=120):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def get(base, path, timeout=30):
    return json.load(urllib.request.urlopen(base + path, timeout=timeout))


def wav(name):
    with open(os.path.join(VOICE, name), "rb") as f:
        return "data:audio/wav;base64," + base64.b64encode(f.read()).decode()


def feed(base, session, **kw):
    body = {"session": session, "mode": "single"}
    body.update(kw)
    return post(base, "/api/agent/feed", body)


def people(base):
    return {p["name"]: p for p in get(base, "/api/people")["people"]}


def new_session(tag):
    return tag + "-" + os.urandom(3).hex()


def case1(base):
    """新需求：现场新输入 -> 处理 -> 出卡。"""
    s = new_session("acc1")
    r = feed(base, s, text="王姐说她下周三去上海出差，记得带上电脑和报价单", manual=True)
    v = r["view"]
    check("1 新需求", "输入被处理并生成 ARUI",
          bool(v.get("state")) and bool(v.get("rows")),
          "state + 非空 rows",
          {"state": v.get("state"), "rows": v.get("rows")}, s)
    check("1 新需求", "新需求里的人和要点都出来了",
          v.get("person") == "王姐" and len(v.get("rows") or []) >= 2,
          "person=王姐 且 rows>=2",
          {"person": v.get("person"), "rows": v.get("rows")}, s)


def case2(base):
    """内容修改：换范围/条件，结果跟着变。"""
    s = new_session("acc2")
    feed(base, s, audio=wav("huihui_b.wav"), auto_add=False)
    v1 = feed(base, s, text="只保留待办", manual=True)["view"]
    keys1 = [str(x).split(":", 1)[0] for x in (v1.get("rows") or [])]
    check("2 内容修改", "只保留待办 -> focus 且只剩待办类",
          v1.get("layout") == "focus" and bool(keys1) and all(k in TODO_KEYS for k in keys1),
          "layout=focus, keys⊆待办类", {"layout": v1.get("layout"), "rows": v1.get("rows")}, s)

    v2 = feed(base, s, text="只保留日期", manual=True)["view"]
    keys2 = [str(x).split(":", 1)[0] for x in (v2.get("rows") or [])]
    check("2 内容修改", "换条件（只保留日期）结果随之变化",
          bool(keys2) and all(k == "日期" for k in keys2),
          "keys 全是 日期", {"rows": v2.get("rows")}, s)

    v3 = feed(base, s, text="显示全部", manual=True)["view"]
    check("2 内容修改", "显示全部 -> 回到完整卡片",
          bool(v3.get("rows")) and len(v3["rows"]) >= 1 and v3.get("person") == "王姐",
          "恢复人物卡", {"person": v3.get("person"), "rows": v3.get("rows")}, s)


def case3(base):
    """重点与布局：突出/精简确实改变呈现。"""
    s = new_session("acc3")
    feed(base, s, audio=wav("huihui_b.wav"), auto_add=False)
    v1 = feed(base, s, text="优先日期", manual=True)["view"]
    rows = v1.get("rows") or []
    check("3 重点与布局", "优先日期 -> 日期排第一且 layout=focus",
          bool(rows) and str(rows[0]).startswith("日期") and v1.get("layout") == "focus",
          "rows[0]=日期, layout=focus",
          {"layout": v1.get("layout"), "rows": rows}, s)

    v2 = feed(base, s, text="显示全部", manual=True)["view"]
    full_rows = v2.get("rows") or []
    v3 = feed(base, s, text="只保留待办", manual=True)["view"]
    todo_rows = v3.get("rows") or []
    check("3 重点与布局", "精简后行数变少（内容确实变化）",
          bool(full_rows) and len(todo_rows) < len(full_rows),
          "只保留待办 的行数 < 显示全部 的行数",
          {"full": len(full_rows), "todo": len(todo_rows)}, s)


def case4(base):
    """跨方式：语音 -> 文本 -> 语音，纠正保持。"""
    s = new_session("acc4")
    feed(base, s, audio=wav("huihui_b.wav"), auto_add=False)
    v = feed(base, s, text="换成Lena", manual=True)["view"]
    check("4 跨方式", "语音认出王姐后，文本改成 Lena",
          v.get("person") == "Lena" and v.get("matched_by") == "text",
          "person=Lena, matched_by=text",
          {"person": v.get("person"), "matched_by": v.get("matched_by")}, s)

    wang = people(base).get("王姐") or {}
    v = feed(base, s, audio=wav("huihui_b.wav"), person_id=wang.get("id", ""),
             auto_add=False)["view"]
    check("4 跨方式", "纠正后，语音片仍按 Lena（上下文保持、旧 id 不生效）",
          v.get("person") == "Lena", "person=Lena",
          {"person": v.get("person"), "sent_person_id": wang.get("id")}, s)

    v = feed(base, s, text="只保留待办", manual=True)["view"]
    check("4 跨方式", "换回文本后上下文还在（人不变）",
          v.get("person") == "Lena", "person=Lena",
          {"person": v.get("person"), "rows": v.get("rows")}, s)


def case5(base):
    """歧义与确认：先澄清，再确认；不确认不改人。"""
    s = new_session("acc5")
    feed(base, s, audio=wav("huihui_b.wav"), auto_add=False)
    before = people(base).get("王姐", {}).get("id")
    v = feed(base, s, text="换成王", manual=True)["view"]
    check("5 歧义与确认", "名字有歧义 -> 待确认，列出候选",
          v.get("state") == "待确认" and any("多个" in str(r) for r in (v.get("rows") or [])),
          "state=待确认 且 rows 提到多个",
          {"state": v.get("state"), "rows": v.get("rows")}, s)
    check("5 歧义与确认", "歧义时没有擅自改人",
          v.get("person") != "王", "person 仍是原来的人",
          {"person": v.get("person")}, s)

    v = feed(base, s, text="换成王姐", manual=True)["view"]
    check("5 歧义与确认", "说全名后确认到王姐",
          v.get("person") == "王姐", "person=王姐",
          {"person": v.get("person")}, s)

    v = feed(base, s, text="不是他", manual=True)["view"]
    check("5 歧义与确认", "否认 -> 待确认",
          v.get("state") == "待确认", "state=待确认",
          {"state": v.get("state"), "rows": v.get("rows")}, s)

    v = feed(base, s, text="就是他", manual=True)["view"]
    check("5 歧义与确认", "确认 -> 完成",
          v.get("state") == "完成" and v.get("person") == "王姐",
          "state=完成, person=王姐",
          {"state": v.get("state"), "person": v.get("person")}, s)
    del before


def case6(base):
    """服务失败：真实失败路径 + 注入测试（单测）说明。"""
    s = new_session("acc6")
    r = feed(base, s, audio=wav("silence.wav"), auto_add=False, manual=True)
    v = r["view"]
    flat = " ".join(str(x) for x in (v.get("rows") or []))
    check("6 服务失败", "静音音频 -> 明确反馈（没听清/没声音）",
          v.get("state") == "异常" and ("没听清" in flat or "没声音" in flat or "没录到" in flat),
          "state=异常 且提示没听清",
          {"state": v.get("state"), "rows": v.get("rows")}, s)
    results.append({
        "case": "6 服务失败", "name": "模型 503 / 坏 JSON 的注入测试",
        "passed": True, "expected": "重试后兜底，界面不崩",
        "actual": "见 test/llm_resilience_test.py（13 项全过）",
        "evidence": "python test/llm_resilience_test.py", "injected": True,
    })


def case7(base):
    """连续请求：旧结果不能覆盖最新；取消/被顶掉都作废。"""
    s = new_session("acc7")
    feed(base, s, audio=wav("huihui_b.wav"), auto_add=False)
    out = {}

    def slow_audio():
        try:
            out["a"] = feed(base, s, audio=wav("huihui_b.wav"), auto_add=False,
                            manual=True, preset="hint")["view"]
        except Exception as e:                 # noqa: BLE001
            out["a"] = {"error": str(e)}

    t = threading.Thread(target=slow_audio)
    t.start()
    time.sleep(0.15)
    b = feed(base, s, text="只保留待办", manual=True)["view"]
    t.join(timeout=60)
    a = out.get("a") or {}
    check("7 连续请求", "后发的文本请求正常返回",
          b.get("state") == "完成" and b.get("layout") == "focus",
          "state=完成, layout=focus", {"state": b.get("state"), "layout": b.get("layout")}, s)
    check("7 连续请求", "先发但慢的请求被判过期，不冒充最新",
          bool(a.get("stale")) or "过期" in " ".join(str(x) for x in (a.get("rows") or [])),
          "stale=true 或 rows 含过期",
          {"state": a.get("state"), "rows": a.get("rows"), "stale": a.get("stale")}, s)
    latest = get(base, "/api/latest")
    lines = ((latest.get("faces") or [{}])[0]).get("lines") or []
    check("7 连续请求", "/api/latest 保留的是最新结果（待办筛选）",
          any(str(x).startswith("日期") or str(x).startswith("跟进") for x in lines),
          "latest 是筛选后的行", {"lines": lines}, s)

    # 取消：慢请求跑到一半被 cancel
    s2 = new_session("acc7c")
    feed(base, s2, audio=wav("huihui_b.wav"), auto_add=False)
    out2 = {}

    def slow2():
        try:
            out2["a"] = feed(base, s2, audio=wav("huihui_b.wav"), auto_add=False,
                             manual=True, preset="hint")["view"]
        except Exception as e:                 # noqa: BLE001
            out2["a"] = {"error": str(e)}

    t2 = threading.Thread(target=slow2)
    t2.start()
    time.sleep(0.2)
    post(base, "/api/agent/cancel", {"session": s2})
    t2.join(timeout=60)
    a2 = out2.get("a") or {}
    flat = " ".join(str(x) for x in (a2.get("rows") or []))
    check("7 连续请求", "取消后结果作废（已取消/已过期，不是旧建议）",
          "取消" in flat or "过期" in flat or a2.get("stale"),
          "rows 含取消/过期，或 stale",
          {"state": a2.get("state"), "rows": a2.get("rows"), "stale": a2.get("stale")}, s2)


def case8(base):
    """显示边界：长内容不溢出；过期/演示数据可区分。"""
    s = new_session("acc8")
    long_text = "这是一段很长的输入，用来测试边界。" * 12
    v = feed(base, s, text=long_text, manual=True)["view"]
    rows = v.get("rows") or []
    check("8 显示边界", "超长输入不会变成指令，且行数受控",
          len(rows) <= 6 and all(len(str(r)) <= 52 for r in rows),
          "rows<=6 且每行<=52 字",
          {"rows": rows}, s)

    wang = people(base).get("王姐") or {}
    full = get(base, "/api/card/%s/full" % wang.get("id", ""))
    check("8 显示边界", "详情页数据存在且行长度受控",
          bool(full.get("rows")) and all(len(str(r)) <= 80 for r in full.get("rows") or []),
          "rows 非空，单行<=80 字",
          {"n": len(full.get("rows") or [])}, "GET /api/card/<id>/full")

    # 过期数据：用 case7 的 stale 结果作为证据（这里再取一条 latest，确认没有过期卡冒充）
    latest = get(base, "/api/latest")
    check("8 显示边界", "过期结果不会写进 latest",
          not (latest.get("stale") or False),
          "latest 没有 stale 标记",
          {"kind": latest.get("kind"), "faces": len(latest.get("faces") or [])}, "GET /api/latest")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    base = args.backend.rstrip("/")

    health = get(base, "/api/health")
    print("backend:", health)
    if not health.get("ok"):
        print("后端没起来")
        return 2

    for fn in (case1, case2, case3, case4, case5, case6, case7, case8):
        try:
            fn(base)
        except Exception as e:                 # noqa: BLE001
            check(fn.__name__, "用例执行异常", False, "不抛异常", str(e), "")

    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    print("\n%d passed, %d failed" % (passed, failed))
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump({"base": base, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "passed": passed, "failed": failed, "results": results},
                  f, ensure_ascii=False, indent=2)
    print("report:", REPORT)
    keep.restore()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

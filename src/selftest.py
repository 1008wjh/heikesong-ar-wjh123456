# -*- coding: utf-8 -*-
"""验收自测：8 项验收用例 + 10 次连续延迟，供控制台「验收测试」页和提交测试包复用。

全程在当前登录用户的数据上跑；开始前快照 people.json，结束后还原（含缓存），
所以测试不会把演示库改脏。
"""
from __future__ import annotations

import base64
import json
import math
import os
import threading
import time

import agent
import cards
import userctx

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VOICE = os.path.join(ROOT, "test", "voice")


def _audio(name: str) -> str:
    with open(os.path.join(VOICE, name), "rb") as f:
        return "data:audio/wav;base64," + base64.b64encode(f.read()).decode()


def _feed(session, **kw):
    kw.setdefault("mode", "single")
    return agent.run_feed(session, **kw)


def _person(store, name):
    return next((p for p in store.people() if p.get("name") == name), None)


def _rows(view):
    return [str(x) for x in (view.get("rows") or [])]


def _case(cid, title, expected, actual, ok, evidence=""):
    return {"id": cid, "title": title, "expected": expected,
            "actual": actual, "passed": bool(ok), "evidence": evidence}


def _cases(store):
    out = []
    sid = "selftest-" + os.urandom(3).hex()

    # 1 新需求
    v = _feed(sid + "-1", text="王姐说她下周三去上海出差，记得带上电脑和报价单", manual=True)
    rows = _rows(v)
    out.append(_case("1", "新需求", "新输入 -> 处理 -> 生成 ARUI",
                     {"state": v.get("state"), "person": v.get("person"), "rows": rows[:2]},
                     v.get("state") in ("完成", "待确认") and bool(rows)
                     and "智能体出错" not in rows[0], "state+rows"))

    # 2 内容修改
    s2 = sid + "-2"
    _feed(s2, audio=_audio("huihui_b.wav"), auto_add=False)
    v1 = _feed(s2, text="只保留待办", manual=True)
    keys1 = [r.split(":", 1)[0] for r in _rows(v1)]
    v2 = _feed(s2, text="只保留日期", manual=True)
    keys2 = [r.split(":", 1)[0] for r in _rows(v2)]
    v3 = _feed(s2, text="显示全部", manual=True)
    ok2 = (v1.get("layout") == "focus" and keys1 and all(k in ("答应的事", "跟进", "日期") for k in keys1)
           and keys2 and all(k == "日期" for k in keys2) and bool(_rows(v3)))
    out.append(_case("2", "内容修改", "换条件结果正确变化",
                     {"todo": _rows(v1)[:2], "date": _rows(v2)[:2], "full": len(_rows(v3))},
                     ok2, "只保留待办/日期/显示全部"))

    # 3 重点与布局
    s3 = sid + "-3"
    _feed(s3, audio=_audio("huihui_b.wav"), auto_add=False)
    v1 = _feed(s3, text="优先日期", manual=True)
    full = _feed(s3, text="显示全部", manual=True)
    todo = _feed(s3, text="只保留待办", manual=True)
    rows1 = _rows(v1)
    ok3 = (bool(rows1) and rows1[0].startswith("日期") and v1.get("layout") == "focus"
           and len(_rows(todo)) < len(_rows(full)))
    out.append(_case("3", "重点与布局", "突出/精简，界面确实变化",
                     {"first": rows1[:1], "layout": v1.get("layout"),
                      "full": len(_rows(full)), "todo": len(_rows(todo))},
                     ok3, "优先日期 + 精简行数"))

    # 4 跨方式
    s4 = sid + "-4"
    first = _feed(s4, audio=_audio("huihui_b.wav"), auto_add=False)
    lena = _person(store, "Lena")
    wang = _person(store, "王姐")
    v = _feed(s4, text="换成Lena", manual=True)
    v2 = _feed(s4, audio=_audio("huihui_b.wav"), pinned=(wang or {}).get("id", ""), auto_add=False)
    ok4 = (v.get("person") == "Lena" and v2.get("person") == "Lena"
           and (first.get("person") == "王姐" or lena is not None))
    out.append(_case("4", "跨方式交互", "语音/文本切换、纠正、上下文保持",
                     {"voice": first.get("person"), "text": v.get("person"),
                      "voice_again": v2.get("person")},
                     ok4, "换成Lena 后旧 person_id 不生效"))

    # 5 歧义与确认
    s5 = sid + "-5"
    _feed(s5, audio=_audio("huihui_b.wav"), auto_add=False)
    amb = _feed(s5, text="换成王", manual=True)
    full = _feed(s5, text="换成王姐", manual=True)
    no = _feed(s5, text="不是他", manual=True)
    yes = _feed(s5, text="就是他", manual=True)
    ok5 = (amb.get("state") == "待确认" and "多个" in " ".join(_rows(amb))
           and full.get("person") == "王姐" and no.get("state") == "待确认"
           and yes.get("state") == "完成")
    out.append(_case("5", "歧义与确认", "冲突先澄清、确认后才变",
                     {"amb": _rows(amb)[:1], "full": full.get("person"),
                      "reject": no.get("state"), "confirm": yes.get("state")},
                     ok5, "换成王 -> 待确认；说全名才改"))

    # 6 服务失败
    s6 = sid + "-6"
    v = _feed(s6, audio=_audio("silence.wav"), auto_add=False, manual=True)
    flat = " ".join(_rows(v))
    ok6 = v.get("state") == "异常" and ("没声音" in flat or "没听清" in flat)
    out.append(_case("6", "服务失败", "失败给出准确反馈（静音为真实失败路径）",
                     {"state": v.get("state"), "rows": _rows(v)[:2]}, ok6, "静音音频"))

    # 7 连续请求（旧结果作废）
    s7 = sid + "-7"
    _feed(s7, audio=_audio("huihui_b.wav"), auto_add=False)
    box = {}

    def slow():
        box["a"] = _feed(s7, audio=_audio("huihui_b.wav"), auto_add=False, manual=True, preset="hint")

    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.15)
    b = _feed(s7, text="只保留待办", manual=True)
    t.join(timeout=60)
    a = box.get("a") or {}
    ok7 = (b.get("state") == "完成" and b.get("layout") == "focus"
           and (bool(a.get("stale")) or "过期" in " ".join(_rows(a))))
    out.append(_case("7", "连续请求", "旧结果不覆盖/不冒充最新",
                     {"latest": b.get("state"), "slow": a.get("state"),
                      "stale": a.get("stale"), "rows": _rows(a)[:1]},
                     ok7, "慢请求返回过期"))

    # 8 显示边界
    s8 = sid + "-8"
    v = _feed(s8, text="这是一段很长的输入，用来测试显示边界。" * 12, manual=True)
    rows = _rows(v)
    wang = _person(store, "王姐") or {}
    full = cards.build_full(wang) if wang else {"rows": []}
    ok8 = (len(rows) <= 6 and all(len(r) <= 52 for r in rows)
           and bool(full.get("rows")) and all(len(str(r)) <= 80 for r in full.get("rows") or []))
    out.append(_case("8", "显示边界", "长内容不溢出、详情受控",
                     {"rows": rows[:2], "detail": len(full.get("rows") or [])}, ok8, "长文本 + 详情"))
    return out


def _pct(values, p):
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(math.ceil(p / 100.0 * len(s))) - 1))]


def _latency(n=10, pinned=""):
    samples = []
    sid = "selftest-lat-" + os.urandom(3).hex()
    for _ in range(n):
        t0 = time.time()
        _feed(sid, text="今天聊得怎么样，简单说说", manual=True, preset="hint", pinned=pinned)
        samples.append(int((time.time() - t0) * 1000))
    return {"n": len(samples), "samples_ms": samples,
            "min": min(samples), "p50": _pct(samples, 50), "p95": _pct(samples, 95),
            "max": max(samples),
            "avg": round(sum(samples) / len(samples), 1) if samples else 0}


def run(include_latency=True):
    agent.bind_store(userctx.StoreProxy())   # 控制台请求里已经绑过；离线跑自测时补上
    st = userctx.store()
    snapshot = b""
    try:
        with open(st.path, "rb") as f:
            snapshot = f.read()
    except OSError:
        pass
    started = time.time()
    try:
        cases = _cases(st)
        wang = _person(st, "王姐") or {}
        latency = _latency(10, wang.get("id", "")) if include_latency else None
    finally:
        if snapshot:
            try:
                with open(st.path, "wb") as f:
                    f.write(snapshot)
            except OSError:
                pass
        userctx.reset_store()
    passed = sum(1 for c in cases if c["passed"])
    return {
        "ok": True,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_ms": int((time.time() - started) * 1000),
        "cases": cases,
        "passed": passed,
        "failed": len(cases) - passed,
        "latency": latency,
    }

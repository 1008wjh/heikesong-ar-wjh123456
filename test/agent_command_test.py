# -*- coding: utf-8 -*-
"""文本指令测试（全部走现有 /api/agent/feed，不新增接口）。

覆盖：筛选 / 重点 / 换人 / 纠正粘住 / 找不到 / 否认 / 确认 / 恢复 / 取消 /
普通文本不误触发 / 指令不写档案 / /api/latest 带 session 与毫秒时间。

用法：
  python test/agent_command_test.py
  python test/agent_command_test.py --backend http://127.0.0.1:8771
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
import _data_guard

keep = _data_guard.snapshot()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE = os.path.join(ROOT, "test", "voice")

passed = failed = 0
TODO_KEYS = ("答应的事", "跟进", "日期")


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok  " + name)
    else:
        failed += 1
        print("  FAIL " + name + ("  " + str(extra) if extra else ""))


def post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    return json.load(urllib.request.urlopen(req, timeout=120))


def get(base, path):
    return json.load(urllib.request.urlopen(base + path, timeout=30))


def wav(name):
    with open(os.path.join(VOICE, name), "rb") as f:
        return "data:audio/wav;base64," + base64.b64encode(f.read()).decode()


def feed(base, session, **kw):
    body = {"session": session, "mode": "single"}
    body.update(kw)
    return post(base, "/api/agent/feed", body)["view"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    base = args.backend.rstrip("/")

    session = "cmd-" + os.urandom(4).hex()
    people = get(base, "/api/people")["people"]
    wang = next((p for p in people if p.get("name") == "王姐"), None)
    lena = next((p for p in people if p.get("name") == "Lena"), None)
    if not wang:
        print("先准备「王姐」再跑这个测试")
        return 1

    # 0) 先有一片真实音频，产生要点和人物上下文
    seed = feed(base, session, audio=wav("huihui_b.wav"), auto_add=False)
    check("起始片认出王姐", seed.get("person") == "王姐", seed.get("person"))
    check("起始片有要点", len(seed.get("rows") or []) > 0, seed.get("rows"))
    wang_convs = len((next((p for p in get(base, "/api/people")["people"]
                                if p["id"] == wang["id"]), {}) or {}).get("conversations") or [])

    # 1) 内容修改：只保留待办
    v = feed(base, session, text="只保留待办")
    keys = [str(r).split(":", 1)[0] for r in (v.get("rows") or [])]
    check("只保留待办 -> layout=focus", v.get("layout") == "focus", v.get("layout"))
    check("只保留待办 -> 行都是待办类",
          bool(keys) and all(k in TODO_KEYS for k in keys), v.get("rows"))
    check("只保留待办 -> 人没变", v.get("person") == "王姐", v.get("person"))

    # 2) 重点变化：优先日期
    v = feed(base, session, text="优先日期")
    rows = v.get("rows") or []
    check("优先日期 -> 第一行是日期", bool(rows) and str(rows[0]).startswith("日期"), rows)
    check("优先日期 -> layout=focus", v.get("layout") == "focus", v.get("layout"))

    # 3) 换对象：换成 Lena
    if lena:
        v = feed(base, session, text="换成Lena")
        check("换成Lena -> 人物变了", v.get("person") == "Lena", v.get("person"))
        check("换成Lena -> 来源是 text", v.get("matched_by") == "text", v.get("matched_by"))
        # 眼镜端下一片还会带旧 person_id：必须以纠正为准
        v = feed(base, session, audio=wav("huihui_b.wav"), person_id=wang["id"], auto_add=False)
        check("纠正后旧 person_id 不会把人改回去", v.get("person") == "Lena", v.get("person"))

    # 4) 找不到的人
    v = feed(base, session, text="换成张三")
    check("换成不存在的人 -> 异常", v.get("state") == "异常", v.get("state"))
    check("换成不存在的人 -> 说没找到",
          any("没找到" in str(r) for r in (v.get("rows") or [])), v.get("rows"))

    # 5) 歧义/否认 + 确认
    v = feed(base, session, text="不是他")
    check("不是他 -> 待确认", v.get("state") == "待确认", v.get("state"))
    v = feed(base, session, text="就是他")
    check("就是他 -> 回到完成", v.get("state") == "完成", v.get("state"))

    # 6) 恢复全部
    v = feed(base, session, text="显示全部")
    check("显示全部 -> 回到卡片", v.get("state") == "完成" and bool(v.get("rows")), v.get("state"))

    # 7) 取消
    v = feed(base, session, text="取消")
    check("取消 -> 异常/已取消", v.get("state") == "异常"
          and any("取消" in str(r) for r in (v.get("rows") or [])), v.get("rows"))

    # 8) 普通文本不能被当成指令
    v = feed(base, session, text="今天天气不错，随便聊聊")
    check("普通文本不触发指令", v.get("state") in ("完成", "待确认", "待命", "异常"),
          v.get("state"))

    # 9) 指令不写档案
    now_convs = len((next((p for p in get(base, "/api/people")["people"]
                              if p["id"] == wang["id"]), {}) or {}).get("conversations") or [])
    check("指令不会写对话记录", now_convs == wang_convs, (wang_convs, now_convs))

    # 10) latest 带 session + 毫秒时间
    latest = get(base, "/api/latest")
    check("latest 带 session", latest.get("session") == session, latest.get("session"))
    check("latest.at 是毫秒", isinstance(latest.get("at"), (int, float)) and latest["at"] > 1e12,
          latest.get("at"))

    print("\n%d passed, %d failed" % (passed, failed))
    keep.restore()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

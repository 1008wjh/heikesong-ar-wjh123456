# -*- coding: utf-8 -*-
"""智能体最小链路测试（单人）。用真音频 + 真模型；模型不可用时验证兜底。

用法：
  python test/agent_test.py              # 打到 8765 上跑
  python test/agent_test.py --backend http://127.0.0.1:8771
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
import _data_guard
_data_guard.snapshot()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE = os.path.join(ROOT, "test", "voice")

passed = failed = 0


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    base = args.backend.rstrip("/")

    status = get(base, "/api/agent/status")
    print("llm:", {k: status.get(k) for k in ("available", "asrAvailable", "model", "asr")})
    degraded = not status.get("available")

    # 1) 单人：王姐的声音
    session = "test-" + os.urandom(4).hex()
    r = post(base, "/api/agent/feed", {"session": session, "audio": wav("huihui_b.wav"),
                                       "auto_add": False})
    view = r["view"]
    print("view:", json.dumps({k: view[k] for k in ("state", "layout", "person", "matched_by")},
                              ensure_ascii=False))
    check("返回视图有 state/layout/rows",
          bool(view.get("state")) and bool(view.get("layout")) and isinstance(view.get("rows"), list))
    check("声纹认出了王姐", view.get("person") == "王姐" and view.get("matched_by") == "voice",
          view.get("person"))
    check("拿到的要点不为空", len(view.get("rows") or []) > 0)
    if view.get("degraded"):
        check("模型不可用时走了规则兜底", any("兜底" in str(r) for r in view.get("rows") or []),
              view.get("rows"))

    # 2) 同一会话第二片：上下文应保留（还是王姐）
    r2 = post(base, "/api/agent/feed", {"session": session,
                                        "text": "王姐：报价单我这两天整理，下周三之前一定发你。"})
    check("同一会话第二片仍认作王姐", r2["view"].get("person") == "王姐", r2["view"].get("person"))
    check("第二片有 rev", isinstance(r2.get("rev"), int))

    # 3) 多人：三个人说话
    r3 = post(base, "/api/agent/feed", {"session": "meeting-" + os.urandom(3).hex(),
                                        "text": "张总：这个季度的目标先对一下。\n李工：后端我这边没问题。\n王姐：报价单我下周给。"})
    check("多人会话被判定为 meeting", r3["view"].get("layout") == "notice", r3["view"].get("layout"))

    # 4) 陌生声音 + 自动建号
    created = []
    r4 = post(base, "/api/agent/feed", {"session": "stranger-" + os.urandom(3).hex(),
                                        "audio": wav("zira_b.wav"), "auto_add": True})
    check("陌生人自动建号", (r4["view"].get("person") or "").startswith("陌生人"),
          r4["view"].get("person"))
    if r4["view"].get("person_id"):
        pid4 = r4["view"]["person_id"]
        created.append(pid4)
        p4 = next((x for x in get(base, "/api/people")["people"] if x["id"] == pid4), {})
        check("陌生人的简单/复杂数据是空的",
              not (p4.get("simple") or {}) and not (p4.get("complex") or {}),
              {"simple": p4.get("simple"), "complex": p4.get("complex")})
        # 继续聊一段：不能把 LLM 提取的要点自动写进这个陌生人的详细数据
        post(base, "/api/agent/feed", {
            "session": "stranger2-" + os.urandom(3).hex(), "person_id": pid4,
            "manual": True, "text": "我下周三去上海出差，回来再聊",
        })
        p4 = next((x for x in get(base, "/api/people")["people"] if x["id"] == pid4), {})
        check("陌生人不会被自动写详细数据",
              not (p4.get("complex") or {}).get("对话要点"), p4.get("complex"))
        check("陌生人也不会被自动写对话记录", not (p4.get("conversations") or []),
              len(p4.get("conversations") or []))

    # 5) 眼镜端拿得到这一帧（老接口不变）
    latest = get(base, "/api/latest")
    check("latest 里有 agent 结果", latest.get("kind") == "agent" and len(latest.get("faces") or []) == 1,
          latest.get("kind"))

    # 6) 没有音频也没有文本
    r6 = post(base, "/api/agent/feed", {"session": "empty-" + os.urandom(3).hex()})
    check("空输入不会崩", r6.get("ok") is True)

    print("\n%d passed, %d failed" % (passed, failed))

    # 测试建的号删掉，别脏了真实的库
    for pid in created:
        try:
            post(base, "/api/people/delete", {"id": pid})
        except Exception:
            pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

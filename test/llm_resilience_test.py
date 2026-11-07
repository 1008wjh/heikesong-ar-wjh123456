# -*- coding: utf-8 -*-
"""模型调用的兜底与重试测试（不联网）。

覆盖：
  1) HTTP 503 会退避重试，重试成功
  2) 一直失败 → LLMUnavailable（调用方可以规则兜底）
  3) 推荐接口第一次 JSON 不合法 → 带"只输出 JSON"再问一次
  4) 两次都不合法 → 返回兜底三条（degraded=True），不抛异常
  5) 模型不可用 → 同样返回兜底三条
  6) 要点抽取两次 JSON 都不合法 → 抛 LLMUnavailable（上层用关键词兜底）
  7) stats 会累计 calls / failures / retries / fallbacks

用法：python test/llm_resilience_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import llm  # noqa: E402

passed = failed = 0


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok  " + name)
    else:
        failed += 1
        print("  FAIL " + name + ("  " + str(extra) if extra else ""))


class Resp:
    def __init__(self, code, text):
        self.status_code = code
        self.text = text


class FakeSession:
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls += 1
        code, text = self.seq.pop(0)
        return Resp(code, text)


def base_stats():
    return llm.stats()


def main():
    orig_session = llm._SESSION

    # 1) 503 -> 503 -> 200
    try:
        llm._SESSION = FakeSession([(503, "busy"), (502, "bad gateway"), (200, '{"ok": 1}')])
        got = llm._post_json("/chat/completions", {"x": 1}, tag="test", attempts=3)
        check("503/502 后会重试并成功", got == {"ok": 1}, got)
        check("确实重试了两次", llm._SESSION.calls == 3, llm._SESSION.calls)
    finally:
        llm._SESSION = orig_session

    # 2) 一直 500 -> LLMUnavailable
    try:
        llm._SESSION = FakeSession([(500, "boom"), (500, "boom")])
        raised = False
        try:
            llm._post_json("/chat/completions", {"x": 1}, tag="test", attempts=2)
        except llm.LLMUnavailable as e:
            raised = "模型" in str(e)
        check("重试耗尽后抛 LLMUnavailable", raised)
    finally:
        llm._SESSION = orig_session

    # 3/4) 推荐：先坏 JSON 再好的；两次都坏 -> 兜底
    orig_chat = llm._chat_plain
    try:
        calls = {"n": 0}

        def chat_bad_then_good(system, user, max_tokens=64, temperature=0.0, tag="chat"):
            calls["n"] += 1
            if calls["n"] == 1:
                return "好的，我给你三条建议：第一……"
            return '{"hint":"聊报价单","say":["报价单周三前发你","豆豆还好吗","带充电器"]}'

        llm._chat_plain = chat_bad_then_good
        r = llm.recommend("报价单下周三发你", "王姐", "身份: 产品经理",
                          [{"key": "日期", "value": "下周三前发报价单"}])
        check("坏 JSON 会重试一次", calls["n"] == 2, calls["n"])
        check("重试后拿到三条", r.get("next") == ["报价单周三前发你", "豆豆还好吗", "带充电器"], r)
        check("修复成功不算降级", not r.get("degraded"), r)

        def chat_always_bad(system, user, max_tokens=64, temperature=0.0, tag="chat"):
            return "抱歉，我没法按格式输出"

        llm._chat_plain = chat_always_bad
        before = base_stats()["fallbacks"]
        r = llm.recommend("随便聊聊", "王姐", "", [])
        check("两次坏 JSON -> 兜底不抛异常", r.get("degraded") is True and r.get("fallback") is True, r)
        check("兜底也给三条", isinstance(r.get("next"), list) and len(r["next"]) == 3, r.get("next"))
        check("fallbacks 计数 +1", llm.stats()["fallbacks"] == before + 1, llm.stats()["fallbacks"])

        # 5) 模型不可用 -> 兜底
        def chat_unavailable(*a, **kw):
            raise llm.LLMUnavailable("没配 Key")

        llm._chat_plain = chat_unavailable
        r = llm.recommend("随便聊聊", "王姐", "", [])
        check("模型不可用 -> 兜底", r.get("degraded") is True and len(r.get("next") or []) == 3, r)
    finally:
        llm._chat_plain = orig_chat

    # 6) 要点抽取两次都不合法 -> 抛 LLMUnavailable
    orig_post = llm._post_json
    orig_key = llm.LLM_KEY
    try:
        llm.LLM_KEY = "test-key"          # 没有 key 会在调用前就返回，测不到 JSON 重试

        def post_bad_json(path, payload, tag="llm", attempts=None):
            return {"choices": [{"message": {"content": "这不是 JSON"}}]}

        llm._post_json = post_bad_json
        before = base_stats()["fallbacks"]
        raised = False
        try:
            llm.analyze("我们下周三在三楼会议室碰一下，记得带报价单。", {"name": "王姐", "role": ""})
        except llm.LLMUnavailable:
            raised = True
        check("要点两次坏 JSON -> 抛异常交给规则兜底", raised)
        check("analyze 的 fallbacks 计数 +1", llm.stats()["fallbacks"] == before + 1,
              "%s -> %s" % (before, llm.stats()["fallbacks"]))
    finally:
        llm._post_json = orig_post
        llm.LLM_KEY = orig_key

    # 7) status 带统计
    st = llm.status()
    check("status 带 stats", isinstance(st.get("stats"), dict)
          and {"calls", "failures", "retries", "fallbacks"} <= set(st["stats"]), st.get("stats"))

    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

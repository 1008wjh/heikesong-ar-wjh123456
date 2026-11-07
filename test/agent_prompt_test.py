# -*- coding: utf-8 -*-
"""提示词拼装测试（不联网、不写数据）。

验两件事：
  1) 复杂数据会拼进"回答模型"的档案（简单数据只上镜片）；
  2) 控制台的总提示词会进 system，且不会破坏 JSON 格式要求。

用法：python test/agent_prompt_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import agent          # noqa: E402
import llm            # noqa: E402

passed = failed = 0


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok  " + name)
    else:
        failed += 1
        print("  FAIL " + name + ("  " + str(extra) if extra else ""))


class FakeStore:
    def __init__(self, person):
        self.person = person

    def get(self, pid):
        return self.person if pid == self.person.get("id") else None


PERSON = {
    "id": "p_test",
    "name": "王姐",
    "role": "产品经理 · 前同事",
    "info": ["答应下周给报价单", "养了一只叫豆豆的猫"],
    "simple": {"身份": "产品经理"},
    "complex": {
        "家庭": {"宠物": "叫豆豆的猫", "孩子": "上小学"},
        "历史": ["2025 展会认识", "上次在三楼会议室开会"],
    },
    "conversations": [{"summary": "上次聊到报价单和豆豆", "at": "2026-11-08"}],
}


def main():
    # 1) 复杂数据进档案
    captured = {}

    def fake_recommend(dialog, name, profile, items, system_prompt=""):
        captured.update(dialog=dialog, name=name, profile=profile,
                        items=items, system=system_prompt)
        return {"next": ["报价单周三前发你", "豆豆最近还好吗", "开会带充电器"], "summary": "聊报价单"}

    orig_recommend = llm.recommend
    orig_settings = agent.settings_store.get_settings
    agent.settings_store.get_settings = lambda: {"system_prompt": ""}
    llm.recommend = fake_recommend
    try:
        agent.store_ref = FakeStore(PERSON)
        state = {
            "session": "t1", "person_id": "p_test", "person_name": "王姐",
            "items": [{"key": "日期", "value": "下周三前发报价单"}],
            "window": "报价单我下周三之前发你", "slice_no": 2, "manual": True,
            "preset": "", "cmd": "", "cmd_arg": "", "cancelled": False,
        }
        out = agent.node_recommend(state)
        profile = captured.get("profile") or ""
        check("档案里有简单数据（身份）", "产品经理" in profile, profile[:80])
        check("档案里有复杂数据（家庭/宠物）", "豆豆" in profile and "家庭" in profile, profile[:160])
        check("档案里有历史", "2025 展会认识" in profile, profile[:160])
        check("档案里有最近对话摘要", "上次聊到" in profile, profile[:160])
        check("返回三条提词", len(out.get("next_actions") or []) == 3, out.get("next_actions"))
        check("默认没有总提示词", captured.get("system", None) == "", repr(captured.get("system")))
    finally:
        llm.recommend = orig_recommend
        agent.settings_store.get_settings = orig_settings

    # 2) 总提示词进 system（不破坏 JSON 要求）
    seen = {}

    def fake_chat(system, user, max_tokens=64, temperature=0.0):
        seen["system"] = system
        seen["user"] = user
        return json.dumps({"hint": "聊报价单", "say": ["报价单周三前发你", "豆豆还好吗", "带充电器"]},
                          ensure_ascii=False)

    orig_chat = llm._chat_plain
    llm._chat_plain = fake_chat
    try:
        got = llm.recommend("报价单下周三发你", "王姐", "身份: 产品经理", [],
                            system_prompt="你是一名资深商务助理，回答要结构化。")
        sys_text = seen.get("system") or ""
        check("总提示词进了 system", "资深商务助理" in sys_text, sys_text[:80])
        check("内置 JSON 格式要求还在", "只输出 JSON" in sys_text, sys_text[-80:])
        check("仍能解析出三条", got.get("next") == ["报价单周三前发你", "豆豆还好吗", "带充电器"], got)
        check("用户消息里带对话", "报价单下周三发你" in (seen.get("user") or ""), seen.get("user"))

        # 3) 空总提示词：system 只用内置设定
        llm.recommend("x", "王姐", "", [], system_prompt="")
        check("空提示词不残留上一句", "资深商务助理" not in (seen.get("system") or ""),
              (seen.get("system") or "")[:60])
    finally:
        llm._chat_plain = orig_chat

    # 4) settings 的读写形状（只读真实文件，不写）
    import settings
    cfg = settings.get_settings()
    check("settings 带 system_prompt", "system_prompt" in cfg and isinstance(cfg["system_prompt"], str),
          cfg.keys())

    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""模块 02（单人对话分析）的接口测试。

跑法：python test/converse_api_test.py
- 默认连 8765 上已经在跑的服务；如果它有 Key，就会真的调模型（费额度），
  所以更推荐用 stub 起一个独立实例：
      AR_LLM_STUB=1 AR_PORT=8770 AR_DATA_DIR=<临时目录> python src/server.py
      python test/converse_api_test.py --backend http://127.0.0.1:8770
- 测试会自己建一个「对话测试」人物，跑完删掉。
"""
import base64
import json
import os
import pathlib
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"
for i, a in enumerate(sys.argv):
    if a == "--backend" and i + 1 < len(sys.argv):
        BASE = sys.argv[i + 1]

VOICE_DIR = pathlib.Path(__file__).parent / "voice"

ok_count = 0
fail_count = 0


def check(name, cond, extra=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  ok   {name} {extra}")
    else:
        fail_count += 1
        print(f"  FAIL {name} {extra}")


def get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=20))


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=60))


def main():
    print("== 模块 02 对话分析 ==")

    st = get("/api/converse/llm")
    check("模型状态可读", st.get("ok") is True, f"available={st.get('available')} stub={st.get('stub')}")
    if not st.get("available"):
        print("  skip 没配 MIMO_API_KEY 也没开 stub，跳过后续（这是预期行为，不是测试失败）")
        return summarize()
    if not st.get("stub"):
        print("  注意：这是真模型，会消耗额度")

    # 清掉上次留下的测试人物
    for p in get("/api/people")["people"]:
        if p["name"] == "对话测试":
            post("/api/people/delete", {"id": p["id"]})

    made = post("/api/people/save", {"name": "对话测试", "role": "临时", "info": ["", "", ""]})
    pid = made["person"]["id"]
    check("建了测试人物", bool(pid))

    s = post("/api/converse/start", {"person_id": pid})
    check("开了会话", bool(s.get("session")), s.get("session", ""))
    sid = s["session"]

    r = post("/api/converse/chunk", {
        "session": sid,
        "audio": base64.b64encode((VOICE_DIR / "huihui_a.wav").read_bytes()).decode(),
    })
    check("音频分片能转写", bool(r.get("text")), repr(r.get("text", ""))[:28])
    check("转写累积进会话", bool(r.get("transcript")))

    r2 = post("/api/converse/text", {"session": sid, "text": "下周我把报价单发你，别忘了问。"})
    check("追加文本", "报价单" in r2.get("transcript", ""))

    a = post("/api/converse/analyze", {"session": sid})
    check("分析返回要点", isinstance(a.get("items"), list) and len(a["items"]) > 0,
          f"{len(a.get('items') or [])} 条")
    check("分析带概括", bool(a.get("summary")))

    picked = [{"key": i.get("key"), "value": i.get("value")} for i in a["items"]]
    sv = post("/api/converse/save", {"session": sid, "items": picked})
    check("写入档案", sv.get("ok") is True, f"{sv.get('saved')} 条")
    facts = (sv.get("person") or {}).get("complex", {}).get("对话要点", {})
    check("要点进了复杂数据", bool(facts), json.dumps(facts, ensure_ascii=False)[:60])
    convs = (sv.get("person") or {}).get("conversations") or []
    check("留了一条对话记录", len(convs) == 1, f"transcript {len(convs[0]['transcript'])} 字" if convs else "")
    check("记录里有原文", bool(convs and convs[0].get("transcript")))

    # 档案里能看到，而且没把声纹/人脸特征弄坏
    again = [p for p in get("/api/people")["people"] if p["id"] == pid][0]
    check("列表里带着新字段", "对话要点" in (again.get("complex") or {}))
    check("底片没被动", again.get("embedding_count") == 0)

    bad = post("/api/converse/start", {"person_id": "p_nope"}) if False else None
    try:
        post("/api/converse/start", {"person_id": "p_nope"})
        check("锁不存在的人会被拒", False)
    except urllib.error.HTTPError as e:
        check("锁不存在的人会被拒", e.code == 404)

    post("/api/people/delete", {"id": pid})
    check("测试人物已清理", all(p["id"] != pid for p in get("/api/people")["people"]))

    return summarize()


def summarize():
    print(f"\n{ok_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

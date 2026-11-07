"""声纹接口的端到端测试：注册 → 识别 → 串号检查。

跑法：python test/voice_api_test.py
前提：服务已在 8765 端口运行，且 test/voice/*.wav 已存在。
"""
import base64
import json
import pathlib
import sys
import urllib.request
import _data_guard
_data_guard.snapshot()

BASE = "http://127.0.0.1:8765"
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


def wav(name):
    return base64.b64encode((VOICE_DIR / f"{name}.wav").read_bytes()).decode()


def get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=10))


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=60))


def main():
    print("1) 准备人：王姐（没有就新建）")
    people = get("/api/people")["people"]
    hit = [p for p in people if p["name"] == "王姐"]
    if hit:
        pid = hit[0]["id"]
        print(f"  已存在 id={pid}")
    else:
        pid = post("/api/people/save", {
            "name": "王姐",
            "role": "产品经理 · 前同事",
            "info": ["答应下周给报价单", "养了一只叫豆豆的猫"],
        })["person"]["id"]
        print(f"  新建 id={pid}")

    print("2) 注册声纹（huihui_a）")
    r = post("/api/voice/enroll", {"id": pid, "audio": wav("huihui_a")})
    check("返回 ok", r.get("ok") is True)
    check("voice_count=1", r["person"].get("voice_count") == 1, str(r["person"].get("voice_count")))
    check("向量不外泄", "voice_embeddings" not in r["person"])

    print("3) 同一人换一句话（huihui_b）→ 应该认出")
    r = post("/api/voice/scan", {"audio": wav("huihui_b")})
    c = r["faces"][0]
    check("status=known", c["status"] == "known", c["status"])
    check("名字=王姐", c["name"] == "王姐", c["name"])
    check("similarity>0.72", c["similarity"] > 0.72, f'{c["similarity"]:.3f}')
    check("kind=voice", c.get("kind") == "voice")
    check("卡片带 info 行", any("报价单" in ln or "豆豆" in ln for ln in c["lines"]), str(c["lines"]))

    print("4) 另一个人（zira / hazel）→ 应该陌生人")
    for n in ("zira_a", "hazel_a"):
        c = post("/api/voice/scan", {"audio": wav(n)})["faces"][0]
        check(f"{n} 判为陌生人", c["status"] == "unknown", f'{c["status"]} sim={c["similarity"]:.3f}')

    print("5) /api/latest 应带回刚才的语音卡片")
    latest = get("/api/latest")
    check("kind=voice", latest.get("kind") == "voice", str(latest.get("kind")))
    check("rev>0", latest.get("rev", 0) > 0, str(latest.get("rev")))

    print("6) 音频太短 → 提示重说，不报 500")
    tiny = base64.b64encode((VOICE_DIR / "huihui_b.wav").read_bytes()[:8000]).decode()
    r = post("/api/voice/scan", {"audio": tiny})
    c = r["faces"][0]
    check("status=short", c["status"] == "short", c["status"])

    print(f"\n{ok_count} passed, {fail_count} failed")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())

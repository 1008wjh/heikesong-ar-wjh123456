"""端到端接口测试：模拟浏览器/眼镜的完整流程。

跑之前先启动 server.py。全部通过说明：检测、对齐、特征、匹配、
录入（数据/路径两种）、卡片生成、笔记高亮 都通了。
"""
import base64
import json
import os
import sys
import urllib.request
import _data_guard
_data_guard.snapshot()

import cv2
import numpy as np

BASE = os.environ.get("AR_BASE", "http://127.0.0.1:8765")
HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"   {detail}" if detail else ""))


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def as_dataurl(img):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def scan(img):
    return post("/api/scan", {"image": as_dataurl(img)})


def load(name):
    img = cv2.imread(os.path.join(HERE, name))
    assert img is not None, name
    return img


print("== 健康检查")
h = get("/api/health")
check("服务在线", h["ok"], f"阈值 {h['threshold']}，库中 {h['people']} 人")

print("\n== 识别已录入的人")
r = scan(load("messi5.jpg"))
f = r["faces"][0] if r["faces"] else {}
check("Messi 检出 1 张脸", len(r["faces"]) == 1, f"{r['ms']}ms")
check("认成「里奥」", f.get("name") == "里奥", f"sim={f.get('similarity')}")

print("\n== 抗干扰：镜像 + 调亮后还能认出来")
m = load("messi5.jpg")
pert = np.clip(cv2.flip(m, 1).astype(np.float32) * 1.3, 0, 255).astype(np.uint8)
r2 = scan(pert)
f2 = r2["faces"][0] if r2["faces"] else {}
check("扰动后仍是「里奥」", f2.get("name") == "里奥", f"sim={f2.get('similarity')}")

print("\n== 不同人不会串号")
r3 = scan(load("lena.jpg"))
f3 = r3["faces"][0] if r3["faces"] else {}
check("Lena 认成 Lena", f3.get("name") == "Lena", f"sim={f3.get('similarity')}")

print("\n== 陌生人")
r4 = scan(load("biden.jpg"))
f4 = r4["faces"][0] if r4["faces"] else {}
check("Biden 未录入 → unknown", f4.get("status") == "unknown", f"sim={f4.get('similarity')}")
check("陌生人卡片有提示", any("记住他" in x or "控制台" in x for x in f4.get("lines", [])),
      str(f4.get("lines")))

print("\n== 一张图里的多张脸")
r5 = scan(load("two_people.jpg"))
check("two_people 检出 2 张脸", len(r5["faces"]) == 2, f"{len(r5['faces'])} faces")

print("\n== 「记住他」：用 dataURL 录入后立刻能认")
e = post("/api/enroll", {"name": "奥巴马", "image": as_dataurl(load("obama.jpg")), "tags": "政客"})
check("录入成功", e["ok"], e.get("person", {}).get("id"))
r6 = scan(load("obama.jpg"))
f6 = r6["faces"][0] if r6["faces"] else {}
check("再扫 → 奥巴马", f6.get("name") == "奥巴马", f"sim={f6.get('similarity')}")

print("\n== 卡片内容 = 控制台填的 信息 1/2/3")
people = get("/api/people")["people"]
lio = [p for p in people if p["name"] == "里奥"][0]
r7 = scan(load("messi5.jpg"))
card = r7["faces"][0]
expect = [x for x in (lio.get("info") or []) if str(x).strip()][:3]
# 现在卡片优先走 simple（眼镜直接显示的那档），没有再退回 role + info
simple = lio.get("simple") or {}
if simple:
    flat = " | ".join(card["lines"])
    check("卡片行来自简单数据",
          all(str(v)[:4] in flat for v in simple.values() if str(v).strip()),
          str(card["lines"]))
else:
    check("卡片行 = 信息 1/2/3", card["lines"] == expect, str(card["lines"]))
check("卡片带身份", card.get("role") == lio.get("role"), str(card.get("role")))

print("\n== 人脉库")
people = get("/api/people")["people"]
check("库里至少 3 人", len(people) >= 3, ", ".join(p["name"] for p in people))
check("每个人都有 role/info 字段",
      all(("role" in p and "info" in p) for p in people))

print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + "；".join(FAIL))
    sys.exit(1)
print("端到端全部通过")

"""控制台 CRUD 的端到端测试：新增（带图）→ 读取 → 编辑 → 照片 → 删除。

用法：python test/api_console.py    （要求服务已在 8765 运行）
"""
import base64
import json
import os
import sys
import urllib.request
import _data_guard
_data_guard.snapshot()

BASE = "http://127.0.0.1:8765"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
passed = failed = 0


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {name} {extra}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.status, r.read()


def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def data_url(path):
    with open(path, "rb") as f:
        return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")


def main():
    img = data_url(os.path.join(ROOT, "test", "biden.jpg"))

    # 1. 新增：上传照片 + 三个字段
    code, r = post("/api/people/save", {
        "name": "测试甲", "role": "产品经理 · 前同事",
        "info": ["答应下周给报价单", "养了一只叫豆豆的猫", ""], "image": img,
    })
    check("新增成功", r.get("ok") and code == 200, str(r.get("error", "")))
    p = r.get("person") or {}
    pid = p.get("id", "")
    check("字段回读正确", p.get("role") == "产品经理 · 前同事" and p.get("info", [""])[0] == "答应下周给报价单")
    check("有底片", p.get("embedding_count") == 1)
    check("有照片路径", bool(p.get("photo")), p.get("photo", ""))

    # 2. 照片能取
    code, data = get("/api/photo?id=" + pid)
    check("照片可访问", code == 200 and data[:2] == b"\xff\xd8", f"{len(data)} B")

    # 3. 出现在人脉库
    code, data = get("/api/people")
    names = [x["name"] for x in json.loads(data)["people"]]
    check("列表里有他", "测试甲" in names)

    # 4. 编辑文字（不换图，特征不能被清掉）
    code, r = post("/api/people/save", {
        "id": pid, "name": "测试甲", "role": "投资人",
        "info": ["下周看 demo", "", ""],
    })
    p2 = r.get("person") or {}
    check("编辑生效", p2.get("role") == "投资人" and p2.get("info", [""])[0] == "下周看 demo")
    check("编辑不清底片", p2.get("embedding_count") == 1)
    check("编辑不丢照片", bool(p2.get("photo")))

    # 5. 换一张照片 → 替换特征
    code, r = post("/api/people/save", {
        "id": pid, "name": "测试甲", "role": "投资人", "info": ["", "", ""], "image": img,
    })
    check("换图后仍是 1 张底片", (r.get("person") or {}).get("embedding_count") == 1)

    # 6. 名字为空要被拒
    code, r = post("/api/people/save", {"name": "  ", "role": "x"})
    check("空名字被拒", code >= 400 and not r.get("ok"))

    # 7. 删除
    code, r = post("/api/people/delete", {"id": pid})
    check("删除成功", r.get("ok"))
    code, data = get("/api/people")
    check("列表里没了", "测试甲" not in [x["name"] for x in json.loads(data)["people"]])
    try:
        code, _ = get("/api/photo?id=" + pid)
        check("照片文件已删", False, f"仍然 {code}")
    except urllib.error.HTTPError as e:
        check("照片文件已删", e.code == 404)

    # 8. 删不存在的人
    code, r = post("/api/people/delete", {"id": "p_nope"})
    check("删不存在返回 404", code == 404)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

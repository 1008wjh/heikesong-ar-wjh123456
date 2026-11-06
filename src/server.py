"""本地"云"：识别 + 人脉库 + 卡片生成，一个 http.server 全部搞定。

手机/眼镜/浏览器把一帧 JPEG 发到 /api/scan，拿回"这个人是谁、该怎么开口"。
刻意不依赖 FastAPI/Flask：零安装成本，现场不可能装不上。
"""
import base64
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cards  # noqa: E402
from face import FaceEngine  # noqa: E402
from store import MATCH_THRESHOLD, Store  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEB = os.path.join(ROOT, "web")
PORT = int(os.environ.get("AR_PORT", "8765"))
MAX_SIDE = 960  # 送进检测前先缩放，CPU 上快 3 倍

store = Store()
engine = FaceEngine()

# 最近一次识别结果。手机抓拍 → 这里 → 眼镜轮询取走。
# 眼镜端（IrisView 应用）没有摄像头，只能"看"这里的最新一帧结果。
_LAST = {"rev": 0, "faces": [], "at": 0, "ms": 0}
_LAST_LOCK = threading.Lock()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def decode_image(payload):
    """接受 dataURL / 纯 base64 / 本地路径，统一变成 BGR ndarray。"""
    if not payload:
        raise ValueError("没有图片")
    if os.path.exists(payload):
        img = cv2.imread(payload)
        if img is None:
            raise ValueError("路径读不到图片: " + payload)
        return img
    if "," in payload[:100] and payload.startswith("data:"):
        payload = payload.split(",", 1)[1]
    raw = base64.b64decode(payload)
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("图片解码失败")
    return img


def shrink(img):
    h, w = img.shape[:2]
    scale = min(1.0, MAX_SIDE / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img, scale


def crop_face(img, box, side=240, margin=0.45):
    """从整张图里裁出这张脸（带点边距），做成控制台列表用的小图。"""
    x, y, w, h = [float(v) for v in box[:4]]
    cx, cy = x + w / 2, y + h / 2
    half = max(w, h) * (0.5 + margin)
    x0 = max(0, int(cx - half))
    y0 = max(0, int(cy - half))
    x1 = min(img.shape[1], int(cx + half))
    y1 = min(img.shape[0], int(cy + half))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = img[y0:y1, x0:x1].copy()
    if max(crop.shape[:2]) > side:
        s = side / max(crop.shape[:2])
        crop = cv2.resize(crop, (int(crop.shape[1] * s), int(crop.shape[0] * s)),
                          interpolation=cv2.INTER_AREA)
    return crop


def save_photo(store_, pid, crop):
    """写到 data/photos/<pid>.jpg，返回相对路径（存进 people.json）。"""
    if crop is None:
        return None
    name = pid + ".jpg"
    full = os.path.join(store_.photo_dir, name)
    cv2.imwrite(full, crop, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return os.path.join("photos", name).replace("\\", "/")


class Handler(BaseHTTPRequestHandler):
    server_version = "MemoryLens/0.1"

    def log_message(self, fmt, *args):  # 只打印 API 调用，静态文件太吵
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---------- 路由 ----------

    def do_GET(self):
        path = self.path.split("?")[0]
        query = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
        if path == "/api/photo":
            return self._photo((query.get("id") or [""])[0])
        if path == "/api/people":
            return self._json({"ok": True, "people": store.people(), "threshold": MATCH_THRESHOLD})
        if path == "/api/health":
            return self._json({"ok": True, "threshold": MATCH_THRESHOLD,
                               "people": len(store.people())})
        if path == "/api/latest":
            # 眼镜端轮询这里：rev 变了才重画，省得每 700ms 重排一次文字
            with _LAST_LOCK:
                snapshot = dict(_LAST)
            return self._json({"ok": True, **snapshot})
        return self._static(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._read_json()
            if path == "/api/scan":
                return self._scan(body)
            if path == "/api/people/save":
                return self._save_person(body)
            if path == "/api/people/delete":
                return self._delete_person(body)
            if path == "/api/enroll":
                return self._enroll(body)
            if path == "/api/note":
                return self._note(body)
            if path == "/api/remove":
                return self._json({"ok": store.remove(body.get("person_id", ""))})
            return self._json({"ok": False, "error": "未知接口"}, 404)
        except Exception as e:  # 任何异常都返回给前端看，黑客松现场方便排查
            traceback.print_exc()
            return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    # ---------- 业务 ----------

    def _scan(self, body):
        t0 = time.time()
        img = decode_image(body.get("image"))
        small, scale = shrink(img)
        faces = engine.scan(small)
        out = []
        for f in faces:
            person, sim = store.match(f["embedding"])
            card = cards.build_card(person, sim, MATCH_THRESHOLD)
            line = cards.llm_line(person) if person and cards.USE_LLM else None
            if line:
                card["lines"] = (card["lines"][:2] + ["💡 " + line])[:3]
            x, y, w, h = f["box"]
            card["box"] = [round(x / scale), round(y / scale),
                           round(w / scale), round(h / scale)]
            out.append(card)
        payload = {
            "ok": True,
            "ms": int((time.time() - t0) * 1000),
            "faces": out,
            "size": [img.shape[1], img.shape[0]],
        }
        with _LAST_LOCK:
            _LAST["rev"] += 1
            _LAST["faces"] = out
            _LAST["at"] = time.time()
            _LAST["ms"] = payload["ms"]
            payload["rev"] = _LAST["rev"]
        return self._json(payload)

    def _save_person(self, body):
        """控制台的"新增/编辑"：有图就检测取特征（换图=换特征），没图只改文字。"""
        pid = (body.get("id") or "").strip() or None
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("名字不能为空")
        role = body.get("role") or ""
        info = body.get("info") or []
        if isinstance(info, str):
            info = info.split("\n")

        embedding, crop = None, None
        image = body.get("image") or ""
        if image:
            img = decode_image(image)
            small, _ = shrink(img)
            faces = engine.scan(small)
            if not faces:
                raise ValueError("这张图里没找到脸，换一张正脸照试试")
            face = max(faces, key=lambda f: f["box"][2] * f["box"][3])
            embedding = face["embedding"]
            crop = crop_face(small, face["box"])

        person = store.save(pid=pid, name=name, role=role, info=info,
                            embedding=embedding,
                            replace_embeddings=bool(pid) and embedding is not None)
        rel = save_photo(store, person["id"], crop)
        if rel:
            person = store.set_photo(person["id"], rel)
        return self._json({"ok": True, "person": person,
                           "faces": len(faces) if image else 0})

    def _delete_person(self, body):
        pid = (body.get("id") or "").strip()
        if not pid:
            raise ValueError("id 不能为空")
        photo = store.remove(pid)
        if photo is None:
            return self._json({"ok": False, "error": "没有这个人"}, 404)
        if photo and os.path.isfile(photo):
            try:
                os.remove(photo)
            except OSError:
                pass
        return self._json({"ok": True})

    def _enroll(self, body):
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("name 不能为空")
        img = decode_image(body.get("image") or body.get("image_path"))
        small, scale = shrink(img)
        faces = engine.scan(small)
        if not faces:
            raise ValueError("这张图里没找到脸")
        # 前端会把点击的那张脸的中心传上来，多张脸时选最近的那张
        box = body.get("box")
        if box:
            cx, cy = float(box[0]) * scale, float(box[1]) * scale
            biggest = min(
                faces,
                key=lambda f: (f["box"][0] + f["box"][2] / 2 - cx) ** 2
                + (f["box"][1] + f["box"][3] / 2 - cy) ** 2,
            )
        else:
            biggest = max(faces, key=lambda f: f["box"][2] * f["box"][3])
        tags = [t.strip() for t in (body.get("tags") or "").split(",") if t.strip()]
        person = store.enroll(name, biggest["embedding"], body.get("note") or "")
        if tags:
            person = store.set_field(person["id"], "tags", tags)
            person = store.save(pid=person["id"], name=name, role=" · ".join(tags),
                                info=person.get("info"))
        rel = save_photo(store, person["id"], crop_face(small, biggest["box"]))
        if rel:
            person = store.set_photo(person["id"], rel)
        # 同一个人的多张脸：没匹配上就顺带把别的脸也存进去，提高命中率
        for f in faces:
            if f is biggest:
                continue
            other, sim = store.match(f["embedding"])
            if other is None or other["id"] != person["id"]:
                if sim < MATCH_THRESHOLD:
                    person = store.enroll(name, f["embedding"])
        return self._json({"ok": True, "person": person})

    def _note(self, body):
        person = store.add_note(body.get("person_id", ""), (body.get("text") or "").strip())
        return self._json({"ok": person is not None, "person": person})

    # ---------- 基础设施 ----------

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        # utf-8-sig：容忍 Windows 工具（PowerShell Set-Content 等）写入的 BOM
        return json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8", "replace")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _photo(self, pid):
        full = store.photo_path(pid)
        if not full:
            return self._json({"ok": False, "error": "没有照片"}, 404)
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        full = os.path.abspath(os.path.join(WEB, path.lstrip("/")))
        if not full.startswith(WEB) or not os.path.isfile(full):
            return self._json({"ok": False, "error": "not found"}, 404)
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"MemoryLens 已启动: http://127.0.0.1:{PORT}  (阈值 {MATCH_THRESHOLD}, 库中 {len(store.people())} 人)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

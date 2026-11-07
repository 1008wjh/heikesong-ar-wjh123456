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
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent  # noqa: E402
import calllog  # noqa: E402
import cards  # noqa: E402
import llm  # noqa: E402
import presets as presets_store  # noqa: E402
import settings as settings_store  # noqa: E402
import voice  # noqa: E402
from face import FaceEngine  # noqa: E402
from store import MATCH_THRESHOLD, Store  # noqa: E402
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEB = os.path.join(ROOT, "web")
PORT = int(os.environ.get("AR_PORT", "8765"))
MAX_SIDE = 960  # 送进检测前先缩放，CPU 上快 3 倍

store = Store()
engine = FaceEngine()
agent.bind_store(store)          # 智能体的工具要用同一个库实例

# 最近一次识别结果。手机抓拍 → 这里 → 眼镜轮询取走。
# 眼镜端（IrisView 应用）没有摄像头，只能"看"这里的最新一帧结果。
_LAST = {"rev": 0, "faces": [], "at": 0, "ms": 0, "kind": ""}
_LAST_LOCK = threading.Lock()

# 对话分析（模块 02）的会话：只活在内存里，服务重启就没了
_CONV = {}
_CONV_LOCK = threading.Lock()
_CONV_MAX_CHUNKS = 200

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


def decode_audio(payload):
    """接受 dataURL / 纯 base64 / 本地路径，统一变成 wav 字节。"""
    if not payload:
        raise ValueError("没有音频")
    if os.path.exists(payload):
        with open(payload, "rb") as f:
            return f.read()
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload)


def shrink(img):
    h, w = img.shape[:2]
    scale = min(1.0, MAX_SIDE / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img, scale


def _next_stranger_name():
    """给自动入库的陌生人起个名字：陌生人A、陌生人B……（用完 26 个再编号）"""
    used = {p.get("name", "") for p in store.people()}
    for i in range(26):
        name = "陌生人" + chr(ord("A") + i)
        if name not in used:
            return name
    n = 27
    while ("陌生人" + str(n)) in used:
        n += 1
    return "陌生人" + str(n)


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


def _as_dict(value):
    """接受 dict 或 JSON 字符串（控制台里是文本框）；空值返回 None。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"列表": value}
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 格式不对：{e.msg}（第 {e.lineno} 行）")
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"列表": parsed}
    return {"值": parsed}


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
        if path == "/api/settings":
            return self._json({"ok": True, **settings_store.get_settings()})
        if path == "/api/presets":
            return self._json({"ok": True, "items": presets_store.list_presets()})
        if path == "/api/agent/status":
            st = llm.status()
            st["ok"] = True
            return self._json(st)
        if path == "/api/agent/log":
            # 最近 N 条调用日志（JSONL 里一行一条），默认 50
            qs = parse_qs(urlparse(self.path).query)
            try:
                n = max(1, min(500, int((qs.get("n") or ["50"])[0])))
            except Exception:
                n = 50
            return self._json({"path": calllog.path(), "items": calllog.tail(n)})
        if path == "/api/latest/reset":
            # 调试用：把"眼镜上现在显示的那条"清掉，方便验证下一次推送
            with _LAST_LOCK:
                _LAST.update({"rev": _LAST["rev"] + 1, "faces": [], "at": time.time(), "ms": 0, "kind": ""})
                rev = _LAST["rev"]
            return self._json({"ok": True, "rev": rev})
        if path.startswith("/api/card/") and path.endswith("/full"):
            pid = path[len("/api/card/"):-len("/full")].strip("/")
            return self._card_full(pid)
        if path == "/api/converse/llm":
            return self._json({"ok": True, **llm.status()})
        return self._static(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._read_json()
            if path == "/api/scan":
                return self._scan(body)
            if path == "/api/voice/scan":
                return self._voice_scan(body)
            if path == "/api/agent/feed":
                return self._agent_feed(body)
            if path == "/api/agent/archive":
                return self._agent_archive(body)
            if path == "/api/settings/save":
                try:
                    got = settings_store.save_settings(body)
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                return self._json({"ok": True, **got})
            if path == "/api/presets/save":
                try:
                    items = presets_store.save_presets(body.get("items"))
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                return self._json({"ok": True, "items": items})
            if path == "/api/agent/cancel":
                session = str(body.get("session") or "")
                agent.cancel(session)
                return self._json({"ok": True, "cancelled": session})
            if path == "/api/voice/enroll":
                return self._voice_enroll(body)
            if path == "/api/people/save":
                return self._save_person(body)
            if path == "/api/people/delete":
                return self._delete_person(body)
            if path == "/api/converse/start":
                return self._conv_start(body)
            if path == "/api/converse/chunk":
                return self._conv_chunk(body)
            if path == "/api/converse/text":
                return self._conv_text(body)
            if path == "/api/converse/analyze":
                return self._conv_analyze(body)
            if path == "/api/converse/save":
                return self._conv_save(body)
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
            card["kind"] = "face"
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

    # ---------- 声纹 ----------

    def _voice_scan(self, body):
        """眼镜端送来一段语音（16k 单声道 wav），判断"这是谁在说话"。"""
        t0 = time.time()
        samples = voice.read_wav(decode_audio(body.get("audio")))
        if voice.seconds(samples) < voice.MIN_SECONDS:
            card = {
                "kind": "voice", "status": "short", "name": "没听清",
                "role": "", "similarity": -1,
                "lines": ["说话时间太短", "再说两三秒就行"],
            }
            return self._push_voice([card], int((time.time() - t0) * 1000))

        if voice.is_silent(samples):
            card = {
                "kind": "voice", "status": "silent", "name": "没声音",
                "role": "", "similarity": -1,
                "lines": ["这段基本是静音", "检查麦克风或再说近一点"],
            }
            return self._push_voice([card], int((time.time() - t0) * 1000))

        emb = voice.embed(samples)
        person, sim, margin = voice.match(emb, store.voice_index())
        print(f"[voice] {voice.seconds(samples):.1f}s sim={sim:+.3f} margin={margin:+.3f} "
              f"-> {person['name'] if person else 'unknown'}")

        if person:
            full = store.get(person["id"]) or person
            card = cards.build_card(full, sim, voice.THRESHOLD)
            card["status"] = "known"
            card["person_id"] = person["id"]
        elif body.get("auto_add"):
            # 陌生人：直接建号入库（眼镜端一次录音就锁定一个人，不然这整段话无处可放）
            # 注意只写声纹，别把 192 维的声纹塞进人脸底片里，否则下次人脸匹配会维度不匹配
            person = store.save(
                name=_next_stranger_name(),
                role="陌生人 · 眼镜端自动建的",
                info=["语音自动入库，回头改个名字", "", ""],
            )
            store.add_voice(person["id"], emb)
            person = [p for p in store.people() if p["id"] == person["id"]][0]
            card = cards.build_card(person, 1.0, voice.THRESHOLD)
            card["status"] = "known"
            card["person_id"] = person["id"]
            card["created"] = True
            print(f"[voice] auto-added {person['name']} ({person['id']})")
        else:
            card = {
                "status": "unknown", "name": "陌生人", "role": "", "similarity": sim,
                "lines": ["没听过这个声音", "对着控制台说一句就能记住"],
            }
        card["kind"] = "voice"
        card["seconds"] = round(voice.seconds(samples), 1)
        return self._push_voice([card], int((time.time() - t0) * 1000))

    def _voice_enroll(self, body):
        """把一段语音绑到某个人身上（控制台/眼镜端都能调）。"""
        pid = (body.get("id") or "").strip()
        name = (body.get("name") or "").strip()
        if not pid and name:
            found = [p for p in store.people() if p["name"] == name]
            pid = found[0]["id"] if found else ""
        if not pid:
            raise ValueError("要指定 id 或 name")
        samples = voice.read_wav(decode_audio(body.get("audio")))
        if voice.seconds(samples) < voice.MIN_SECONDS:
            raise ValueError("声音太短，至少说 1 秒")
        person = store.add_voice(pid, voice.embed(samples))
        if person is None:
            raise ValueError("没有这个人")
        return self._json({"ok": True, "person": person,
                           "seconds": round(voice.seconds(samples), 1)})

    # ---------- 智能体（LangGraph） ----------

    def _agent_feed(self, body):
        """眼镜端每 15 秒送一片进来：能转写、能认人、能抽要点、能出界面。

        这是新的主链路；老的人脸/声纹接口保留，作为模型不可用时的兜底。
        """
        session = str(body.get("session") or uuid.uuid4().hex[:10])
        text = body.get("text") or ""
        audio = body.get("audio") or ""
        auto_add = body.get("auto_add", True)
        manual = bool(body.get("manual"))
        mode = str(body.get("mode") or "")
        preset = str(body.get("preset") or "")
        view = agent.run_feed(
            session, text=text, audio=audio, auto_add=bool(auto_add),
            mode=mode, manual=manual, preset=preset,
        )
        person = None if view.get("mode") == "meeting" else store.get(view.get("person_id") or "")
        if person:
            try:
                view["role"] = person.get("role") or view.get("role") or ""
            except Exception:
                pass
        card = {
            "kind": "agent",
            "status": "known" if view.get("person") else "unknown",
            "name": view.get("person") or "对话",
            "role": view.get("role") or "",
            "similarity": float(view.get("similarity") or 0.0),
            "lines": (view.get("rows") or [])[:6],
            "person_id": view.get("person_id") or "",
            "state": view.get("state"),
            "layout": view.get("layout"),
            "options": view.get("options") or [],
            "degraded": bool(view.get("degraded")),
        }
        payload = {"ok": True, "session": session, "view": view, "ms": view.get("ms", 0),
                   "faces": [card], "kind": "agent"}
        with _LAST_LOCK:
            _LAST["rev"] += 1
            _LAST["faces"] = [card]
            _LAST["at"] = time.time()
            _LAST["ms"] = view.get("ms", 0)
            _LAST["kind"] = "agent"
            payload["rev"] = _LAST["rev"]
        return self._json(payload)

    def _agent_archive(self, body):
        """结束多人会议：把会议 checkpoint 落成 data/meetings/agent_<session>/meta.json。"""
        session = str(body.get("session") or "")
        if not session:
            raise ValueError("缺少 session")
        return self._json(agent.archive_meeting(session))

    def _push_voice(self, cards_out, ms):
        payload = {"ok": True, "ms": ms, "faces": cards_out, "kind": "voice"}
        with _LAST_LOCK:
            _LAST["rev"] += 1
            _LAST["faces"] = cards_out
            _LAST["at"] = time.time()
            _LAST["ms"] = ms
            _LAST["kind"] = "voice"
            payload["rev"] = _LAST["rev"]
        return self._json(payload)

    def _card_full(self, pid):
        """眼镜上按「确认」时拉这个：复杂数据摊平成一行行。"""
        person = store.get(pid)
        if not person:
            return self._json({"ok": False, "error": "没有这个人"}, 404)
        full = cards.build_full(person)
        return self._json({"ok": True, **full})

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

        # 新的两档数据：simple 直接上镜片，complex 按需拉。两种入参都收：
        #   {"simple": {...}, "complex": {...}} 或 {"simple_json": "{...}"}（控制台里是文本框）
        simple = _as_dict(body.get("simple", body.get("simple_json")))
        complex_ = _as_dict(body.get("complex", body.get("complex_json")))
        if simple is None and not pid:
            simple = {}
        if complex_ is None and not pid:
            complex_ = {}

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
                            replace_embeddings=bool(pid) and embedding is not None,
                            simple=simple, complex_=complex_)
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

    # ---------- 模块 02：单人对话 → 大模型析出要点 ----------

    def _conv_start(self, body):
        pid = (body.get("person_id") or "").strip()
        if not pid:
            raise ValueError("先锁定一个人")
        person = store.get(pid)
        if not person:
            return self._json({"ok": False, "error": "没有这个人"}, 404)
        sid = "c_" + uuid.uuid4().hex[:10]
        with _CONV_LOCK:
            _CONV[sid] = {
                "id": sid,
                "person_id": pid,
                "name": person.get("name", ""),
                "started": time.time(),
                "chunks": 0,
                "transcript": "",
                "analyzed": None,
            }
        return self._json({"ok": True, "session": sid,
                           "person": {"id": pid, "name": person.get("name", ""),
                                      "role": person.get("role", "")},
                           "llm": llm.status()})

    def _conv_get(self, body):
        sid = (body.get("session") or "").strip()
        with _CONV_LOCK:
            s = _CONV.get(sid)
        if not s:
            raise ValueError("会话不存在或服务重启过，重新开一段")
        return sid, s

    def _conv_chunk(self, body):
        sid, s = self._conv_get(body)
        audio = decode_audio(body.get("audio"))
        if not audio:
            raise ValueError("这段音频是空的")
        text = llm.transcribe(audio)
        with _CONV_LOCK:
            s["chunks"] += 1
            if s["chunks"] > _CONV_MAX_CHUNKS:
                raise ValueError("这段对话太长了，先停下来分析一下")
            s["transcript"] = (s["transcript"] + ("\n" if s["transcript"] else "") + text)
            transcript = s["transcript"]
        return self._json({"ok": True, "text": text, "transcript": transcript,
                           "seconds": round(time.time() - s["started"], 1)})

    def _conv_text(self, body):
        """手动补一句/改一句：转写有错时用，或者在没麦克风的机器上试。"""
        sid, s = self._conv_get(body)
        text = (body.get("text") or "").strip()
        if not text and body.get("replace"):
            raise ValueError("内容是空的")
        with _CONV_LOCK:
            if body.get("replace"):
                s["transcript"] = text
            else:
                s["transcript"] = (s["transcript"] + ("\n" if s["transcript"] else "") + text)
            transcript = s["transcript"]
        return self._json({"ok": True, "transcript": transcript})

    def _conv_analyze(self, body):
        sid, s = self._conv_get(body)
        person = store.get(s["person_id"]) or {"name": s["name"]}
        with _CONV_LOCK:
            transcript = s["transcript"]
        result = llm.analyze(transcript, person)
        with _CONV_LOCK:
            s["analyzed"] = result
        return self._json({"ok": True, "transcript": transcript, **result})

    def _conv_save(self, body):
        sid, s = self._conv_get(body)
        items = body.get("items")
        with _CONV_LOCK:
            analyzed = s.get("analyzed") or {}
            transcript = s["transcript"]
        if items is None:
            items = analyzed.get("items") or []
        summary = body.get("summary", analyzed.get("summary", ""))
        person = store.merge_facts(s["person_id"], items, transcript=transcript, summary=summary)
        if not person:
            return self._json({"ok": False, "error": "这个人已经被删了"}, 404)
        return self._json({"ok": True, "person": person, "saved": len(items)})

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

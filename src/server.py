"""本地"云"：识别 + 人脉库 + 卡片生成，一个 http.server 全部搞定。

手机/眼镜/浏览器把一帧 JPEG 发到 /api/scan，拿回"这个人是谁、该怎么开口"。
刻意不依赖 FastAPI/Flask：零安装成本，现场不可能装不上。
"""
import base64
import hmac
import json
import os
import ssl
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
import asr  # noqa: E402
import cards  # noqa: E402
import diarize  # noqa: E402
import llm  # noqa: E402
import meeting  # noqa: E402
import voice  # noqa: E402
from face import FaceEngine  # noqa: E402
from store import MATCH_THRESHOLD, Store  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEB = os.path.join(ROOT, "web")
PORT = int(os.environ.get("AR_PORT", "8765"))
# 默认只监听本机：要局域网/手机访问时显式设 AR_HOST=0.0.0.0（并配 AR_API_TOKEN）
HOST = os.environ.get("AR_HOST", "127.0.0.1").strip() or "127.0.0.1"
MAX_SIDE = 960  # 送进检测前先缩放，CPU 上快 3 倍

# ---- 安全开关 ----
# 配了就要求所有 /api/*（/api/health 除外）带 X-API-Token 或 Authorization: Bearer
API_TOKEN = os.environ.get("AR_API_TOKEN", "").strip()
TLS_CERT = os.environ.get("AR_TLS_CERT", "").strip()
TLS_KEY = os.environ.get("AR_TLS_KEY", "").strip()
DEBUG_ERRORS = os.environ.get("AR_DEBUG_ERRORS", "0").strip() == "1"
LOG_PII = os.environ.get("AR_LOG_PII", "0").strip() == "1"

# ---- 资源上限：防超大盘内存/磁盘；本地演示够用，正式部署可按需调 ----
MAX_BODY_BYTES = int(os.environ.get("AR_MAX_BODY_BYTES", str(16 * 1024 * 1024)))
MAX_AUDIO_BYTES = int(os.environ.get("AR_MAX_AUDIO_BYTES", str(64 * 1024 * 1024)))
MAX_IMAGE_BYTES = int(os.environ.get("AR_MAX_IMAGE_BYTES", str(16 * 1024 * 1024)))
# 本机请求允许传本地文件路径（方便调试）；远端只能用 base64/dataURL。
ALLOW_LOCAL_FILES = os.environ.get("AR_ALLOW_LOCAL_FILES", "1").strip().lower() not in ("0", "false", "no", "off")


class RequestTooLarge(ValueError):
    pass


class UnsupportedMediaType(ValueError):
    pass


class ForbiddenRequest(ValueError):
    pass

store = Store()
engine = FaceEngine()
meetings = meeting.MeetingStore(people_store=store)

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


def _as_bool(value, default=False):
    """把 JSON/表单里的 true/"false"/1 统一成 bool（避免 "false" 被当成 True）。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def decode_image(payload, allow_local=False):
    """接受 dataURL / 纯 base64 / 本地路径，统一变成 BGR ndarray。

    本地路径默认只允许本机请求（allow_local=True），防止远端用绝对路径/UNC
    读取服务器上的任意文件。远端请改用 base64/dataURL。
    """
    if not payload:
        raise ValueError("没有图片")
    if allow_local and os.path.exists(payload):
        if os.path.getsize(payload) > MAX_IMAGE_BYTES:
            raise ValueError("图片过大")
        img = cv2.imread(payload)
        if img is None:
            raise ValueError("路径读不到图片")
        return img
    if "," in payload[:100] and payload.startswith("data:"):
        payload = payload.split(",", 1)[1]
    raw = base64.b64decode(payload)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("图片过大")
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("图片解码失败")
    return img


def decode_audio(payload, allow_local=False):
    """接受 dataURL / 纯 base64 / 本地路径，统一变成 wav 字节。"""
    if not payload:
        raise ValueError("没有音频")
    if allow_local and os.path.exists(payload):
        if os.path.getsize(payload) > MAX_AUDIO_BYTES:
            raise ValueError("音频过大")
        with open(payload, "rb") as f:
            return f.read()
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[1]
    raw = base64.b64decode(payload)
    if len(raw) > MAX_AUDIO_BYTES:
        raise ValueError("音频过大")
    return raw


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
    timeout = 30  # 慢连接/半截请求不能无限占线程

    def log_message(self, fmt, *args):  # 只打印 API 调用，静态文件太吵
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _local(self):
        """本地文件路径只对本机请求开放（远端只能用 base64/dataURL）。"""
        host = (self.client_address or ("",))[0]
        return ALLOW_LOCAL_FILES and host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _token(self):
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return (self.headers.get("X-API-Token") or "").strip()

    def _authorized(self):
        if not API_TOKEN:
            return True
        return hmac.compare_digest(self._token(), API_TOKEN)

    def _reject(self, code, message):
        return self._json({"ok": False, "error": message}, code)

    def _check_csrf(self):
        """所有写请求（含无 body 的 POST/DELETE）都要过这一关。

        浏览器跨站请求会带 Sec-Fetch-Site: cross-site 或不同 Origin；
        命令行/脚本通常不带这两个头，所以只拒绝"带了且不匹配"的情况。
        """
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site == "cross-site":
            raise ForbiddenRequest("cross-site request rejected")
        origin = (self.headers.get("Origin") or "").strip()
        if origin:
            host = (self.headers.get("Host") or "").strip().lower()
            if urlparse(origin).netloc.lower() != host:
                raise ForbiddenRequest("origin mismatch")

    # ---------- 路由 ----------

    def do_GET(self):
        path = self.path.split("?")[0]
        # /api/health 不鉴权（容器健康检查/存活探测）；其余 API 都要 token
        if path.startswith("/api/") and path != "/api/health" and not self._authorized():
            return self._reject(401, "unauthorized")
        try:
            self._get()
        except ValueError as e:  # 业务错（不存在/参数错）保留可读信息
            return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        except Exception as e:  # 其他异常不能泄露内部细节
            traceback.print_exc()
            detail = f"{type(e).__name__}: {e}" if DEBUG_ERRORS else "internal_error"
            return self._json({"ok": False, "error": detail}, 500)

    def _get(self):
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
        if path.startswith("/api/card/") and path.endswith("/full"):
            pid = path[len("/api/card/"):-len("/full")].strip("/")
            return self._card_full(pid)
        if path == "/api/v1/sessions":
            return self._json({"ok": True, "sessions": meetings.list()})
        if path == "/api/v1/audio/play":
            return self._v1_play(query)
        if path.startswith("/api/v1/session/"):
            rest = path[len("/api/v1/session/"):].strip("/")
            parts = rest.split("/")
            if len(parts) == 1:
                return self._json({"ok": True, **meetings.get(parts[0])})
            if len(parts) == 2 and parts[1] == "transcript":
                return self._json({"ok": True, **meetings.transcript(parts[0])})
            if len(parts) == 2 and parts[1] == "card":
                return self._json({"ok": True, **meetings.latest_card(parts[0])})
        if path == "/api/converse/llm":
            return self._json({"ok": True, **llm.status()})
        return self._static(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/") and not self._authorized():
            return self._reject(401, "unauthorized")
        try:
            self._check_csrf()
            body = self._read_json()
            if path == "/api/scan":
                return self._scan(body)
            if path == "/api/voice/scan":
                return self._voice_scan(body)
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
            # ---------- 多人会议：v1 接口（本地《开发者文档》的端云契约） ----------
            if path == "/api/v1/session/create":
                return self._v1_create(body)
            if path == "/api/v1/audio/upload":
                return self._v1_upload(body)
            if path.startswith("/api/v1/session/") and path.endswith("/finalize"):
                sid = path[len("/api/v1/session/"):-len("/finalize")].strip("/")
                body["sessionId"] = sid
                return self._v1_finalize(body)
            if path == "/api/v1/speaker/bind":
                return self._v1_bind(body)
            if path == "/api/v1/action/trigger":
                return self._v1_action(body)
            # 单段音频直接分离（不建会议，给 glasses/调试用）
            if path == "/api/diarize":
                return self._diarize_once(body)
            return self._json({"ok": False, "error": "未知接口"}, 404)
        except RequestTooLarge as e:
            return self._json({"ok": False, "error": str(e)}, 413)
        except UnsupportedMediaType as e:
            return self._json({"ok": False, "error": str(e)}, 415)
        except ForbiddenRequest as e:
            return self._json({"ok": False, "error": str(e)}, 403)
        except ValueError as e:  # 业务校验失败：保留可读信息（现场排障）
            return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        except Exception as e:  # 未知异常：默认不回显内部细节
            traceback.print_exc()
            detail = f"{type(e).__name__}: {e}" if DEBUG_ERRORS else "internal_error"
            return self._json({"ok": False, "error": detail}, 500)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/") and not self._authorized():
            return self._reject(401, "unauthorized")
        try:
            self._check_csrf()
            if path.startswith("/api/v1/session/"):
                sid = path[len("/api/v1/session/"):].strip("/")
                if sid and "/" not in sid:
                    return self._json({"ok": True, **meetings.delete(sid)})
            return self._json({"ok": False, "error": "未知接口"}, 404)
        except ForbiddenRequest as e:
            return self._json({"ok": False, "error": str(e)}, 403)
        except ValueError as e:
            return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        except Exception as e:
            traceback.print_exc()
            detail = f"{type(e).__name__}: {e}" if DEBUG_ERRORS else "internal_error"
            return self._json({"ok": False, "error": detail}, 500)

    # ---------- 业务 ----------

    def _scan(self, body):
        t0 = time.time()
        img = decode_image(body.get("image"), self._local())
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
        samples = voice.read_wav(decode_audio(body.get("audio"), self._local()))
        if voice.seconds(samples) < voice.MIN_SECONDS:
            card = {
                "kind": "voice", "status": "short", "name": "没听清",
                "role": "", "similarity": -1,
                "lines": ["说话时间太短", "再说两三秒就行"],
            }
            return self._push_voice([card], int((time.time() - t0) * 1000))

        emb = voice.embed(samples)
        person, sim, margin = voice.match(emb, store.voice_index())
        who = (person["name"] if LOG_PII else person["id"]) if person else "unknown"
        print(f"[voice] {voice.seconds(samples):.1f}s sim={sim:+.3f} margin={margin:+.3f} -> {who}")

        if person:
            full = store.get(person["id"]) or person
            card = cards.build_card(full, sim, voice.THRESHOLD)
            card["status"] = "known"
            card["person_id"] = person["id"]
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
        samples = voice.read_wav(decode_audio(body.get("audio"), self._local()))
        if voice.seconds(samples) < voice.MIN_SECONDS:
            raise ValueError("声音太短，至少说 1 秒")
        person = store.add_voice(pid, voice.embed(samples))
        if person is None:
            raise ValueError("没有这个人")
        return self._json({"ok": True, "person": person,
                           "seconds": round(voice.seconds(samples), 1)})

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

    # ---------- 多人会议（v1 端云契约，先本地实现 + 预留云端位） ----------

    def _v1_create(self, body):
        meta = meetings.create(body.get("title", ""), body.get("userId", ""))
        return self._json({"ok": True, **meta})

    def _v1_upload(self, body):
        sid = (body.get("sessionId") or "").strip()
        data = decode_audio(body.get("audio") or body.get("data") or "", self._local())
        return self._json({"ok": True, **meetings.add_chunk(sid, data, body.get("seq"))})

    def _v1_finalize(self, body):
        sid = (body.get("sessionId") or "").strip()
        meta = meetings.finalize(sid, with_asr=_as_bool(body.get("asr"), True))
        self._push_meeting_card(sid)
        return self._json({"ok": True, **meta})

    def _v1_bind(self, body):
        sid = (body.get("sessionId") or "").strip()
        spk = (body.get("spkId") or body.get("spk_id") or "").strip()
        meta = meetings.bind_speaker(
            sid, spk,
            body.get("personId") or body.get("person_id") or "",
            body.get("name") or "",
            _as_bool(body.get("enroll"), False),
        )
        return self._json({"ok": True, **meta})

    def _v1_action(self, body):
        """按键指令：summary / ask / mark。当前只做参数与状态，LLM/RAG 接入后填结果。"""
        action = (body.get("actionType") or body.get("action") or "").strip()
        if action not in ("summary", "ask", "mark"):
            raise ValueError("actionType 只能是 summary / ask / mark")
        sid = (body.get("sessionId") or "").strip()
        if sid:
            meetings.get(sid)  # 不存在会抛错
        task_id = "task_%d" % int(time.time() * 1000)
        return self._json({
            "ok": True, "taskId": task_id, "state": "reserved",
            "actionType": action, "sessionId": sid,
            "detail": "接口已预留；接入 LLM/RAG 后在此返回 summary_card / answer_card",
        })

    def _diarize_once(self, body):
        samples = voice.read_wav(decode_audio(body.get("audio"), self._local()))
        result = diarize.diarize(samples, store.voice_index())
        result.pop("_centroids", None)
        return self._json({"ok": True, **result})

    def _push_meeting_card(self, sid):
        card = meetings.latest_card(sid)
        card["kind"] = "meeting"
        with _LAST_LOCK:
            _LAST["rev"] += 1
            _LAST["faces"] = [card]
            _LAST["at"] = time.time()
            _LAST["ms"] = 0
            _LAST["kind"] = "meeting"
        return card

    def _v1_play(self, query):
        sid = (query.get("sessionId") or [""])[0]
        try:
            ts = max(0.0, float((query.get("timestamp") or ["0"])[0]))
            before = min(30.0, max(0.0, float((query.get("before") or ["3"])[0])))
            after = min(30.0, max(0.0, float((query.get("after") or ["3"])[0])))
        except (TypeError, ValueError):
            raise ValueError("timestamp / before / after 必须是数字")
        data = meetings.audio_slice(sid, ts, before, after)
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

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
            img = decode_image(image, self._local())
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
        audio = decode_audio(body.get("audio"), self._local())
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
        img = decode_image(body.get("image") or body.get("image_path"), self._local())
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
        raw_length = self.headers.get("Content-Length")
        try:
            n = int(raw_length or 0)
        except (TypeError, ValueError):
            raise ValueError("非法 Content-Length")
        if n < 0:
            raise ValueError("非法 Content-Length")
        if n > MAX_BODY_BYTES:
            raise RequestTooLarge(f"请求体超过上限 {MAX_BODY_BYTES} 字节")
        if n:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise UnsupportedMediaType("Content-Type 必须是 application/json")

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

    @staticmethod
    def _safe_static(path):
        """把 URL 路径映射到 web/ 下的真实文件；越界/软链逃逸返回 None。"""
        if path in ("/", ""):
            path = "/index.html"
        web = os.path.realpath(WEB)
        full = os.path.realpath(os.path.join(web, path.lstrip("/").replace("\\", "/")))
        try:
            if os.path.commonpath([full, web]) != web:
                return None
        except ValueError:  # 不同盘符
            return None
        return full if os.path.isfile(full) else None

    def _static(self, path):
        full = self._safe_static(path)
        if full is None:
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
    if bool(TLS_CERT) != bool(TLS_KEY):
        raise SystemExit("AR_TLS_CERT 和 AR_TLS_KEY 必须同时设置（或都不设）")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    scheme = "http"
    if TLS_CERT:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(TLS_CERT, TLS_KEY)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    shown = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    print(f"MemoryLens 已启动: {scheme}://{shown}:{PORT}  (阈值 {MATCH_THRESHOLD}, 库中 {len(store.people())} 人)")
    print(f"监听 {HOST}:{PORT}；鉴权 {'开启' if API_TOKEN else '关闭'}；"
          f"本机文件路径 {'允许' if ALLOW_LOCAL_FILES else '禁用'}；TLS {'开启' if TLS_CERT else '关闭'}")
    if HOST in ("0.0.0.0", "::") and not API_TOKEN:
        print("警告: 正在监听所有网卡且未设置 AR_API_TOKEN，同一局域网的人可以访问你的数据！")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

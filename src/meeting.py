"""会议会话：把眼镜/手机连续上传的音频分片，合成一场会，跑多人分离并落盘。

设计目标（黑客松优先级）：
  1. 一切都能用记事本看懂：data/meetings/<id>/meta.json + chunk_XXXX.wav
  2. 不引入数据库/消息队列；一个进程一把锁
  3. 先离线跑通"谁在说话"，ASR/LLM 纪要按需接（src/asr.py 预留）
  4. 声纹质心单独放 centroids.json，meta.json 可以放心分享/贴日志

会话状态机：
    collecting ──finalize──▶ finalized
        ▲                        │
        └──────── 再上传新分片会重新变为 collecting（再次 finalize 会重算）

任务边界：只处理轮流说话；重叠段由 diarize 清洗后丢弃（dropped_overlap 计数）。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
import uuid
import wave

import numpy as np

import asr
import diarize
import llm
import voice

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DIR = os.environ.get("AR_MEETING_DIR", os.path.join(ROOT, "data", "meetings"))


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# 资源上限：防止一场会传无限分片把磁盘/内存吃满（默认 30 分钟 / 240 片 / 单片 120s）
MAX_MEETING_SECONDS = max(1.0, _env_float("AR_MEETING_MAX_SECONDS", 1800.0))
MAX_CHUNKS = max(1, _env_int("AR_MEETING_MAX_CHUNKS", 240))
MAX_CHUNK_SECONDS = max(1.0, _env_float("AR_MEETING_MAX_CHUNK_SECONDS", 120.0))


def _public(meta: dict) -> dict:
    out = dict(meta)
    out.pop("_centroids", None)
    out.pop("_samples", None)
    return out


def _write_wav(path: str, samples: np.ndarray) -> None:
    pcm = np.clip(samples * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice.SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def _read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    pcm = np.clip(samples * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice.SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class MeetingError(ValueError):
    pass


class MeetingStore:
    def __init__(self, root: str | None = None, people_store=None):
        self.root = os.path.abspath(root or DEFAULT_DIR)
        self.people = people_store
        self._lock = threading.RLock()
        os.makedirs(self.root, exist_ok=True)

    # ------------------------------------------------------------ 基础

    def _dir(self, sid: str) -> str:
        sid = (sid or "").strip()
        if not sid or any(c not in "0123456789abcdefghijklmnopqrstuvwxyz_" for c in sid):
            raise MeetingError("非法的 sessionId")
        return os.path.join(self.root, sid)

    def _meta_path(self, sid: str) -> str:
        return os.path.join(self._dir(sid), "meta.json")

    def _centroid_path(self, sid: str) -> str:
        return os.path.join(self._dir(sid), "centroids.json")

    def _load(self, sid: str) -> dict:
        path = self._meta_path(sid)
        if not os.path.isfile(path):
            raise MeetingError("没有这个会议")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, meta: dict) -> None:
        path = self._meta_path(meta["sessionId"])
        meta["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    # ------------------------------------------------------------ 会话

    def create(self, title: str = "", user_id: str = "") -> dict:
        with self._lock:
            sid = "s_" + uuid.uuid4().hex[:10]
            os.makedirs(self._dir(sid), exist_ok=True)
            meta = {
                "sessionId": sid,
                "title": (title or "").strip() or "未命名会议",
                "userId": (user_id or "").strip(),
                "status": "collecting",
                "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updatedAt": "",
                "chunks": [],
                "duration": 0.0,
                "speaker_count": 0,
                "speakers": [],
                "segments": [],
                "dropped_overlap": 0,
                "asr": {"provider": "off", "state": "not-configured", "detail": ""},
                "summary": None,
            }
            self._save(meta)
            return _public(meta)

    def get(self, sid: str) -> dict:
        with self._lock:
            return _public(self._load(sid))

    def list(self) -> list[dict]:
        out = []
        for name in sorted(os.listdir(self.root)):
            if not name.startswith("s_"):
                continue
            try:
                meta = self._load(name)
            except (MeetingError, OSError, json.JSONDecodeError):
                continue
            out.append({
                "sessionId": meta["sessionId"],
                "title": meta.get("title", ""),
                "status": meta.get("status", ""),
                "duration": meta.get("duration", 0),
                "speakerCount": len(meta.get("speakers") or []),
                "updatedAt": meta.get("updatedAt", ""),
            })
        out.sort(key=lambda m: m.get("updatedAt", ""), reverse=True)
        return out

    def delete(self, sid: str) -> dict:
        """删除一整场会议（含音频、质心）。"""
        with self._lock:
            path = self._dir(sid)
            if not os.path.isdir(path):
                raise MeetingError("没有这个会议")
            shutil.rmtree(path)
            return {"sessionId": sid, "deleted": True}

    # ------------------------------------------------------------ 分片

    def add_chunk(self, sid: str, data: bytes, seq: int | None = None) -> dict:
        """上传一个音频分片（wav 字节）。任何格式都会被转成 16k mono 落盘。"""
        samples = voice.read_wav(data)
        if samples.size == 0:
            raise MeetingError("空音频")
        seconds = round(samples.size / voice.SAMPLE_RATE, 3)
        if seconds > MAX_CHUNK_SECONDS:
            raise MeetingError(f"单个分片不能超过 {MAX_CHUNK_SECONDS:.0f} 秒")
        with self._lock:
            meta = self._load(sid)
            if len(meta["chunks"]) >= MAX_CHUNKS:
                raise MeetingError(f"分片数已达上限 {MAX_CHUNKS}")
            if meta["duration"] + seconds > MAX_MEETING_SECONDS:
                raise MeetingError(f"会议总时长不能超过 {MAX_MEETING_SECONDS:.0f} 秒")
            if seq is None:
                seq = max([c["seq"] for c in meta["chunks"]] + [0]) + 1
            try:
                seq = int(seq)
            except (TypeError, ValueError):
                raise MeetingError("seq 必须是整数")
            if seq < 1:
                raise MeetingError("seq 必须 >= 1")
            if any(c["seq"] == seq for c in meta["chunks"]):
                raise MeetingError(f"分片 {seq} 已存在")
            name = f"chunk_{seq:04d}.wav"
            _write_wav(os.path.join(self._dir(sid), name), samples)
            meta["chunks"].append({"seq": seq, "file": name, "seconds": seconds})
            meta["chunks"].sort(key=lambda c: c["seq"])
            meta["duration"] = round(sum(c["seconds"] for c in meta["chunks"]), 3)
            meta["status"] = "collecting"
            self._save(meta)
            return {"sessionId": sid, "received": seq, "seconds": seconds,
                    "duration": meta["duration"], "chunks": len(meta["chunks"])}

    def _concat(self, sid: str) -> np.ndarray:
        meta = self._load(sid)
        if not meta["chunks"]:
            raise MeetingError("还没有任何音频分片")
        parts = [_read_wav(os.path.join(self._dir(sid), c["file"])) for c in meta["chunks"]]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------ 分离

    def finalize(self, sid: str, with_asr: bool = True) -> dict:
        """合并所有分片 → 多人分离 → 实名映射 →（可选）ASR 文本。"""
        with self._lock:
            meta = self._load(sid)
            samples = self._concat(sid)
            if samples.size < voice.SAMPLE_RATE:
                raise MeetingError("音频不足 1 秒，先多说几句再生成")
            _write_wav(os.path.join(self._dir(sid), "meeting.wav"), samples)

            people = self.people.voice_index() if self.people is not None else None
            result = diarize.diarize(samples, people)
            if not result.get("speaker_count"):
                reason = result.get("reason") or "没有检测到人声"
                raise MeetingError(reason)

            meta["duration"] = round(samples.size / voice.SAMPLE_RATE, 3)
            meta["speakers"] = result["speakers"]
            meta["speaker_count"] = result["speaker_count"]
            meta["segments"] = [dict(s, text="") for s in result["segments"]]
            meta["dropped_overlap"] = result.get("dropped_overlap", 0)

            # 质心单独落盘：meta.json 不含生物特征，方便贴日志/分享
            centroids = result.get("_centroids") or []
            centroid_map = {s["spk_id"]: c for s, c in zip(result["speakers"], centroids)}
            with open(self._centroid_path(sid), "w", encoding="utf-8") as f:
                json.dump(centroid_map, f)

            # 转写优先用 llm.py 的统一 ASR（本地 paraformer / OpenAI 兼容），
            # 它的可用性不会"假装成功"；没配时退回 asr.py 的云端适配层。
            meta["asr"] = {"provider": "off", "state": "not-configured", "detail": ""}
            if with_asr:
                if llm.asr_available():
                    status = llm.status()
                    meta["asr"] = {"provider": status.get("asrProvider", "llm"),
                                   "state": "ok", "detail": ""}
                    ok_count = failed = 0
                    for seg in meta["segments"]:
                        a = int(seg["start"] * voice.SAMPLE_RATE)
                        b = int(seg["end"] * voice.SAMPLE_RATE)
                        if b - a < int(0.2 * voice.SAMPLE_RATE):
                            continue
                        try:
                            seg["text"] = llm.transcribe(wav_bytes(samples[a:b]))
                            ok_count += 1
                        except Exception as error:
                            failed += 1
                            meta["asr"]["detail"] = f"{type(error).__name__}: {error}"
                    if failed and not ok_count:
                        meta["asr"]["state"] = "error"
                    elif failed:
                        meta["asr"]["state"] = "partial"
                else:
                    provider = asr.get_provider()
                    meta["asr"] = {"provider": provider.name, "state": provider.state,
                                   "detail": provider.detail}
                    if provider.available:
                        try:
                            text_segments = provider.transcribe(
                                os.path.join(self._dir(sid), "meeting.wav"))
                            meta["segments"] = asr.attach_text(meta["segments"], text_segments)
                            meta["asr"]["state"] = "ok"
                        except Exception as error:  # 云端失败绝不能把离线结果弄丢
                            meta["asr"]["state"] = "error"
                            meta["asr"]["detail"] = f"{type(error).__name__}: {error}"

            meta["status"] = "finalized"
            self._save(meta)
            return _public(meta)

    def transcript(self, sid: str) -> dict:
        meta = self.get(sid)
        names = {s["spk_id"]: (s.get("name") or s["spk_id"]) for s in meta.get("speakers", [])}
        segments = []
        for seg in meta.get("segments", []):
            row = dict(seg)
            row["speaker"] = names.get(seg["spk_id"], seg["spk_id"])
            segments.append(row)
        return {"sessionId": sid, "duration": meta.get("duration", 0),
                "speakers": meta.get("speakers", []), "segments": segments,
                "dropped_overlap": meta.get("dropped_overlap", 0)}

    # ------------------------------------------------------------ 实名

    def bind_speaker(self, sid: str, spk_id: str, person_id: str = "",
                     name: str = "", enroll: bool = False) -> dict:
        """把一个匿名 spk 绑到人脉库里的某个人；可选把簇质心写进他的声纹。

        这是"匿名 spk → 实名"的关键一步：ASR/分离只给 spk_0，人由用户确认。
        """
        with self._lock:
            meta = self._load(sid)
            target = next((s for s in meta.get("speakers", []) if s["spk_id"] == spk_id), None)
            if target is None:
                raise MeetingError(f"没有 {spk_id}")
            person = None
            if person_id and self.people is not None:
                person = self.people.get(person_id)
            elif name and self.people is not None:
                hits = [p for p in self.people.people() if p["name"] == name]
                person = hits[0] if hits else None
            if person is None:
                raise MeetingError("要指定存在的 person_id 或 name")

            target["person_id"] = person["id"]
            target["name"] = person.get("name", "")
            if enroll and self.people is not None:
                centroid = self._load_centroids(sid).get(spk_id)
                if centroid:
                    self.people.add_voice(person["id"],
                                          np.asarray(centroid, dtype=np.float32),
                                          replace=False)
            self._save(meta)
            return _public(meta)

    def _load_centroids(self, sid: str) -> dict:
        path = self._centroid_path(sid)
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    # ------------------------------------------------------------ 锚点回放

    def audio_slice(self, sid: str, timestamp: float, before: float = 3.0,
                    after: float = 3.0) -> bytes:
        """按"会议时间戳"截一段音频，用于纪要/锚点回放。"""
        with self._lock:
            path = os.path.join(self._dir(sid), "meeting.wav")
            if not os.path.isfile(path):
                self.finalize(sid, with_asr=False)
            samples = _read_wav(path)
        start = max(0, int((timestamp - before) * voice.SAMPLE_RATE))
        end = min(samples.size, int((timestamp + after) * voice.SAMPLE_RATE))
        if end <= start:
            raise MeetingError("时间戳超出会议范围")
        return wav_bytes(samples[start:end])

    # ------------------------------------------------------------ 眼镜卡片

    def latest_card(self, sid: str | None = None) -> dict:
        """把最近一场会议压成眼镜能显示的 4 行。"""
        meta = None
        if sid:
            meta = self.get(sid)
        else:
            items = self.list()
            if items:
                meta = self.get(items[0]["sessionId"])
        if meta is None:
            return {"status": "meeting", "name": "会议", "accent": "amber",
                    "lines": ["还没有会议", "先创建一场会再说"]}
        lines = [f"会议 · {len(meta.get('speakers') or [])} 人 · {meta.get('duration', 0):.0f}s"]
        for i, spk in enumerate(meta.get("speakers") or [], start=1):
            if i > 3:
                break
            lines.append(f"{i}. {spk.get('name') or spk['spk_id']} · {spk.get('seconds', 0):.0f}s")
        return {"status": "meeting", "name": meta.get("title", "会议"),
                "accent": "cyan", "sessionId": meta["sessionId"],
                "lines": lines, "person_id": None}

# -*- coding: utf-8 -*-
"""豆包流式语音识别 2.0：每次转写一段 WAV。

当前用于替换本地 SenseVoice 的 per-segment ASR；后续接真正的连续流式时，
复用这里的鉴权/协议常量和解析函数。
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import re
import struct
import uuid

ENDPOINT = os.environ.get(
    "AR_DOUBAO_ENDPOINT",
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
)
RESOURCE_ID = os.environ.get("AR_DOUBAO_RESOURCE_ID", "volc.seedasr.sauc.duration")
MODEL = os.environ.get("AR_DOUBAO_MODEL", "bigmodel")
TIMEOUT = float(os.environ.get("AR_DOUBAO_TIMEOUT", "15"))


_DEFAULT_HOTWORDS = [
    "主席树", "主函数", "主键", "豆包", "API", "转录", "调优",
    "PDF", "bug", "二叉树", "算法", "数据结构",
    "氢氟酸", "氟化氢", "王水", "化学",
]


def _hotwords() -> list[str]:
    raw = os.environ.get("AR_DOUBAO_HOTWORDS", "")
    words = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    # env.local.bat 是 ASCII，中文热词放在 data/hotwords.txt（UTF-8）
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "data", "hotwords.txt")
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as f:
                words += [x.strip() for x in re.split(r"[,，\s]+", f.read()) if x.strip()]
    except Exception:
        pass
    if not words:
        words = list(_DEFAULT_HOTWORDS)
    out = []
    for w in words:
        if w and w not in out:
            out.append(w)
    return out[:20]


def _fix_domain(text: str) -> str:
    # 少量高频领域词兜底；只做明确、不会改变原意的替换。
    for src, dst in (
        ("主席数", "主席树"),
        ("豆包调鱼", "豆包调优"),
        ("转路", "转录"),
        ("幸福算", "氢氟酸"),
        ("幸福酸", "氢氟酸"),
        ("青氟酸", "氢氟酸"),
    ):
        text = text.replace(src, dst)
    return text

_HEADER_SIZE = 4
_FULL_CLIENT = 0b0001
_AUDIO_ONLY = 0b0010
_FULL_RESPONSE = 0b1001
_ERROR = 0b1111


class DoubaoASRError(RuntimeError):
    pass


def available() -> bool:
    return bool(_api_key())


def _api_key() -> str:
    return (os.environ.get("AR_DOUBAO_API_KEY")
            or os.environ.get("VOLCENGINE_VOICE_API_KEY") or "").strip()


def _header(msg_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return struct.pack("BBBB", (1 << 4) | 1, (msg_type << 4) | flags,
                       (serialization << 4) | compression, 0)


def _extract_text(obj: dict) -> str:
    result = obj.get("result") or {}
    if isinstance(result, dict):
        text = (result.get("text") or "").strip()
        if text:
            return text
        parts = []
        for u in result.get("utterances") or []:
            if isinstance(u, dict) and u.get("text"):
                parts.append(str(u["text"]).strip())
        return "".join(parts).strip()
    if isinstance(result, str):
        return result.strip()
    return ""


async def _transcribe(wav_bytes: bytes) -> dict:
    import websockets as ws_lib  # 延迟导入，没装时服务仍可走本地兜底

    key = _api_key()
    if not key:
        raise DoubaoASRError("没配 AR_DOUBAO_API_KEY")
    headers = {
        "X-Api-Key": key,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Sequence": "-1",
    }
    async with ws_lib.connect(ENDPOINT, additional_headers=headers,
                              max_size=64 * 1024 * 1024) as ws:
        request = {
            "model_name": MODEL,
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "enable_nonstream": True,
        }
        hotwords = _hotwords()
        if hotwords:
            request["hotwords"] = [{"word": w} for w in hotwords]
            request["context"] = json.dumps(
                {"hotwords": [{"word": w} for w in hotwords]}, ensure_ascii=False)
        payload = json.dumps({
            "user": {"uid": "memorylens"},
            "audio": {"format": "wav", "rate": 16000, "bits": 16, "channel": 1},
            "request": request,
        }, ensure_ascii=False).encode("utf-8")
        payload = gzip.compress(payload)
        await ws.send(_header(_FULL_CLIENT, 0, 1, 1)
                      + struct.pack(">I", len(payload)) + payload)
        await ws.send(_header(_AUDIO_ONLY, 0, 0, 0)
                      + struct.pack(">I", len(wav_bytes)) + wav_bytes)
        await ws.send(_header(_AUDIO_ONLY, 2, 0, 0) + struct.pack(">I", 0))

        final_text = ""
        utterances: list[dict] = []
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            except asyncio.TimeoutError:
                break
            except Exception:
                break
            if isinstance(raw, str):
                continue
            if len(raw) < 8:
                continue
            h = raw[:4]
            msg_type = (h[1] >> 4) & 0x0F
            flags = h[1] & 0x0F
            compression = h[2] & 0x0F
            has_seq = flags in (1, 3)
            off = 4 + (4 if has_seq else 0)
            if len(raw) < off + 4:
                continue
            size = struct.unpack(">I", raw[off:off + 4])[0]
            body = raw[off + 4:off + 4 + size]
            if compression == 1:
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
            if msg_type == _FULL_RESPONSE and body:
                try:
                    obj = json.loads(body.decode("utf-8"))
                except Exception:
                    continue
                text = _extract_text(obj)
                if text:
                    final_text = text
                result = obj.get("result") or {}
                if isinstance(result, dict):
                    for u in result.get("utterances") or []:
                        if not isinstance(u, dict):
                            continue
                        ut = str(u.get("text") or "").strip()
                        if not ut:
                            continue
                        item = {
                            "text": _fix_domain(ut),
                            "start": float(u.get("start_time") or 0) / 1000.0,
                            "end": float(u.get("end_time") or 0) / 1000.0,
                            "speaker_id": str((u.get("additions") or {}).get("speaker_id") or ""),
                        }
                        key = (round(item["start"], 2), round(item["end"], 2), item["text"])
                        if key not in {(round(x["start"], 2), round(x["end"], 2), x["text"]) for x in utterances}:
                            utterances.append(item)
            elif msg_type == _ERROR:
                raise DoubaoASRError("豆包 ASR 返回错误：" + body.decode("utf-8", "replace")[:200])
        return {"text": _fix_domain(final_text.strip()), "utterances": utterances}


def transcribe_detail(wav_bytes: bytes) -> dict:
    if not wav_bytes:
        raise DoubaoASRError("空音频")
    try:
        # 不要用 asyncio.get_event_loop()：Python 3.12 在 meeting-offline 等
        # 非主线程里没有 current loop，会直接抛 "There is no current event loop"，
        # 结果每个豆包调用都被静默回退到本地 ASR。
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_transcribe(wav_bytes))
        # 极端情况下调用方本身在 async loop 里：换线程跑，避免嵌套 loop。
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _transcribe(wav_bytes)).result(timeout=TIMEOUT + 10)
    except DoubaoASRError:
        raise
    except Exception as exc:
        raise DoubaoASRError(str(exc)[:200]) from exc


def transcribe_wav(wav_bytes: bytes) -> str:
    return str(transcribe_detail(wav_bytes).get("text") or "").strip()

"""声纹：把一段说话音频压成一个向量，用余弦相似度比"是不是同一个人"。

模型：CAM++（3D-Speaker 中文通用版，16k 单声道），28MB，CPU 上 1 秒音频约 20ms。
输入格式和眼镜端的 input.audio 完全一致：16000Hz / 单声道 / signed16。
"""

from __future__ import annotations

import io
import os
import threading
import time
import wave

import numpy as np

MODEL = os.environ.get(
    "AR_VOICE_MODEL",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "campplus_zh_16k.onnx"),
)
SAMPLE_RATE = 16000
MIN_SECONDS = 1.0          # 短于这个时长，特征不稳，直接判"没听清"
MAX_SECONDS = 30.0         # 再长也不提升，截断省时间
THRESHOLD = float(os.environ.get("AR_VOICE_THRESHOLD", "0.72"))
# 低于这个"最响一帧"就当没说话（int16 幅值）。会场底噪常在 200 以下
PEAK_MIN = float(os.environ.get("AR_VOICE_PEAK_MIN", "300"))
# 陌生声音评分低于这个值才允许自动建号，避免把"像但不确定"的人建重复
AUTOADD_MAX = float(os.environ.get("AR_VOICE_AUTOADD_MAX", "0.62"))
MARGIN = float(os.environ.get("AR_VOICE_MARGIN", "0.08"))
# 「识别人」专用的阈值：真实录音（微信/电话/会议室）和注册时的近场麦克风差别很大，
# 同一个人常常只有 0.5~0.6；用 0.72 会全部判成陌生人。0.45 + margin 只在
# 明显比第二个人像时才认，实测对 TTS 陌生人（≤0.30）仍不会误认。
IDENTIFY_THRESHOLD = float(os.environ.get("AR_IDENTIFY_THRESHOLD", "0.45"))
IDENTIFY_MARGIN = float(os.environ.get("AR_IDENTIFY_MARGIN", "0.08"))

_extractor = None
_lock = threading.Lock()        # 推理锁
_init_lock = threading.Lock()   # 模型加载锁


def _get_extractor():
    """懒加载：第一次调用才把 28MB 模型读进内存。"""
    global _extractor
    if _extractor is not None:
        return _extractor
    with _init_lock:
        if _extractor is None:
            import sherpa_onnx

            cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=MODEL, num_threads=2, debug=False, provider="cpu"
            )
            _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
    return _extractor


def warmup() -> float:
    """预加载声纹模型，返回加载耗时秒数（服务启动时后台调用）。

    不预加载的话，重启后第一次「识别人」要等模型加载，实测 ~10 秒，
    眼镜上看起来就像"卡住了"。
    """
    t0 = time.time()
    _get_extractor()
    return round(time.time() - t0, 2)


def read_wav(data: bytes) -> np.ndarray:
    """wav 字节 -> float32 mono，重采样到 16k。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("只支持 16-bit PCM")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != SAMPLE_RATE:
        x = resample(x, rate, SAMPLE_RATE)
    return x


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """线性插值重采样——只用于"凑合"，正式链路（眼镜/手机）本来就是 16k。"""
    if src == dst or x.size == 0:
        return x
    n = int(round(x.size * dst / src))
    pos = np.linspace(0, x.size - 1, n, dtype=np.float64)
    return np.interp(pos, np.arange(x.size), x).astype(np.float32)


def peak(samples: np.ndarray) -> float:
    """最响那一帧的幅值（int16 量级），用来判断"是不是真有人在说话"。"""
    if samples.size == 0:
        return 0.0
    return float(np.max(np.abs(samples)) * 32768.0)


def is_silent(samples: np.ndarray, peak_min: float = None) -> bool:
    return peak(samples) < (PEAK_MIN if peak_min is None else peak_min)


def embed(samples: np.ndarray) -> np.ndarray:
    """一段 float32 波形 -> 192 维单位向量。"""
    ex = _get_extractor()
    if samples.size == 0:
        raise ValueError("空音频")
    if is_silent(samples):
        raise ValueError("这段没有声音")
    limit = int(MAX_SECONDS * SAMPLE_RATE)
    if samples.size > limit:
        samples = samples[:limit]
    samples = np.ascontiguousarray(samples, dtype=np.float32)
    with _lock:
        stream = ex.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        stream.input_finished()
        if not ex.is_ready(stream):
            raise ValueError("音频太短")
        v = np.asarray(ex.compute(stream), dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def embed_wav(data: bytes) -> np.ndarray:
    return embed(read_wav(data))


def seconds(samples: np.ndarray) -> float:
    return samples.size / SAMPLE_RATE


def match(emb: np.ndarray, people: list[dict], threshold=None, margin=None) -> tuple[dict | None, float, float]:
    """在人脉库里找最像的人。

    返回 (person|None, best_sim, margin)。判定要同时满足：
      1) 最高分 >= threshold（默认 THRESHOLD）  —— 绝对像
      2) 领先第二名 >= margin（默认 MARGIN）    —— 明显比"第二像的人"更像
    第 2 条是为了挡住"两个声线接近的人"互相串号。
    """
    thr = THRESHOLD if threshold is None else float(threshold)
    mar = MARGIN if margin is None else float(margin)
    scored = []
    for p in people:
        for v in p.get("voice_embeddings") or []:
            scored.append((float(np.dot(emb, np.asarray(v, dtype=np.float32))), p))
    if not scored:
        return None, -1.0, 0.0
    scored.sort(key=lambda t: t[0], reverse=True)
    best_sim, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else -1.0
    margin = best_sim - max(second, -1.0) if len(scored) > 1 else 1.0
    if best_sim >= thr and margin >= mar:
        return best, best_sim, margin
    return None, best_sim, margin

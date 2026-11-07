# -*- coding: utf-8 -*-
"""说话人分离：把一段"我和对方"的录音拆成两个簇，供"只认对方"用。"""
import os
import threading

import numpy as np
import sherpa_onnx

import voice

SEG_MODEL = os.environ.get(
    "AR_DIAR_MODEL",
    r"E:\ar-memory\models\diar\sherpa-onnx-pyannote-segmentation-3-0\model.onnx",
)
SPEAKER_MODEL = os.environ.get("AR_VOICE_MODEL", r"E:\ar-memory\models\campplus_zh_16k.onnx")

_lock = threading.Lock()
_diar = None


def available():
    return os.path.exists(SEG_MODEL) and os.path.exists(SPEAKER_MODEL)


def _get():
    global _diar
    if _diar is not None:
        return _diar
    with _lock:
        if _diar is None:
            cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=SEG_MODEL
                    )
                ),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=SPEAKER_MODEL, num_threads=2, provider="cpu"
                ),
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
                min_duration_on=0.3,
                min_duration_off=0.5,
            )
            _diar = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    return _diar


def split(samples):
    """返回 (clusters, segments)。

    clusters: {speaker_id: {"emb": 质心, "secs": 该簇总时长, "count": 段数}}
    segments: [{"start","end","speaker","emb"}]
    """
    if not available() or samples is None or len(samples) < 16000:
        return {}, []

    sd = _get()
    res = sd.process(np.asarray(samples, dtype=np.float32)).sort_by_start_time()
    segs = []
    buckets = {}
    for s in res:
        piece = samples[int(s.start * 16000): int(s.end * 16000)]
        if len(piece) < 16000 * 0.5:
            continue
        try:
            emb = voice.embed(piece)
        except Exception:
            continue
        segs.append({"start": float(s.start), "end": float(s.end), "speaker": int(s.speaker), "emb": emb})
        buckets.setdefault(int(s.speaker), []).append(emb)

    clusters = {}
    for spk, embs in buckets.items():
        c = np.mean(embs, axis=0)
        n = float(np.linalg.norm(c))
        if n <= 0:
            continue
        clusters[spk] = {
            "emb": c / n,
            "count": len(embs),
            "secs": round(sum(s["end"] - s["start"] for s in segs if s["speaker"] == spk), 2),
        }
    return clusters, segs

"""多人说话人分离（diarization）：分段 → 逐段声纹 → 聚类 → 映射回人脉库。

任务边界（v3）：
  - **只处理轮流说话**（turn-taking）。两人同时说话视为垃圾信息，默认丢弃重叠段。
  - 输入 16k mono float32；输出 spk_0..spk_N + 起止时间 + 可选实名。

链路：
    音频
      → pyannote segmentation-3.0（拿语音段起止）
      → 重叠段清洗（drop / trim，默认 drop）
      → CAM++ 逐段 192 维声纹（复用 src/voice.py）
      → 贪心聚类 + 质心二次合并（余弦 ≥ AR_DIAR_MERGE_SIM 归为一簇）
      → 簇质心与人脉库比对（复用 voice.THRESHOLD / voice.MARGIN）
      → [{spk_id, start, end, person_id?, name?}]

为什么不用 sherpa-onnx 自带的 FastClustering：
实测 3 人测试集上它把两个人合成一个 speaker（K=3 也只出 2 簇），且不好把
匿名 spk 绑回人脉库。这里只借它的 pyannote 分段，聚类自己做。

环境变量：
    AR_DIAR_SEG_MODEL      分段模型路径（默认 models/diarization/pyannote-segmentation-3.0.onnx）
    AR_DIAR_MERGE_SIM      同人判定阈值，默认 0.75（同人实测 ≥0.82，异人 ≤0.67）
    AR_DIAR_MAX_SPEAKERS   最多几个人，默认 8
    AR_DIAR_MIN_SEG        小于这个时长的段不单独成簇，默认 0.5s
    AR_DIAR_GAP            相邻同人段合并的最大间隔，默认 0.35s
    AR_DIAR_MIN_ON / OFF   分段最小语音/静音时长，默认 0.3 / 0.5s
    AR_DIAR_OVERLAP        重叠策略：drop（默认，整段丢）/ trim（截掉重叠部分）
    AR_DIAR_OVERLAP_TOL    判定重叠的容差，默认 0.12s
"""

from __future__ import annotations

import os
import threading
import time

import numpy as np

import voice


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


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SEG_MODEL = os.environ.get(
    "AR_DIAR_SEG_MODEL",
    os.path.join(ROOT, "models", "diarization", "pyannote-segmentation-3.0.onnx"),
)
MERGE_SIM = min(max(_env_float("AR_DIAR_MERGE_SIM", 0.75), 0.1), 0.99)
MAX_SPEAKERS = max(1, _env_int("AR_DIAR_MAX_SPEAKERS", 8))
MIN_SEG_SECONDS = _env_float("AR_DIAR_MIN_SEG", 0.8)
MIN_SPEAKER_SECONDS = _env_float("AR_DIAR_MIN_SPEAKER_SEC", 1.0)
GAP_SECONDS = _env_float("AR_DIAR_GAP", 0.35)
MIN_ON = _env_float("AR_DIAR_MIN_ON", 0.3)
MIN_OFF = _env_float("AR_DIAR_MIN_OFF", 0.5)
OVERLAP_POLICY = (os.environ.get("AR_DIAR_OVERLAP", "drop") or "drop").strip().lower()
if OVERLAP_POLICY not in ("drop", "trim"):
    OVERLAP_POLICY = "drop"
OVERLAP_TOL = max(0.0, _env_float("AR_DIAR_OVERLAP_TOL", 0.12))
SAMPLE_RATE = voice.SAMPLE_RATE

_segmenter = None
_segmenter_lock = threading.Lock()


# ---------------------------------------------------------------- 分段


def _get_segmenter():
    """懒加载 pyannote 分段模型（只借它的分段，聚类不用它）。线程安全。"""
    global _segmenter
    if _segmenter is not None:
        return _segmenter
    with _segmenter_lock:
        if _segmenter is not None:
            return _segmenter
        if not os.path.isfile(SEG_MODEL):
            return None
        import sherpa_onnx

        cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=SEG_MODEL
                )
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=voice.MODEL),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
            min_duration_on=MIN_ON,
            min_duration_off=MIN_OFF,
        )
        _segmenter = sherpa_onnx.OfflineSpeakerDiarization(cfg)
        return _segmenter


def _energy_segments(samples: np.ndarray) -> list[tuple[float, float]]:
    """没有分段模型时的兜底：短时能量 VAD。"""
    if samples.size == 0:
        return []
    frame = int(0.025 * SAMPLE_RATE)
    hop = int(0.010 * SAMPLE_RATE)
    if samples.size < frame:
        return [(0.0, samples.size / SAMPLE_RATE)]
    count = 1 + (samples.size - frame) // hop
    rms = np.empty(count, dtype=np.float32)
    for i in range(count):
        chunk = samples[i * hop: i * hop + frame]
        rms[i] = float(np.sqrt(np.mean(chunk * chunk)))
    threshold = max(0.006, 0.12 * float(np.percentile(rms, 95)))
    voiced = rms >= threshold

    segments: list[tuple[float, float]] = []
    start = None
    for i, on in enumerate(voiced):
        t = (i * hop + frame / 2) / SAMPLE_RATE
        if on and start is None:
            start = max(0.0, t - frame / 2 / SAMPLE_RATE)
        elif not on and start is not None:
            if t - start >= MIN_ON:
                segments.append((start, t))
            start = None
    if start is not None:
        segments.append((start, samples.size / SAMPLE_RATE))

    merged: list[tuple[float, float]] = []
    for seg in segments:
        if merged and seg[0] - merged[-1][1] < MIN_OFF:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)
    return merged


def _resolve_overlaps(segments: list[tuple[float, float]],
                      policy: str = OVERLAP_POLICY,
                      tol: float = OVERLAP_TOL) -> tuple[list[tuple[float, float]], int]:
    """清理重叠段。轮流说话场景下，重叠=垃圾，默认整段丢弃。

    返回 (清理后的段, 丢弃段数)。trim 策略会截掉与前一段重叠的部分。
    """
    ordered = sorted((max(0.0, float(s)), float(e)) for s, e in segments if e > s)
    out: list[tuple[float, float]] = []
    dropped = 0
    for start, end in ordered:
        # 逐段回退，处理"新段比前一段长、丢掉前一段后仍重叠"的链式情况
        while out and start < out[-1][1] - tol:
            prev_start, prev_end = out[-1]
            if policy == "trim":
                start = max(start, prev_end)
                break
            # drop：丢短的那段（等长时丢当前段）
            if (end - start) <= (prev_end - prev_start):
                dropped += 1
                start = None
                break
            out.pop()
            dropped += 1
        if start is None:
            continue
        if end - start < MIN_ON:
            dropped += 1
            continue
        out.append((start, end))
    return out, dropped


def _raw_segments(samples: np.ndarray) -> list[tuple[float, float]]:
    sd = _get_segmenter()
    if sd is None:
        return _energy_segments(samples)
    with _segmenter_lock:
        result = sd.process(np.ascontiguousarray(samples, dtype=np.float32))
    return [(float(r.start), float(r.end)) for r in result.sort_by_start_time()]


def segment(samples: np.ndarray) -> list[tuple[float, float]]:
    """返回清洗后的语音段（已处理重叠）。保留这个公开函数给调试用。"""
    segs, _ = _resolve_overlaps(_raw_segments(samples))
    return segs


# ---------------------------------------------------------------- 聚类


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _merge_pairwise(sums: list[np.ndarray], counts: list[int], labels: list[int],
                    threshold: float) -> tuple[list[np.ndarray], list[int], list[int]]:
    """把质心仍然很像的簇两两合并（贪心顺序敏感的解药）。原地返回新列表。"""
    sums = list(sums)
    counts = list(counts)
    labels = list(labels)
    while len(sums) > 1:
        best_pair, best_sim = None, -1.0
        for i in range(len(sums)):
            ci = _normalize(sums[i])
            for j in range(i + 1, len(sums)):
                sim = float(np.dot(ci, _normalize(sums[j])))
                if sim > best_sim:
                    best_sim, best_pair = sim, (i, j)
        if best_pair is None or best_sim < threshold:
            break
        i, j = best_pair
        sums[i] = sums[i] + sums[j]
        counts[i] += counts[j]
        del sums[j], counts[j]
        labels = [lab - 1 if lab > j else lab for lab in labels]
        labels = [i if lab == j else lab for lab in labels]
    return sums, counts, labels


def cluster(embeddings: list[np.ndarray], threshold: float = MERGE_SIM,
            max_speakers: int = MAX_SPEAKERS) -> tuple[list[int], list[np.ndarray]]:
    """贪心最近质心聚类 + 质心二次合并。

    贪心是顺序敏感的（先来的样本会把质心带偏），所以聚完再把"质心仍然很像"
    的簇合并一次，结果稳定得多。
    """
    if not embeddings:
        return [], []

    sums: list[np.ndarray] = []
    counts: list[int] = []
    labels: list[int] = []
    for emb in embeddings:
        emb = _normalize(np.asarray(emb, dtype=np.float32))
        if not sums:
            sums.append(emb.copy())
            counts.append(1)
            labels.append(0)
            continue
        sims = [float(np.dot(emb, _normalize(s))) for s in sums]
        best = int(np.argmax(sims))
        if sims[best] >= threshold:
            labels.append(best)
            sums[best] = sums[best] + emb
            counts[best] += 1
        elif len(sums) < max_speakers:
            sums.append(emb.copy())
            counts.append(1)
            labels.append(len(sums) - 1)
        else:
            # 到上限就并进最像的那簇，避免"每人一句话"炸成几十个 spk
            labels.append(best)
            sums[best] = sums[best] + emb
            counts[best] += 1

    sums, counts, labels = _merge_pairwise(sums, counts, labels, threshold)

    # 按首次出现重排成 0..N-1，保证 spk_id 稳定
    remap: dict[int, int] = {}
    for lab in labels:
        if lab not in remap:
            remap[lab] = len(remap)
    labels = [remap[lab] for lab in labels]
    centroids = [_normalize(sums[old]) for old in sorted(remap, key=lambda k: remap[k])]
    return labels, centroids


def _absorb_small_clusters(embeddings: list[np.ndarray], labels: list[int],
                           centroids: list[np.ndarray], durations: list[float],
                           min_seconds: float) -> tuple[list[int], list[np.ndarray]]:
    """总时长太短的簇（噪声/碎片）并入最像的大簇。

    分段器偶尔会在一个人说完时切出 0.5s 的尾巴，单独聚类就会多出一个"陌生人"。
    轮流说话场景里这种段属于垃圾信息，直接并进最近的人。
    """
    if min_seconds <= 0 or len(centroids) <= 1:
        return labels, centroids
    labels = list(labels)
    centroids = [np.asarray(c, dtype=np.float32).copy() for c in centroids]
    while len(set(labels)) > 1:
        seconds: dict[int, float] = {}
        for lab, dur in zip(labels, durations):
            seconds[lab] = seconds.get(lab, 0.0) + dur
        bad = min(seconds, key=seconds.get)
        if seconds[bad] >= min_seconds:
            break
        sims = [(float(np.dot(centroids[bad], centroids[j])), j)
                for j in range(len(centroids)) if j != bad and j in seconds]
        if not sims:
            break
        target = max(sims)[1]
        labels = [target if lab == bad else lab for lab in labels]
        # 重新计算质心并按首次出现重排
        sums: dict[int, np.ndarray] = {}
        for emb, lab in zip(embeddings, labels):
            sums[lab] = sums.get(lab, np.zeros_like(emb)) + emb
        remap: dict[int, int] = {}
        for lab in labels:
            if lab not in remap:
                remap[lab] = len(remap)
        labels = [remap[lab] for lab in labels]
        centroids = [_normalize(sums[old]) for old in sorted(remap, key=lambda k: remap[k])]
    return labels, centroids


def _merge_close(segments: list[dict], gap: float = GAP_SECONDS) -> list[dict]:
    """相邻同人段合并（同一口气被切成两段的情况）。"""
    if not segments:
        return []
    out = [segments[0]]
    for seg in segments[1:]:
        prev = out[-1]
        if seg["spk_id"] == prev["spk_id"] and seg["start"] - prev["end"] <= gap:
            prev["end"] = seg["end"]
        else:
            out.append(dict(seg))
    return out


# ---------------------------------------------------------------- 主入口


def _empty(reason: str, duration: float, t0: float, dropped: int) -> dict:
    return {
        "duration": round(duration, 3),
        "speaker_count": 0,
        "speakers": [],
        "segments": [],
        "dropped_overlap": dropped,
        "reason": reason,
        "ms": int((time.time() - t0) * 1000),
        "_centroids": [],
    }


def diarize(samples: np.ndarray, people: list[dict] | None = None,
            threshold: float | None = None) -> dict:
    """一段音频 → 多人分离结果。

    people 传 store.voice_index()，即可把 spk_N 映射成实名（同一个人只认一次）。
    """
    t0 = time.time()
    if samples is None:
        return _empty("空音频", 0.0, t0, 0)
    samples = np.ascontiguousarray(np.asarray(samples, dtype=np.float32).reshape(-1))
    duration = float(samples.size) / SAMPLE_RATE if samples.size else 0.0
    if samples.size == 0:
        return _empty("空音频", 0.0, t0, 0)
    if threshold is not None:
        threshold = min(max(float(threshold), 0.1), 0.99)

    raw = _raw_segments(samples)
    segs, dropped = _resolve_overlaps(raw)
    if not segs:
        return _empty("没有检测到人声", duration, t0, dropped)

    # 逐段声纹；太短的段先留空，聚类后再挂到最近的簇
    items: list[dict] = []
    for start, end in segs:
        a, b = int(start * SAMPLE_RATE), int(end * SAMPLE_RATE)
        piece = samples[a:b]
        emb = None
        if piece.size / SAMPLE_RATE >= MIN_SEG_SECONDS:
            try:
                emb = voice.embed(piece)
            except Exception:  # 模型/底层异常不能把整场会议打挂
                emb = None
        items.append({"start": start, "end": end, "emb": emb})

    emb_items = [(it["emb"], it["end"] - it["start"])
                 for it in items if it["emb"] is not None]
    embeddings = [e for e, _ in emb_items]
    durations = [d for _, d in emb_items]
    if not embeddings:
        return _empty("语音段太短，无法提取声纹", duration, t0, dropped)

    labels, centroids = cluster(embeddings, threshold if threshold is not None else MERGE_SIM)
    labels, centroids = _absorb_small_clusters(
        embeddings, labels, centroids, durations, MIN_SPEAKER_SECONDS)
    label_iter = iter(labels)
    for it in items:
        if it["emb"] is not None:
            it["label"] = next(label_iter)
            continue
        # 短段：自己算一次声纹，贴到最近的簇
        piece = samples[int(it["start"] * SAMPLE_RATE): int(it["end"] * SAMPLE_RATE)]
        try:
            e = voice.embed(piece)
            sims = [float(np.dot(e, c)) for c in centroids]
            it["label"] = int(np.argmax(sims)) if sims else 0
        except Exception:
            it["label"] = 0

    raw_rows = [
        {"start": round(it["start"], 3), "end": round(it["end"], 3),
         "spk_id": f"spk_{it['label']}"}
        for it in items
    ]
    segments = _merge_close(raw_rows)

    # 实名映射：每个人最多认领一个簇，按相似度从高到低分配
    speakers: list[dict] = []
    if people:
        candidates = []
        for label, centroid in enumerate(centroids):
            person, sim, margin = voice.match(centroid, people)
            if person is not None:
                candidates.append((sim, margin, label, person))
        candidates.sort(reverse=True, key=lambda t: t[0])
        claimed: dict[int, dict] = {}
        for sim, margin, label, person in candidates:
            if person["id"] in {info["person"]["id"] for info in claimed.values()}:
                continue
            claimed[label] = {"person": person, "sim": sim, "margin": margin}
        for label in range(len(centroids)):
            info = claimed.get(label)
            speakers.append({
                "spk_id": f"spk_{label}",
                "person_id": info["person"]["id"] if info else None,
                "name": info["person"].get("name", "") if info else "陌生人",
                "similarity": round(info["sim"], 3) if info else None,
                "margin": round(info["margin"], 3) if info else None,
            })
    else:
        speakers = [{"spk_id": f"spk_{i}", "person_id": None, "name": "陌生人",
                     "similarity": None, "margin": None}
                    for i in range(len(centroids))]

    for spk in speakers:
        mine = [s for s in segments if s["spk_id"] == spk["spk_id"]]
        spk["seconds"] = round(sum(s["end"] - s["start"] for s in mine), 3)
        spk["segments"] = len(mine)

    return {
        "duration": round(duration, 3),
        "speaker_count": len(speakers),
        "speakers": speakers,
        "segments": segments,
        "dropped_overlap": dropped,
        "overlap_policy": OVERLAP_POLICY,
        "ms": int((time.time() - t0) * 1000),
        # 私有字段：每簇 192 维质心，供"绑定并录入声纹"用；对外 API 会剥掉。
        "_centroids": [c.tolist() for c in centroids],
    }


def diarize_wav(data: bytes, people: list[dict] | None = None) -> dict:
    return diarize(voice.read_wav(data), people)

"""自研聚类版多人分离：pyannote 分段 + CAM++ 逐段声纹 + 贪心聚类 + 人脉库实名映射。

比 sherpa 自带的 FastClustering 更可控，也方便把"匿名 spk"映射成"已登记的人"。
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import voice  # noqa: E402
from diarize_probe import EMB_MODEL, SEG_MODEL, build_meeting  # noqa: E402

MERGE_SIM = float(os.environ.get("AR_DIAR_MERGE_SIM", "0.75"))


def segment(samples: np.ndarray):
    """用 pyannote 分段模型拿语音段（忽略它自带的聚类结果）。"""
    import sherpa_onnx

    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=SEG_MODEL)
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=EMB_MODEL),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    return [(r.start, r.end) for r in sd.process(samples).sort_by_start_time()]


def cluster(embeddings: list[np.ndarray], threshold: float = MERGE_SIM):
    """贪心最近质心聚类：足够像就并进去，否则开新簇。返回每条 embedding 的簇号。"""
    centroids: list[np.ndarray] = []
    labels: list[int] = []
    for emb in embeddings:
        if not centroids:
            centroids.append(emb.copy())
            labels.append(0)
            continue
        sims = [float(np.dot(emb, c)) for c in centroids]
        best = int(np.argmax(sims))
        if sims[best] >= threshold:
            labels.append(best)
            # 增量更新质心后再归一化
            c = centroids[best] * (1.0 - 1.0 / (labels.count(best) + 1)) + emb / (labels.count(best) + 1)
            n = float(np.linalg.norm(c))
            centroids[best] = c / n if n else c
        else:
            centroids.append(emb.copy())
            labels.append(len(centroids) - 1)
    return labels, centroids


def main():
    samples, truth = build_meeting()
    segs = segment(samples)
    print(f"分段：{len(segs)} 段")
    embeddings = []
    for start, end, in segs:
        x = samples[int(start * 16000): int(end * 16000)]
        embeddings.append(voice.embed(x))
    labels, centroids = cluster(embeddings)

    print(f"聚类：{len(centroids)} 人 (merge_sim={MERGE_SIM})")
    mapping = {}
    for (start, end), lab in zip(segs, labels):
        mid = (start + end) / 2
        for t0, t1, gt, name in truth:
            if t0 <= mid <= t1:
                mapping.setdefault(gt, set()).add(lab)
                print(f"  {start:6.2f}-{end:6.2f}s  spk_{lab}   (真值 {gt})")
                break
    desc = " / ".join(f"{k}:{sorted(mapping.get(k, set()))}" for k in ("A", "B", "C"))
    print("\n真值 -> 簇:", desc)
    ok = len(mapping) == 3 and all(len(v) == 1 for v in mapping.values()) and len({next(iter(v)) for v in mapping.values()}) == 3
    print("结论：", "3 人可分且无串号" if ok else "仍有串号")

    # 簇质心映射到"已登记的人"：A/B/C 各取第一句当注册声纹
    enrolled = [{"id": lab, "name": lab, "role": "", "voice_embeddings": [emb]}
                for (_, lab, emb) in [(n, l, voice.embed(samples[int(t[0]*16000):int(t[1]*16000)]))
                                      for n, l, t in [("a", "A", (truth[0][0], truth[0][1])),
                                                      ("b", "B", (truth[1][0], truth[1][1])),
                                                      ("c", "C", (truth[2][0], truth[2][1]))]]]
    print("\n簇 -> 实名映射：")
    for i, c in enumerate(centroids):
        person, sim, margin = voice.match(c, enrolled)
        print(f"  spk_{i}: -> {person['name'] if person else '陌生人'} sim={sim:+.3f} margin={margin:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

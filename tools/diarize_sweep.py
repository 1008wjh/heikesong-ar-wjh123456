"""扫描 diarization 聚类参数，找出在"3 人会议"上不串号的组合。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diarize_probe import EMB_MODEL, SEG_MODEL, build_meeting  # noqa: E402


def run(threshold: float, clusters: int, min_on: float, min_off: float):
    import sherpa_onnx

    samples, truth = build_meeting()
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=SEG_MODEL)
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=EMB_MODEL),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=clusters, threshold=threshold),
        min_duration_on=min_on,
        min_duration_off=min_off,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    segments = [(r.start, r.end, r.speaker) for r in sd.process(samples).sort_by_start_time()]

    mapping = {}
    for start, end, spk in segments:
        mid = (start + end) / 2
        for t0, t1, label, _ in truth:
            if t0 <= mid <= t1:
                mapping.setdefault(label, set()).add(spk)
                break
    ok = (
        len(mapping) == 3
        and all(len(v) == 1 for v in mapping.values())
        and len({next(iter(v)) for v in mapping.values()}) == 3
    )
    return segments, mapping, ok


def main():
    print("thr  K  min_on min_off | segments | A / B / C | ok")
    best = None
    for clusters in (-1, 3):
        for threshold in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            segs, mapping, ok = run(threshold, clusters, 0.3, 0.5)
            desc = " / ".join(f"{k}:{sorted(mapping.get(k, set()))}" for k in ("A", "B", "C"))
            print(f"{threshold:4.1f} {clusters:2d}   0.3    0.5   | {len(segs):8d} | {desc} | {ok}")
            if ok and best is None:
                best = (threshold, clusters)
    print("\nbest:", best)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""多人分离 PoC：把 test/voice 里 3 个人的 6 段语音拼成"一场会议"，看离线 diarization 能不能分开。

用法：
    python tools/diarize_probe.py

依赖：
    pip install numpy sherpa-onnx
    models/diarization/pyannote-segmentation-3.0.onnx（已随仓库放在这里）
    models/campplus_zh_16k.onnx（仓库原有声纹模型，直接当 embedding 用）
"""

from __future__ import annotations

import os
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SEG_MODEL = os.path.join(ROOT, "models", "diarization", "pyannote-segmentation-3.0.onnx")
EMB_MODEL = os.path.join(ROOT, "models", "campplus_zh_16k.onnx")
VOICE_DIR = os.path.join(ROOT, "test", "voice")

# 6 段语音：3 个人，每人 2 句。拼起来就是一场"三人轮流说话"的会议。
ORDER = [
    ("huihui_a.wav", "A"),
    ("hazel_a.wav", "B"),
    ("zira_a.wav", "C"),
    ("huihui_b.wav", "A"),
    ("hazel_b.wav", "B"),
    ("zira_b.wav", "C"),
]
GAP_SECONDS = 0.6


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: only 16-bit PCM supported")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        raise ValueError(f"{path}: expected 16k, got {rate}")
    return x


def build_meeting():
    gap = np.zeros(int(GAP_SECONDS * 16000), dtype=np.float32)
    chunks, truth = [], []
    cursor = 0.0
    for name, label in ORDER:
        x = read_wav(os.path.join(VOICE_DIR, name))
        chunks.append(x)
        chunks.append(gap)
        dur = x.size / 16000.0
        truth.append((cursor, cursor + dur, label, name))
        cursor += dur + GAP_SECONDS
    return np.concatenate(chunks), truth


def main():
    required = [SEG_MODEL, EMB_MODEL]
    missing = [p for p in required if not os.path.isfile(p)]
    if missing:
        print("缺少模型：")
        for p in missing:
            print("  -", p)
        return 1

    import sherpa_onnx

    samples, truth = build_meeting()
    total = samples.size / 16000.0
    print(f"会议音频：{total:.1f}s，{len(truth)} 段，3 个说话人")
    for start, end, label, name in truth:
        print(f"  真值 {start:6.2f}-{end:6.2f}s  {label}  ({name})")

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=SEG_MODEL)
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=EMB_MODEL),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        config.validate()
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    print("sample_rate:", sd.sample_rate)

    result = sd.process(samples).sort_by_start_time()
    segments = [(r.start, r.end, r.speaker) for r in result]
    print(f"\n分离结果：{len(segments)} 段")
    for start, end, spk in segments:
        print(f"  {start:6.2f}-{end:6.2f}s  spk_{spk}")

    # 一致性检查：同一真值人是否被分到同一个 spk
    mapping = {}
    for start, end, spk in segments:
        mid = (start + end) / 2
        for t0, t1, label, _ in truth:
            if t0 <= mid <= t1:
                mapping.setdefault(label, set()).add(spk)
                break
    print("\n真值 -> 预测映射：")
    for label in ("A", "B", "C"):
        print(f"  {label}: {sorted(mapping.get(label, set()))}")

    ok = all(len(v) == 1 for v in mapping.values()) and len({next(iter(v)) for v in mapping.values()}) == len(mapping)
    print("\n结论：", "3 人可分且无串号" if ok else "存在串号或人数不符，需要调阈值/聚类")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

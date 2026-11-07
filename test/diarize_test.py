"""多人分离的单元 + 集成测试（不需要起服务）。

跑法：python test/diarize_test.py
依赖：test/voice/*.wav、models/campplus_zh_16k.onnx、models/diarization/pyannote-segmentation-3.0.onnx
"""

from __future__ import annotations

import os
import sys
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import diarize  # noqa: E402
import voice  # noqa: E402

VOICE_DIR = os.path.join(ROOT, "test", "voice")
ORDER = [
    ("huihui_a.wav", "A"),
    ("hazel_a.wav", "B"),
    ("zira_a.wav", "C"),
    ("huihui_b.wav", "A"),
    ("hazel_b.wav", "B"),
    ("zira_b.wav", "C"),
]
GAP = 0.6
ok_count = 0
fail_count = 0


def check(name, cond, extra=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  ok   {name} {extra}")
    else:
        fail_count += 1
        print(f"  FAIL {name} {extra}")


def read_wav(name):
    with wave.open(os.path.join(VOICE_DIR, name), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def build_meeting():
    gap = np.zeros(int(GAP * 16000), dtype=np.float32)
    parts, truth, cursor = [], [], 0.0
    for name, label in ORDER:
        x = read_wav(name)
        parts.extend([x, gap])
        truth.append((cursor, cursor + x.size / 16000.0, label))
        cursor += x.size / 16000.0 + GAP
    return np.concatenate(parts), truth


def test_cluster_unit():
    print("1) 聚类单元测试（3 个明显分开的方向）")
    rng = np.random.default_rng(7)
    bases = [rng.normal(size=192) for _ in range(3)]
    bases = [b / np.linalg.norm(b) for b in bases]
    embs = []
    for b in bases:
        for _ in range(3):
            embs.append(b + 0.003 * rng.normal(size=192))
    labels, centroids = diarize.cluster(embs, threshold=0.75)
    check("聚成 3 簇", len(centroids) == 3, str(len(centroids)))
    check("同源同簇", labels[0] == labels[1] == labels[2])
    check("异源不同簇", labels[0] != labels[3] and labels[3] != labels[6])


def test_merge_pairwise():
    print("2) 质心二次合并（解贪心顺序敏感）")
    v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([0.9, 0.43, 0.0], dtype=np.float32)
    sums, counts, labels = diarize._merge_pairwise([v1, v2], [1, 2], [0, 1], threshold=0.8)
    check("相似质心合并成 1 簇", len(sums) == 1, str(len(sums)))
    check("标签重映射", labels == [0, 0], str(labels))
    check("计数累加", counts == [3], str(counts))


def test_absorb_small_clusters():
    print("3) 碎片簇并入（0.5s 尾巴不能变成第 4 个人）")
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    noise = np.array([0.9, 0.1, 0.0], dtype=np.float32)  # 和 a 像，但时长短
    labels, centroids = diarize._absorb_small_clusters(
        [a, b, noise], [0, 1, 2], [diarize._normalize(a), diarize._normalize(b),
                                   diarize._normalize(noise)],
        [5.0, 4.0, 0.4], min_seconds=1.0)
    check("碎片被并进最近簇", len(centroids) == 2, str(len(centroids)))
    check("标签重排", labels == [0, 1, 0], str(labels))


def test_overlap():
    print("4) 重叠段清洗（轮流说话，重叠=垃圾）")
    segs, dropped = diarize._resolve_overlaps([(0.0, 2.0), (1.5, 4.0)], policy="drop")
    check("drop 丢掉短段", segs == [(1.5, 4.0)] and dropped == 1, f"{segs} dropped={dropped}")
    segs, dropped = diarize._resolve_overlaps([(0.0, 2.0), (1.5, 4.0)], policy="trim")
    check("trim 截掉重叠", len(segs) == 2 and abs(segs[1][0] - 2.0) < 1e-6 and dropped == 0, str(segs))
    segs, dropped = diarize._resolve_overlaps([(0.0, 2.0), (0.5, 1.0)], policy="drop")
    check("被包住的短段丢弃", segs == [(0.0, 2.0)] and dropped == 1, f"{segs}")
    segs, dropped = diarize._resolve_overlaps([(0.0, 2.0), (2.05, 4.0)])
    check("容差内不算重叠", len(segs) == 2 and dropped == 0, f"{segs}")


def test_silence():
    print("5) 静音/空音频")
    res = diarize.diarize(np.zeros(16000 * 2, dtype=np.float32))
    check("静音不出人", res["speaker_count"] == 0, str(res["speaker_count"]))
    check("给出人声原因", "人声" in (res.get("reason") or ""), str(res.get("reason")))
    res = diarize.diarize(np.zeros(0, dtype=np.float32))
    check("空音频原因", res.get("reason") == "空音频", str(res.get("reason")))


def test_meeting():
    print("6) 3 人会议分离（6 段语音拼接）")
    samples, truth = build_meeting()
    enrolled = []
    for label, idx in (("A", 0), ("B", 1), ("C", 2)):
        x = read_wav(ORDER[idx][0])
        enrolled.append({"id": "id_" + label, "name": label, "role": "",
                         "voice_embeddings": [voice.embed(x).tolist()]})

    result = diarize.diarize(samples, enrolled)
    check("时长约 35.8s", abs(result["duration"] - 35.8) < 1.0, str(result["duration"]))
    check("识别出 3 人", result["speaker_count"] == 3, str(result["speaker_count"]))
    check("分段数 >= 6", len(result["segments"]) >= 6, str(len(result["segments"])))
    check("干净音频无重叠丢弃", result["dropped_overlap"] == 0, str(result["dropped_overlap"]))

    # 每一段按真值时间核对：同一个真值人必须落在同一个 spk，且 spk 不串
    mapping = {}
    for seg in result["segments"]:
        mid = (seg["start"] + seg["end"]) / 2
        for t0, t1, label in truth:
            if t0 <= mid <= t1:
                mapping.setdefault(label, set()).add(seg["spk_id"])
                break
    check("3 个真值人都有段", len(mapping) == 3, str(sorted(mapping)))
    check("每人只对应一个 spk", all(len(v) == 1 for v in mapping.values()), str(mapping))
    check("3 个 spk 互不重复", len({next(iter(v)) for v in mapping.values()}) == 3, str(mapping))

    names = {s["name"] for s in result["speakers"]}
    check("实名映射到 A/B/C", names == {"A", "B", "C"}, str(names))
    check("质心为私有字段", "_centroids" in result and len(result["_centroids"]) == 3)
    check("质心维度 192", len(result["_centroids"][0]) == 192)


def test_noise():
    print("7) 加噪鲁棒性（近似真实会场底噪）")
    samples, truth = build_meeting()
    rng = np.random.default_rng(11)
    noise = rng.normal(0.0, 0.002, size=samples.shape).astype(np.float32)
    noisy = samples + noise
    enrolled = []
    for label, idx in (("A", 0), ("B", 1), ("C", 2)):
        x = read_wav(ORDER[idx][0])
        enrolled.append({"id": "id_" + label, "name": label, "role": "",
                         "voice_embeddings": [voice.embed(x).tolist()]})
    result = diarize.diarize(noisy, enrolled)
    check("加噪后仍是 3 人", result["speaker_count"] == 3, str(result["speaker_count"]))
    mapping = {}
    for seg in result["segments"]:
        mid = (seg["start"] + seg["end"]) / 2
        for t0, t1, label in truth:
            if t0 <= mid <= t1:
                mapping.setdefault(label, set()).add(seg["spk_id"])
                break
    check("加噪后不串号", len(mapping) == 3 and all(len(v) == 1 for v in mapping.values()), str(mapping))


def main():
    test_cluster_unit()
    test_merge_pairwise()
    test_absorb_small_clusters()
    test_overlap()
    test_silence()
    test_meeting()
    test_noise()
    print(f"\n{ok_count} passed, {fail_count} failed")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""用真实录音评估多人分离（把"测试通过"变成"在真数据上多少分"）。

准备：
  1. 一段 16k 单声道 wav（手机/眼镜录的会议，轮流说话）
  2. 人工标注 JSON：
     {"segments":[{"start":0.0,"end":3.2,"speaker":"A"},
                  {"start":3.6,"end":7.1,"speaker":"B"}]}
     （可以先用 mac 的 Audacity / 任何能看波形的工具粗标）

跑：
  python tools/eval_diarization.py --wav meeting.wav --labels meeting.json

输出：
  - 预测人数 vs 真实人数
  - 预测 spk 到真实 speaker 的映射
  - 按"重叠时长"加权的时间准确率（只统计有人说话的部分）
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import diarize  # noqa: E402
import voice  # noqa: E402


def load_labels(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("segments", data) if isinstance(data, dict) else data
    out = []
    for row in rows:
        out.append({"start": float(row["start"]), "end": float(row["end"]),
                    "speaker": str(row["speaker"])})
    out.sort(key=lambda r: r["start"])
    return out


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--merge-sim", type=float, default=None)
    args = parser.parse_args()

    with open(args.wav, "rb") as f:
        samples = voice.read_wav(f.read())
    truth = load_labels(args.labels)
    result = diarize.diarize(samples, threshold=args.merge_sim)

    pred = result["segments"]
    true_speakers = sorted({r["speaker"] for r in truth})
    print(f"音频：{result['duration']:.1f}s  预测 {result['speaker_count']} 人 / 标注 {len(true_speakers)} 人")
    print(f"分段：预测 {len(pred)} 段 / 标注 {len(truth)} 段；丢弃重叠 {result.get('dropped_overlap', 0)} 段")

    # 预测 spk -> 真实 speaker（按重叠时长投票）
    votes = {}
    for p in pred:
        for t in truth:
            ov = overlap(p["start"], p["end"], t["start"], t["end"])
            if ov > 0:
                votes.setdefault(p["spk_id"], {}).setdefault(t["speaker"], 0.0)
                votes[p["spk_id"]][t["speaker"]] += ov
    mapping = {spk: max(by.items(), key=lambda kv: kv[1])[0] for spk, by in votes.items()}

    total = sum(t["end"] - t["start"] for t in truth)
    correct = 0.0
    for p in pred:
        best_spk = mapping.get(p["spk_id"])
        for t in truth:
            ov = overlap(p["start"], p["end"], t["start"], t["end"])
            if ov > 0 and t["speaker"] == best_spk:
                correct += ov
    acc = correct / total if total else 0.0

    print("\n映射（预测 -> 标注）：")
    for spk in sorted(mapping):
        print(f"  {spk} -> {mapping[spk]}")
    print(f"\n按重叠时长加权的时间准确率：{acc * 100:.1f}%")
    print("（只统计有人说话的区间；重叠段被丢弃，所以不会超过 100%）")

    if len(set(mapping.values())) < len(mapping):
        print("\n警告：多个预测 spk 映射到了同一个标注人，可能有并簇。")
    if result["speaker_count"] != len(true_speakers):
        print(f"\n警告：人数不一致（预测 {result['speaker_count']} / 标注 {len(true_speakers)}），建议调 AR_DIAR_MERGE_SIM。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

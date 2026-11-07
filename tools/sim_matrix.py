"""诊断：6 段测试语音两两余弦相似度 +  utterance 级聚类是否可分。"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import voice  # noqa: E402
from diarize_probe import ORDER, VOICE_DIR, read_wav  # noqa: E402


def main():
    items = []
    for name, label in ORDER:
        x = read_wav(os.path.join(VOICE_DIR, name))
        items.append((name, label, voice.embed(x)))

    print("两两余弦相似度（行/列按顺序）")
    print("        " + " ".join(f"{n[:8]:>8}" for n, _, _ in items))
    for i, (name, label, emb) in enumerate(items):
        row = " ".join(f"{float(np.dot(emb, e)):8.3f}" for _, _, e in items)
        print(f"{label} {name[:8]:>10} {row}")

    print("\n同类 vs 异类:")
    same, diff = [], []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            s = float(np.dot(items[i][2], items[j][2]))
            (same if items[i][1] == items[j][1] else diff).append(s)
    print(f"  同人 均值={np.mean(same):.3f} 最小={np.min(same):.3f} 最大={np.max(same):.3f}")
    print(f"  异人 均值={np.mean(diff):.3f} 最小={np.min(diff):.3f} 最大={np.max(diff):.3f}")

    # 用仓库现有阈值做 1:N 是否为 3 人可分
    people = [{"id": l, "name": l, "role": "", "voice_embeddings": [e]}
              for l, _, e in [(items[0][1], None, items[0][2]), (items[1][1], None, items[1][2]), (items[2][1], None, items[2][2])]]
    people = [{"id": lab, "name": lab, "role": "", "voice_embeddings": [emb]}
              for (_, lab, emb) in items[:3]]
    print("\n1:N 识别（每人第一句注册，六句全部测试）:")
    for name, label, emb in items:
        person, sim, margin = voice.match(emb, people)
        print(f"  {label} {name}: -> {person['name'] if person else 'unknown'}  sim={sim:+.3f} margin={margin:+.3f}")


if __name__ == "__main__":
    main()

"""细扫 0.20~0.30 的聚类阈值，覆盖 3 人测试会议。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diarize_probe import build_meeting  # noqa: E402
from diarize_sweep import run  # noqa: E402


def main():
    print("thr  K  | segments | A / B / C | ok")
    best = []
    for clusters in (-1, 3):
        t = 0.20
        while t <= 0.3001:
            segs, mapping, ok = run(round(t, 3), clusters, 0.3, 0.5)
            desc = " / ".join(f"{k}:{sorted(mapping.get(k, set()))}" for k in ("A", "B", "C"))
            print(f"{t:4.2f} {clusters:2d} | {len(segs):8d} | {desc} | {ok}")
            if ok:
                best.append((round(t, 3), clusters))
            t += 0.01
    print("\n可行参数:", best)


if __name__ == "__main__":
    main()

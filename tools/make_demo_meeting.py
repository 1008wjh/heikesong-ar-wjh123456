"""生成一段「3 人轮流说话」的演示会议 wav + 标注 JSON，方便直接试模块 03。

用的是仓库 test/voice 里的合成语音（TTS），只用于演示流程；
真实效果请换成真人录音。

用法：
    python tools/make_demo_meeting.py
    python tools/make_demo_meeting.py --out D:\\demo\\meeting.wav

输出：
    <out>.wav         16k 单声道，3 人各 2 句
    <out>.labels.json 与 wav 对应的标注（谁在什么时候说）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VOICE = os.path.join(ROOT, "test", "voice")
GAP = 0.6
ORDER = [
    ("huihui_a.wav", "A"),
    ("hazel_a.wav", "B"),
    ("zira_a.wav", "C"),
    ("huihui_b.wav", "A"),
    ("hazel_b.wav", "B"),
    ("zira_b.wav", "C"),
]


def read_wav(path):
    with wave.open(path, "rb") as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: 只支持 16-bit PCM")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        raise SystemExit(f"{path}: 需要 16k，实际 {rate}")
    return x


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "test", "demo_meeting.wav"))
    args = parser.parse_args()
    out_wav = os.path.abspath(args.out)
    out_labels = os.path.splitext(out_wav)[0] + ".labels.json"
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)

    gap = np.zeros(int(GAP * 16000), dtype=np.float32)
    parts, labels, cursor = [], [], 0.0
    for name, speaker in ORDER:
        path = os.path.join(VOICE, name)
        if not os.path.isfile(path):
            raise SystemExit(f"缺少测试音频: {path}")
        x = read_wav(path)
        parts.extend([x, gap])
        labels.append({"start": round(cursor, 3),
                       "end": round(cursor + x.size / 16000, 3),
                       "speaker": speaker})
        cursor += x.size / 16000 + GAP

    samples = np.concatenate(parts)
    pcm = np.clip(samples * 32768, -32768, 32767).astype("<i2")
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    with open(out_labels, "w", encoding="utf-8") as f:
        json.dump({"segments": labels}, f, ensure_ascii=False, indent=1)

    print(f"wav    : {out_wav}  ({samples.size / 16000:.1f}s)")
    print(f"labels : {out_labels}")
    print("\n下一步：")
    print(f"  python tools\\eval_diarization.py --wav \"{out_wav}\" --labels \"{out_labels}\"")
    print("  或打开控制台 → 模块 03 → 新建会议 → 上传上面的 wav → 生成分离")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

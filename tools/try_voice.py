"""拿一个 WAV 文件测声纹：不戴眼镜、不开模拟器，也能验证"这条声纹会认成谁"。

用法：
    python tools/try_voice.py test/voice/huihui_b.wav
    python tools/try_voice.py 我的录音.wav --backend http://127.0.0.1:8765

WAV 要求：16kHz 单声道 16bit。Windows 可以用「录音机」录完再用 ffmpeg 转，
或者直接在看效果时不录音，用 test/voice/ 里现成的几条。
"""
import argparse
import base64
import json
import sys
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    args = ap.parse_args()

    try:
        raw = open(args.wav, "rb").read()
    except OSError as exc:
        print(f"读不到 {args.wav}：{exc}")
        return 1

    body = json.dumps({
        "audio": "data:audio/wav;base64," + base64.b64encode(raw).decode()
    }).encode()
    req = urllib.request.Request(
        args.backend.rstrip("/") + "/api/voice/scan",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        data = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as exc:  # noqa: BLE001 - 这是给人看的脚本
        print(f"请求失败：{exc}")
        return 1

    face = (data.get("faces") or [None])[0]
    if not face:
        print("没结果")
        return 1

    status = face.get("status")
    if status == "short":
        print(f"太短了（{face.get('seconds', '?')} 秒）——至少要说 1 秒")
        return 0

    print(f"{status:<8} name={face.get('name') or '（陌生人）':<6} "
          f"sim={face.get('similarity', 0):.3f}  {data.get('ms')} ms")
    for line in face.get("lines") or []:
        print("   " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""下载多人识别用的 pyannote 分段模型（5.72MB）。

模型已随仓库放在 models/diarization/ 下，正常克隆不需要跑这个脚本；
只有模型丢失、或想验证校验和时用：

    python tools/fetch_diarization_model.py           # 缺了才下
    python tools/fetch_diarization_model.py --force   # 强制重下
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEST = os.path.join(ROOT, "models", "diarization", "pyannote-segmentation-3.0.onnx")
URL = ("https://hf-mirror.com/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0"
       "/resolve/main/model.onnx")
SHA256 = "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    force = "--force" in sys.argv
    os.makedirs(os.path.dirname(DEST), exist_ok=True)

    if os.path.isfile(DEST) and not force:
        actual = sha256(DEST)
        if actual == SHA256:
            print(f"已存在且校验通过: {DEST}")
            return 0
        print(f"校验不匹配，重新下载。actual={actual}")

    tmp = DEST + ".part"
    print(f"下载 {URL}")
    urllib.request.urlretrieve(URL, tmp)
    actual = sha256(tmp)
    if actual != SHA256:
        os.remove(tmp)
        print(f"下载文件校验失败: {actual}")
        return 1
    os.replace(tmp, DEST)
    print(f"完成: {DEST} ({os.path.getsize(DEST) / 1024 / 1024:.2f} MB, sha256={actual})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

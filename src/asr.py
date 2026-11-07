"""ASR + 说话人分离的"云端适配器"预留层。

仓库现状是纯离线：声纹用 CAM++，没有语音转文字。本地那份《开发者文档》要求
"阿里云/腾讯云/火山引擎 ASR（开启说话人分离）→ LLM 纪要"，所以这里先把接口
和配置约定定下来，接不接、用哪家由环境变量决定；没配置时完全不影响离线链路。

统一契约：
    provider.transcribe(wav_path) -> [
        {"spk_id": "spk_0"|None, "start": 12.3, "end": 16.8, "text": "..."},
        ...
    ]
    provider.available -> bool
    provider.state     -> "ready" | "not-configured" | "error"

环境变量：
    AR_ASR_PROVIDER = off | aliyun | tencent | volcano   （默认 off）
    AR_ASR_APP_KEY / AR_ASR_ACCESS_KEY / AR_ASR_SECRET  各家通用命名
    AR_ASR_REGION（可选）
    AR_ASR_ENDPOINT（可选，私有化/代理）
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class AsrNotConfigured(RuntimeError):
    pass


@dataclass
class AsrSegment:
    start: float
    end: float
    text: str
    spk_id: str | None = None

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "text": self.text, "spk_id": self.spk_id}


class AsrProvider:
    """所有厂商适配器的基类。"""

    name = "base"

    def __init__(self) -> None:
        self.state = "not-configured"
        self.detail = ""

    @property
    def available(self) -> bool:
        return self.state == "ready"

    def transcribe(self, wav_path: str) -> list[dict]:
        raise AsrNotConfigured(f"{self.name} ASR 未配置：{self.detail or '缺少密钥/开关'}")


class NullAsr(AsrProvider):
    """默认实现：不说话。离线链路完全不需要它。"""

    name = "off"

    def __init__(self) -> None:
        super().__init__()
        self.state = "not-configured"
        self.detail = "AR_ASR_PROVIDER=off（默认，纯离线）"


class _CloudAsr(AsrProvider):
    """云端 ASR 的公共壳：检查配置、给出可读状态；真正的 HTTP 调用留给接入时补。"""

    env_keys: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        missing = [k for k in self.env_keys if not os.environ.get(k)]
        if missing:
            self.state = "not-configured"
            self.detail = "缺少环境变量: " + ", ".join(missing)
        else:
            # 密钥齐了，但这一层只是预留：不要假装 ready，否则 finalize 一定进 error
            self.state = "reserved"
            self.detail = "密钥已配置，但厂商 HTTP 调用尚未实现（多人链路优先用 src/llm.py 的 ASR）"

    def transcribe(self, wav_path: str) -> list[dict]:
        if not self.available:
            raise AsrNotConfigured(f"{self.name} ASR 未配置：{self.detail}")
        # TODO(接入时): 按各厂商文档提交长音频任务 → 轮询 → 归一化成统一契约。
        raise AsrNotConfigured(
            f"{self.name} ASR 接口已预留，但尚未实现 HTTP 调用；"
            "离线多说话人链路不受影响。"
        )


class AliyunAsr(_CloudAsr):
    name = "aliyun"
    env_keys = ("AR_ASR_ACCESS_KEY", "AR_ASR_SECRET")


class TencentAsr(_CloudAsr):
    name = "tencent"
    env_keys = ("AR_ASR_SECRET_ID", "AR_ASR_SECRET_KEY")


class VolcanoAsr(_CloudAsr):
    name = "volcano"
    env_keys = ("AR_ASR_APP_KEY", "AR_ASR_ACCESS_TOKEN")


PROVIDERS = {
    "off": NullAsr,
    "aliyun": AliyunAsr,
    "tencent": TencentAsr,
    "volcano": VolcanoAsr,
}


def get_provider(name: str | None = None) -> AsrProvider:
    key = (name or os.environ.get("AR_ASR_PROVIDER", "off")).strip().lower()
    cls = PROVIDERS.get(key, NullAsr)
    return cls()


def attach_text(segments: list[dict], asr_segments: list[dict]) -> list[dict]:
    """把 ASR 文本按时间重叠贴到 diarization 的分段上。

    diarization 给"谁在什么时候说"，ASR 给"说了什么"。两边切法不同，
    所以按中心点/重叠时长取最匹配的一条；一条 ASR 文本可能被拆给多个段。
    """
    for seg in segments:
        mid = (seg["start"] + seg["end"]) / 2
        best, best_overlap = None, 0.0
        for a in asr_segments:
            overlap = min(seg["end"], a["end"]) - max(seg["start"], a["start"])
            if overlap > best_overlap:
                best, best_overlap = a, overlap
        if best is None:
            for a in asr_segments:
                if a["start"] <= mid <= a["end"]:
                    best = a
                    break
        seg["text"] = (best or {}).get("text", "")
        if (best or {}).get("spk_id"):
            seg["asr_spk_id"] = best["spk_id"]
    return segments

r"""大模型接入：语音转写（ASR）+ 单人对话要点提取（LLM）。

两家能力是**分开配**的，因为不是每个厂商都两样都有：

| 能力 | 谁来做 | 环境变量 |
|---|---|---|
| 转写 | 本地 sherpa-onnx / 任意 OpenAI 兼容 `/audio/transcriptions` | `AR_ASR_*` |
| 析出 | 任意 OpenAI 兼容 `/chat/completions`（MiMo、DeepSeek、OpenAI…） | `AR_LLM_*` |

几种常见配法：

```powershell
# A. 全用小米 MiMo（默认）
$env:MIMO_API_KEY = "sk-..."; python src\server.py

# B. 转写走本地、析出走 DeepSeek（不需要 MiMo 的 key）
$env:AR_ASR_PROVIDER = "local"
$env:DEEPSEEK_API_KEY = "sk-..."
$env:AR_LLM_PRESET = "deepseek"
python src\server.py

# C. 转写用 OpenAI whisper、析出用 DeepSeek
$env:AR_ASR_PROVIDER = "http"
$env:AR_ASR_BASE = "https://api.openai.com/v1"
$env:AR_ASR_KEY = "sk-..."
$env:AR_ASR_MODEL = "whisper-1"
$env:DEEPSEEK_API_KEY = "sk-..."
$env:AR_LLM_PRESET = "deepseek"
```

设计原则：
- 没配 Key 时**不假装能用**：available() 返回 False，接口回 400 并说清楚缺什么。
- 只在测试里用 `AR_LLM_STUB=1` 走假数据（返回里带 "stub": true）。
- 不在这里做持久化，写入都回 store。
"""

import json
import os
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 文本模型预设

_PRESETS = {
    # name: (base, model)
    "mimo": ("https://api.xiaomimimo.com/v1", "mimo-v2.6-pro"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
}

STUB = os.environ.get("AR_LLM_STUB", "") == "1"
TIMEOUT = float(os.environ.get("AR_LLM_TIMEOUT", "60"))


def _env(*names, default=""):
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return default


# ---- 析出（chat）
LLM_PRESET = _env("AR_LLM_PRESET", default="").lower()
_base, _model = _PRESETS.get(LLM_PRESET, ("", ""))
LLM_BASE = _env("AR_LLM_BASE", "MIMO_BASE", default=_base or "https://api.xiaomimimo.com/v1")
LLM_MODEL = _env("AR_LLM_MODEL", "MIMO_MODEL", default=_model or "mimo-v2.6-pro")
LLM_KEY = _env(
    "AR_LLM_KEY",
    "MIMO_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
)

# ---- 转写（ASR）
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASR_SENSE = os.path.join(HERE, "models", "asr", "sense-voice")
ASR_DIR = _env("AR_ASR_DIR", default=(
    _ASR_SENSE if os.path.exists(os.path.join(_ASR_SENSE, "model.int8.onnx"))
    else os.path.join(HERE, "models", "asr", "paraformer-zh-small")))
ASR_LOCAL_MODEL = os.path.join(ASR_DIR, "model.int8.onnx")
ASR_LOCAL_TOKENS = os.path.join(ASR_DIR, "tokens.txt")

ASR_PROVIDER = _env("AR_ASR_PROVIDER", default="").lower()
if not ASR_PROVIDER:
    if os.path.exists(ASR_LOCAL_MODEL) and os.path.exists(ASR_LOCAL_TOKENS):
        ASR_PROVIDER = "local"
    elif _env("AR_ASR_KEY", "MIMO_API_KEY"):
        ASR_PROVIDER = "http"
    else:
        ASR_PROVIDER = "none"

ASR_BASE = _env("AR_ASR_BASE", "MIMO_BASE", default="https://api.xiaomimimo.com/v1")
ASR_MODEL = _env("AR_ASR_MODEL", "MIMO_ASR_MODEL", default="mimo-v2.5-asr")
ASR_KEY = _env("AR_ASR_KEY", "MIMO_API_KEY")
ASR_LANG = _env("AR_ASR_LANGUAGE", default="zh")

# 有 LLM key 就能析出；本地转写不需要 key
LLM_READY = bool(LLM_KEY) or STUB
ASR_READY = ASR_PROVIDER == "local" or (ASR_PROVIDER == "http" and bool(ASR_KEY)) or STUB


class LLMUnavailable(RuntimeError):
    """没配 Key / 请求失败：调用方应当当成"功能没开"，而不是"程序坏了"。"""


def available():
    return LLM_READY


def asr_available():
    return ASR_READY


def status():
    return {
        "available": available(),
        "asrAvailable": asr_available(),
        "stub": STUB,
        "model": LLM_MODEL,
        "base": LLM_BASE,
        "asr": ASR_MODEL if ASR_PROVIDER == "http" else ("sense-voice" if os.path.basename(ASR_DIR).startswith("sense") else "paraformer-zh-small"),
        "asrProvider": ASR_PROVIDER,
        "asrBase": ASR_BASE if ASR_PROVIDER == "http" else "local",
        "preset": LLM_PRESET or "custom",
    }


# ---------------------------------------------------------------- 本地 ASR

_local_recognizer = None
_local_error = ""


def _recognizer():
    """懒加载 sherpa-onnx 的 paraformer；第一次调用大约 200ms。"""
    global _local_recognizer, _local_error
    if _local_recognizer is not None:
        return _local_recognizer
    try:
        import sherpa_onnx  # 只有本地转写才需要
    except Exception as e:  # pragma: no cover - 环境问题
        _local_error = "没装 sherpa-onnx，本地转写用不了：%s" % e
        raise LLMUnavailable(_local_error)
    if not (os.path.exists(ASR_LOCAL_MODEL) and os.path.exists(ASR_LOCAL_TOKENS)):
        _local_error = "本地 ASR 模型不在 %s（要 model.int8.onnx 和 tokens.txt）" % ASR_DIR
        raise LLMUnavailable(_local_error)
    # 放的是 sense-voice 就用 sense-voice，放的是 paraformer 就用 paraformer
    # （目录里有 sense-voice 的标记文件就优先，见 tools/get_asr_model.ps1 -Kind sensevoice）
    if os.path.exists(os.path.join(ASR_DIR, "sense-voice")):
        _local_recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=ASR_LOCAL_MODEL,
            tokens=ASR_LOCAL_TOKENS,
            num_threads=int(os.environ.get("AR_ASR_THREADS", "4")),
            use_itn=True,          # 带标点、数字规整
            debug=False,
            language="auto",
        )
    else:
        _local_recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=ASR_LOCAL_MODEL,
            tokens=ASR_LOCAL_TOKENS,
            num_threads=int(os.environ.get("AR_ASR_THREADS", "4")),
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
        )
    return _local_recognizer


def _local_transcribe(wav_bytes):
    import voice  # 复用同一个 wav 解码，保证 16k 单声道 float32

    samples = voice.read_wav(wav_bytes)
    if samples is None or len(samples) < 1600:  # < 0.1s
        raise LLMUnavailable("这段音频太短，听不出字")
    rec = _recognizer()
    # 长音频切窗解码：paraformer-small 直接吃 55 秒会吐空，切到 25 秒一段最稳
    win = 25 * 16000
    if len(samples) > win:
        parts = []
        for i in range(0, len(samples), win):
            seg = samples[i:i + win]
            if len(seg) < 1600:
                continue
            st = rec.create_stream()
            st.accept_waveform(16000, seg)
            rec.decode_stream(st)
            t = (st.result.text or '').strip()
            if t:
                parts.append(t)
        return ''.join(parts)
    stream = rec.create_stream()
    stream.accept_waveform(16000, samples)
    rec.decode_stream(stream)
    return (getattr(stream.result, "text", "") or "").strip()


# ---------------------------------------------------------------- HTTP 调用


def _post_json(path, payload):
    req = urllib.request.Request(
        LLM_BASE.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + LLM_KEY,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMUnavailable(f"模型接口返回 {e.code}：{detail}")
    except Exception as e:
        raise LLMUnavailable(f"模型接口连不上：{e}")


def _post_multipart(path, fields, files):
    """最小 multipart 实现，避免为了一个 ASR 接口引入 requests。"""
    boundary = "----memorylens" + os.urandom(8).hex()
    body = bytearray()
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += str(v).encode("utf-8") + b"\r\n"
    for k, (filename, data, ctype) in files.items():
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="{k}"; filename="{filename}"\r\n'
        ).encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        ASR_BASE.rstrip("/") + path,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": "Bearer " + ASR_KEY,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMUnavailable(f"转写接口返回 {e.code}：{detail}")
    except Exception as e:
        raise LLMUnavailable(f"转写接口连不上：{e}")


# ---------------------------------------------------------------- 对外两个能力


def transcribe(wav_bytes):
    """一段 wav → 文字。"""
    if not wav_bytes:
        raise LLMUnavailable("这段音频是空的")
    if STUB:
        return "（stub 转写）我们上次说的那个报价单，我这两天整理一下发你"
    if ASR_PROVIDER == "local":
        text = _local_transcribe(wav_bytes)
        if not text:
            raise LLMUnavailable("这段音频没听出字")
        return text
    if ASR_PROVIDER == "http":
        if not ASR_KEY:
            raise LLMUnavailable("转写配成 http 了但没给 AR_ASR_KEY")
        data = _post_multipart(
            "/audio/transcriptions",
            {"model": ASR_MODEL, "language": ASR_LANG},
            {"file": ("chunk.wav", wav_bytes, "audio/wav")},
        )
        text = (data.get("text") or "").strip()
        if not text:
            raise LLMUnavailable("这段音频没听出字")
        return text
    raise LLMUnavailable(
        "没有可用的转写通道：放一份本地模型到 models/asr/，或配 AR_ASR_KEY"
    )


PROMPT = """你在帮一个人整理他和某个人的对话记录。只根据给定对话原文提取信息，不要编。

要求：
1) 只提取**关于对面这个人**的事实：答应过什么、偏好、近况、关系、重要日期、需要跟进的事。
2) 每条要点写成一句短话，不超过 26 个字，能直接放进名片。
3) 必须有原文依据；原文没提到的一律不要写。
4) 最多 6 条，按重要性排序。
5) 如果对话里没有可用信息，items 返回空数组。

只输出 JSON，格式：
{"summary":"一句话概括这次对话","items":[{"key":"答应的事","value":"下周给报价单","quote":"原话片段"}]}

key 只能从这里选：答应的事 / 偏好 / 近况 / 关系 / 日期 / 跟进 / 其他"""


def _chat_plain(system, user, max_tokens=64, temperature=0.0):
    """只要一句短回答的调用（分类、选人）。"""
    if STUB:
        raise LLMUnavailable("stub 模式没有这个能力")
    if not LLM_KEY:
        raise LLMUnavailable("没配文本模型 Key")
    data = _post_json(
        "/chat/completions",
        {
            "model": LLM_MODEL,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
    )
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        raise LLMUnavailable("模型返回格式看不懂")


def classify_mode(transcript):
    """这段对话是单人对谈还是多人会议？只回 single / meeting。"""
    out = _chat_plain(
        "判断下面这段对话是【一个人和对面一个人】在聊，还是【三个及以上的人】在开会。"
        "只回一个词：single 或 meeting。看不出来就回 single。",
        (transcript or "")[:1500],
        max_tokens=4,
    ).lower()
    return "meeting" if "meeting" in out else "single"


def pick_person(transcript, names):
    """从名单里挑出用户提到的那个（"刚才那个产品经理"）。挑不出返回 None。"""
    if not names:
        return None
    out = _chat_plain(
        "从候选名单里，选出对话中提到的那个人。只回名字本身；判断不出来就回 none。\n"
        "候选：" + "、".join(names[:30]),
        (transcript or "")[:800],
        max_tokens=12,
    )
    out = out.strip().strip("。.「」\"'")
    for name in names:
        if name and name in out:
            return name
    return None


def recommend(transcript, person_name, profile, items):
    """下一步该做什么：给三条可点的行动建议（要贴着这个人的档案说）。"""
    who = person_name or "一个刚认识的陌生人"
    facts = profile or "（没有档案）"
    done = "；".join("%s：%s" % (i.get("key", ""), i.get("value", "")) for i in (items or [])[:6]) or "（这段还没抽出要点）"
    out = _chat_plain(
        "你在帮用户和对面的人聊天。根据这个人的档案和这一段对话，给出三条「下一步可以做什么」的短建议。"
        "要求：每条不超过 12 个字，动词开头，能直接照着做；不要解释；只输出 JSON。\n"
        '格式：{"summary":"一句话总结这段对话","next":["建议1","建议2","建议3"]}',
        "对面的人：%s\n档案：%s\n这段的要点：%s\n对话原文：%s" % (who, facts, done, (transcript or "")[-1200:]),
        max_tokens=180,
    )
    obj = _loads_loose(out) or {}
    nxt = [str(x).strip()[:14] for x in (obj.get("next") or []) if str(x).strip()][:3]
    return {"summary": str(obj.get("summary") or "")[:60], "next": nxt}


def analyze(transcript, person):
    """对话原文 → {summary, items[]}。person 只用 name/role 做上下文。"""
    transcript = (transcript or "").strip()
    if len(transcript) < 6:
        raise LLMUnavailable("对话太短，先多说几句")
    if STUB:
        return {
            "summary": "（stub）对方答应下周给报价单",
            "items": [
                {"key": "答应的事", "value": "下周给报价单", "quote": "我这两天整理一下发你"},
                {"key": "跟进", "value": "下周三前问一次", "quote": "下周给报价单"},
            ],
            "stub": True,
        }
    if not LLM_KEY:
        raise LLMUnavailable("没配文本模型 Key（AR_LLM_KEY / MIMO_API_KEY / DEEPSEEK_API_KEY）")

    ctx = "对面这个人：%s（%s）" % (person.get("name", "?"), person.get("role") or "身份未知")
    data = _post_json(
        "/chat/completions",
        {
            "model": LLM_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": ctx + "\n\n对话原文：\n" + transcript[-6000:]},
            ],
        },
    )
    content = ""
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        raise LLMUnavailable("模型回的格式不对")

    obj = _loads_loose(content)
    if not isinstance(obj, dict):
        raise LLMUnavailable("模型没按 JSON 回，换个说法再试一次")

    items = []
    seen = []
    for it in (obj.get("items") or [])[:8]:
        if not isinstance(it, dict):
            continue
        value = str(it.get("value") or "").strip()
        if not value:
            continue
        # 同一件事常被模型同时归到「答应的事」和「日期」，按内容去一次重
        norm = "".join(ch for ch in value if ch not in " ，。、,.:：!！?？")
        if any(norm in s or s in norm for s in seen):
            continue
        seen.append(norm)
        items.append(
            {
                "key": str(it.get("key") or "其他").strip()[:8],
                "value": value[:40],
                "quote": str(it.get("quote") or "").strip()[:60],
            }
        )
        if len(items) >= 6:
            break
    return {"summary": str(obj.get("summary") or "").strip()[:80], "items": items}


def _loads_loose(text):
    """模型偶尔会在 JSON 前后加 ```json 或解释，尽量捞出来。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None

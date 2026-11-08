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

import ast
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

import calllog

try:                                   # 有 requests 就用连接池，省掉每次 TLS 握手
    import requests
    _SESSION = requests.Session()
except Exception:                      # 没装就退回 urllib
    requests = None
    _SESSION = None

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


# 运行统计：给 /api/agent/status 和演示日志看，不参与业务逻辑
_STATS = {"calls": 0, "failures": 0, "retries": 0, "fallbacks": 0,
          "last_ms": 0, "last_error": ""}
_STATS_LOCK = threading.Lock()


def _stat(**kv):
    with _STATS_LOCK:
        for k, v in kv.items():
            if k in ("calls", "failures", "retries", "fallbacks"):
                _STATS[k] += int(v)
            else:
                _STATS[k] = v


def stats():
    with _STATS_LOCK:
        return dict(_STATS)


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
        "meetingAnswerModel": (os.environ.get("AR_MEETING_ANSWER_MODEL") or "").strip() or LLM_MODEL,
        "meetingSummaryModel": (os.environ.get("AR_MEETING_SUMMARY_MODEL") or "").strip() or LLM_MODEL,
        "base": LLM_BASE,
        "asr": ASR_MODEL if ASR_PROVIDER == "http" else ("sense-voice" if os.path.basename(ASR_DIR).startswith("sense") else "paraformer-zh-small"),
        "asrProvider": ASR_PROVIDER,
        "asrBase": ASR_BASE if ASR_PROVIDER == "http" else "local",
        "preset": LLM_PRESET or "custom",
        "stats": stats(),
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


def warmup_asr() -> float:
    """预加载本地转写模型，返回耗时秒数（服务启动时后台调用）。

    不预热的话，重启后第一次提词要多等 ~1.3 秒（模型加载）。
    """
    t0 = time.time()
    if ASR_PROVIDER == "local":
        _recognizer()
    return round(time.time() - t0, 2)


# ---------------------------------------------------------------- HTTP 调用


RETRIES = max(0, int(os.environ.get("AR_LLM_RETRIES", "2")))
RETRY_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class _HTTPFail(Exception):
    def __init__(self, code, detail):
        super().__init__("HTTP %s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def _post_json(path, payload, tag="llm", attempts=None):
    """POST 一个 JSON，失败按退避重试；每次尝试都写一行 llm.call 日志。

    重试只针对"可能自己好"的错误：超时、连不上、429、5xx。
    400 这种（key 错、参数错）立刻失败，重试也没用。
    """
    url = LLM_BASE.rstrip("/") + path
    n = attempts if attempts is not None else (RETRIES + 1)
    last = "unknown"
    for i in range(max(1, n)):
        t0 = time.time()
        try:
            if _SESSION is not None:
                r = _SESSION.post(url, json=payload, timeout=TIMEOUT,
                                  headers={"Authorization": "Bearer " + LLM_KEY})
                code, body = r.status_code, r.text
                if code >= 400:
                    raise _HTTPFail(code, body[:300])
                data = json.loads(body)
            else:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + LLM_KEY},
                )
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    data = json.loads(r.read().decode("utf-8"))
            ms = int((time.time() - t0) * 1000)
            _stat(calls=1, last_ms=ms)
            calllog.emit(kind="llm.call", tag=tag, attempt=i + 1, ok=True, ms=ms,
                         model=LLM_MODEL, req_chars=len(json.dumps(payload, ensure_ascii=False)))
            return data
        except Exception as e:                     # noqa: BLE001 - 统一归类
            ms = int((time.time() - t0) * 1000)
            if isinstance(e, _HTTPFail):
                code = e.code
                retryable = code in RETRY_CODES
                detail = e.detail
            else:
                code = 0
                retryable = not isinstance(e, LLMUnavailable)
                detail = str(e)[:200]
            last = detail
            _stat(failures=1, last_error=detail[:200])
            calllog.emit(kind="llm.call", tag=tag, attempt=i + 1, ok=False, ms=ms,
                         model=LLM_MODEL, code=code or None, err=detail[:200])
            if not retryable or i >= n - 1:
                break
            _stat(retries=1)
            time.sleep(min(1.2, 0.25 * (i + 1)))
    if isinstance(last, str) and last.startswith("HTTP "):
        raise LLMUnavailable("模型接口返回：" + last)
    raise LLMUnavailable("模型接口连不上：" + str(last))


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


def _chat_plain(system, user, max_tokens=64, temperature=0.0, tag="chat", model=None):
    """只要一段短回答的调用（分类、选人、提词）。"""
    if STUB:
        raise LLMUnavailable("stub 模式没有这个能力")
    if not LLM_KEY:
        raise LLMUnavailable("没配文本模型 Key")
    data = _post_json(
        "/chat/completions",
        {
            "model": model or LLM_MODEL,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        tag=tag,
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


def _fallback_next(items):
    """模型彻底不可用时，用规则给三条能说的话（不装成模型输出）。"""
    vals = []
    for it in (items or [])[:3]:
        v = str(it.get("value") or "").strip()
        if v:
            vals.append(v[:14])
    return vals or ["先接一句近况", "问问刚才提到的事", "确认下一步"]


def recommend(transcript, person_name, profile, items, system_prompt=""):
    """下一步该说什么：给三条「提词」（不是总结，是接下来能直接说出口的话）。

    system_prompt：控制台「预设」页里的总提示词，放在最前面定风格/结构；
    后面仍接内置的格式要求，保证返回的 JSON 能解析。

    可靠性：第一次 JSON 不合法会带着"上次格式不对"再问一次；两次都不行就回兜底，
    不把异常抛给界面。每次失败都进日志（llm.call / llm.fallback）。
    """
    who = person_name or "一个刚认识的陌生人"
    facts = profile or "（没有档案）"
    done = "；".join("%s：%s" % (i.get("key", ""), i.get("value", "")) for i in (items or [])[:6]) or "（这段还没抽出要点）"
    base = (
        "你是对话提词器，不是会议纪要。用户戴着眼镜正和对面的人面对面说话，"
        "需要的是「接下来我该说什么」，不是「刚才说了什么」。\n"
        "根据档案和刚才这段对话，给三条可以直接说出口的话，挑对方最可能接得住的方向："
        "追问细节、兑现承诺、或者接一个他刚提到的事。\n"
        "要求：每条不超过 12 个字；写成第一人称可以直接念出来的短句；"
        "不要出现「总结/建议/可以」这类词；不要解释；只输出 JSON。\n"
        '格式：{"hint":"一句≤18字的接话提示","say":["可以说的1","可以说的2","可以说的3"]}'
    )
    custom = (system_prompt or "").strip()
    system = (custom + "\n\n" + base) if custom else base
    user = "对面的人：%s\n档案：%s\n这段的要点：%s\n对话原文：%s" % (
        who, facts, done, (transcript or "")[-1200:])

    for attempt in range(2):
        try:
            out = _chat_plain(system, user, max_tokens=160, tag="recommend")
        except LLMUnavailable as e:
            _stat(fallbacks=1)
            calllog.emit(kind="llm.fallback", tag="recommend", reason="unavailable",
                         err=str(e)[:200], attempt=attempt + 1)
            return {"summary": "", "next": _fallback_next(items),
                    "degraded": True, "fallback": True}
        obj = _loads_loose(out) or {}
        say = [str(x).strip()[:14] for x in (obj.get("say") or obj.get("next") or [])
               if str(x).strip()][:3]
        if say:
            return {"summary": str(obj.get("hint") or obj.get("summary") or "")[:40], "next": say}
        # 格式不对：提醒一次"只输出 JSON"，再问
        _stat(retries=1)
        calllog.emit(kind="llm.call", tag="recommend", attempt=attempt + 1, ok=False,
                     model=LLM_MODEL, err="JSON 不合法：%s" % str(out)[:120])
        user = ("上一次输出不是合法 JSON，无法解析。请严格只输出这种 JSON，不要解释、不要代码块：\n"
                '{"hint":"一句≤18字的接话提示","say":["可以说的1","可以说的2","可以说的3"]}\n\n' + user)

    _stat(fallbacks=1)
    calllog.emit(kind="llm.fallback", tag="recommend", reason="bad-json")
    return {"summary": "", "next": _fallback_next(items), "degraded": True, "fallback": True}


MEETING_QA_PROMPT = """下面是一段线下会议对话，请完成以下要求：
1. 从对话中提取出核心问题；
2. 针对问题给出分点式的精简回答要点；
3. 语言简洁，避免长段落，适配小屏幕阅读。
4. 回答过长时，先给一句结论 + 3 个关键词 + 最多 4 条短要点。

对话上下文：
{context}"""

MEETING_ANSWER_SCHEMA = """只输出 JSON，不要解释、不要代码块：
{"summary":"不超过30字的结论","keywords":["关键词1","关键词2","关键词3"],
 "points":["不超过20字的要点1","要点2","要点3"],"answer":"完整一点的回答，最多5条"}"""

_CN_STOP = set("的了是在我你他她它们这那和与及就都而把被让给对说有个一不没很也还又或如果因为所以但是然后可以我们你们他们")


def keywords_of(text, n=4):
    """从一段中文/英文文本里抽关键词（不依赖分词库，供小屏摘要兜底）。"""
    text = str(text or "")
    words = []
    # 英文词
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,20}", text):
        words.append(w.lower())
    # 中文二元组，过滤高频虚词
    cn = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(cn) - 1):
        a, b = cn[i], cn[i + 1]
        if a in _CN_STOP or b in _CN_STOP:
            continue
        words.append(a + b)
    freq = {}
    for w in words:
        if len(w.strip()) < 2:
            continue
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for w, _ in ranked:
        if w not in out:
            out.append(w)
        if len(out) >= n:
            break
    return out


def _meeting_brief(summary, keywords, points, answer):
    # 有些模型会把 answer 再套一层对象：{"answer":{"summary":...}}；
    # 这时优先用外层 summary/points，不能把 dict 直接 str() 上小屏。
    if isinstance(answer, dict):
        summary = summary or answer.get("summary") or answer.get("conclusion") or ""
        keywords = keywords or answer.get("keywords") or []
        points = points or answer.get("points") or []
        answer = answer.get("answer") or answer.get("text") or ""
    elif isinstance(answer, (list, tuple)):
        points = points or [str(x) for x in answer]
        answer = ""
    summary = str(summary or "").strip()[:60]
    keywords = [str(x).strip()[:12] for x in (keywords or []) if str(x).strip()][:4]
    points = [str(x).strip()[:40] for x in (points or []) if str(x).strip()][:4]
    answer = str(answer or "").strip()
    if not answer:
        answer = "\n".join(([summary] if summary else []) + points)
    return {"summary": summary, "keywords": keywords, "points": points,
            "answer": answer or "（没有内容）"}


def answer_meeting(context, question="", system_prompt=""):
    """多人会议的一键 AI 应答。

    返回 {summary, keywords, points, answer}；小屏只显示 summary/keywords/points，
    answer 留在控制台历史里。没配 Key 时抛 LLMUnavailable，由服务层兜底。
    """
    context = (context or "").strip()
    if not context and not (question or "").strip():
        raise LLMUnavailable("还没有会议上下文")
    question = (question or "").strip()
    custom = (system_prompt or "").strip()
    user = MEETING_QA_PROMPT.format(context=context[-5000:])
    if question:
        user += "\n\n用户这次的问题：" + question[:300]
    if custom:
        user = "系统提示词（用户可编辑，优先遵守）：\n" + custom[:2000] + "\n\n" + user
    if STUB:
        return _meeting_brief("王姐下周三前发报价单，明天下午3点三楼开会",
                              ["报价单", "下周三", "三楼会议室"],
                              ["王姐下周三前发报价单", "明天下午3点三楼开会", "记得带电脑和充电器"],
                              "1. 王姐下周三前发报价单。\n2. 明天下午3点三楼会议室开会。\n3. 记得带电脑和充电器。")
    if not LLM_KEY:
        raise LLMUnavailable("没配文本模型 Key（AR_LLM_KEY / DEEPSEEK_API_KEY 等）")
    # 左键实时问答优先速度：可单独指定非推理模型（如 glm-4-flash），
    # 会后总结继续用 AR_LLM_MODEL（如 glm-5.3-flash）保质量。
    answer_model = (os.environ.get("AR_MEETING_ANSWER_MODEL") or "").strip() or LLM_MODEL
    default_max = "600" if "glm-4-flash" in answer_model.lower() else "1600"
    try:
        answer_max = int(os.environ.get("AR_MEETING_ANSWER_MAX_TOKENS") or default_max)
    except ValueError:
        answer_max = int(default_max)
    out = _chat_plain(
        "你是线下会议的 AI 助手。回答必须精简、分点、适合 AR 小屏阅读；"
        "只根据给出的会议上下文，不要编造没出现的信息。"
        + MEETING_ANSWER_SCHEMA,
        user,
        max_tokens=answer_max,
        temperature=0.2,
        model=answer_model,
    )
    out = (out or "").strip()
    if not out:
        raise LLMUnavailable("模型没有返回内容")
    obj = _loads_loose(out)
    if isinstance(obj, dict):
        summary = (obj.get("summary") or obj.get("conclusion")
                   or obj.get("结论") or obj.get("核心结论") or "")
        keywords = obj.get("keywords") or obj.get("关键词") or []
        points = obj.get("points") or obj.get("要点") or []
        answer = obj.get("answer") or obj.get("回答") or ""
        brief = _meeting_brief(summary, keywords, points, answer)
        # 模型只给了 summary/points、没给 answer 时，拼出可读文本
        if not brief["answer"].strip() and (brief["summary"] or brief["points"]):
            brief["answer"] = "\n".join(([brief["summary"]] if brief["summary"] else []) + brief["points"])
        return brief
    # 模型没按 JSON 回：保留原文，摘要用首行 + 关键词
    first = out.splitlines()[0][:60]
    return _meeting_brief(first, keywords_of(out, 3), out.splitlines()[:3], out)


def summarize_meeting(transcript, system_prompt=""):
    """会后总结：返回 {summary, key_points, action_items}。"""
    transcript = (transcript or "").strip()
    if len(transcript) < 8:
        raise LLMUnavailable("会议内容太短，先多说几句")
    custom = (system_prompt or "").strip()
    user = ("首要任务：下面是一场线下会议的全部转写，请总结：\n"
            "1) 一句话结论（≤40字）；2) 3–5 条关键结论；3) 待办（谁、做什么、什么时候）。\n"
            "只根据原文，不要编。只输出 JSON，不要解释、不要代码块："
            '{"summary":"...","key_points":["..."],"action_items":["..."]}\n\n'
            "会议转写：\n" + transcript[-8000:])
    # 用户可编辑提示词只作为“措辞风格”补充，放在 JSON 任务之后；
    # 否则默认的“商务助理”提示词会让模型输出散文，解析失败。
    if custom:
        user += "\n\n风格偏好（只影响措辞，不得改变 JSON 结构）：\n" + custom[:1600]
    if STUB:
        return {"summary": "3 人会议：报价单与明天下午开会安排",
                "key_points": ["王姐下周三前发报价单", "明天下午3点三楼会议室开会"],
                "action_items": ["王姐：下周三前发报价单", "全员：明天下午3点带电脑到三楼"]}
    if not LLM_KEY:
        raise LLMUnavailable("没配文本模型 Key")
    obj = None
    summary_model = (os.environ.get("AR_MEETING_SUMMARY_MODEL") or "").strip() or LLM_MODEL
    for attempt, max_tokens in enumerate((1800, 2600)):
        out = _chat_plain(
            "你是会议纪要助手，输出必须精简、可执行、只依据原文。",
            user, max_tokens=max_tokens, temperature=0.2, model=summary_model,
        )
        obj = _loads_loose(out)
        if isinstance(obj, dict):
            break
        user += ("\n\n上一次输出不是合法 JSON。请严格只输出 "
                 '{"summary":"...","key_points":["..."],"action_items":["..."]}，不要解释、不要代码块。')
    if not isinstance(obj, dict):
        raise LLMUnavailable("模型没按 JSON 返回总结")
    pick = lambda k, alt: [str(x).strip()[:80] for x in (obj.get(k) or obj.get(alt) or []) if str(x).strip()][:6]
    return {
        "summary": str(obj.get("summary") or obj.get("结论") or "").strip()[:120],
        "key_points": pick("key_points", "关键结论"),
        "action_items": pick("action_items", "待办"),
    }


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
    user = ctx + "\n\n对话原文：\n" + transcript[-6000:]
    obj = None
    for attempt in range(2):
        data = _post_json(
            "/chat/completions",
            {
                "model": LLM_MODEL,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": user},
                ],
            },
            tag="analyze",
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            raise LLMUnavailable("模型回的格式不对")
        obj = _loads_loose(content)
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            break
        _stat(retries=1)
        calllog.emit(kind="llm.call", tag="analyze", attempt=attempt + 1, ok=False,
                     model=LLM_MODEL, err="JSON 不合法：%s" % str(content)[:120])
        user = ("上一次输出不是合法 JSON，无法解析。请严格只输出这种 JSON，不要解释、不要代码块：\n"
                '{"summary":"一句话总结","items":[{"key":"答应的事","value":"下周三前发报价单","quote":"原话"}]}\n\n'
                + user)
    if not isinstance(obj, dict):
        _stat(fallbacks=1)
        calllog.emit(kind="llm.fallback", tag="analyze", reason="bad-json")
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
        pass
    # 有些模型会回 Python dict 的单引号形式（conclusion/keywords/points）
    try:
        return ast.literal_eval(text)
    except Exception:
        return None

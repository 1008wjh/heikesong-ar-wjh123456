"""MiMo 大模型接入：语音转写 + 单人对话要点提取。

设计原则：
- 没配 Key 时**不假装能用**：available() 返回 False，接口回 400 并给出明确提示。
- 只在测试里用 AR_LLM_STUB=1 走假数据（stub 的返回里带 "stub": true，调用方能看到）。
- 不在这里做持久化，所有写入都回到 store。
"""

import json
import os
import urllib.error
import urllib.request

KEY = os.environ.get("MIMO_API_KEY", "")
BASE = os.environ.get("MIMO_BASE", "https://api.xiaomimimo.com/v1")
TEXT_MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.6-pro")
ASR_MODEL = os.environ.get("MIMO_ASR_MODEL", "mimo-v2.5-asr")
STUB = os.environ.get("AR_LLM_STUB", "") == "1"

TIMEOUT = float(os.environ.get("AR_LLM_TIMEOUT", "60"))


class LLMUnavailable(RuntimeError):
    """没配 Key / 请求失败，调用方应该把它当成"功能没开"而不是"程序坏了"。"""


def available():
    return bool(KEY) or STUB


def status():
    return {
        "available": available(),
        "stub": STUB,
        "model": TEXT_MODEL,
        "asr": ASR_MODEL,
        "base": BASE,
    }


def _post_json(path, payload):
    req = urllib.request.Request(
        BASE.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + KEY,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMUnavailable(f"模型接口返回 {e.code}：{detail}")
    except Exception as e:  # 网络错误、超时、JSON 坏了
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
        BASE.rstrip("/") + path,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": "Bearer " + KEY,
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


def transcribe(wav_bytes):
    """一段 wav → 文字。空音频或没配 Key 时抛 LLMUnavailable。"""
    if not wav_bytes:
        raise LLMUnavailable("这段音频是空的")
    if STUB:
        return "（stub 转写）我们上次说的那个报价单，我这两天整理一下发你"
    if not KEY:
        raise LLMUnavailable("没配 MIMO_API_KEY，转写和析出都用不了")
    data = _post_multipart(
        "/audio/transcriptions",
        {"model": ASR_MODEL, "language": "zh"},
        {"file": ("chunk.wav", wav_bytes, "audio/wav")},
    )
    text = (data.get("text") or "").strip()
    if not text:
        raise LLMUnavailable("这段音频没听出字")
    return text


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
    if not KEY:
        raise LLMUnavailable("没配 MIMO_API_KEY，分析用不了")

    ctx = "对面这个人：%s（%s）" % (person.get("name", "?"), person.get("role") or "身份未知")
    data = _post_json(
        "/chat/completions",
        {
            "model": TEXT_MODEL,
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
    for it in (obj.get("items") or [])[:6]:
        if not isinstance(it, dict):
            continue
        value = str(it.get("value") or "").strip()
        if not value:
            continue
        items.append(
            {
                "key": str(it.get("key") or "其他").strip()[:8],
                "value": value[:40],
                "quote": str(it.get("quote") or "").strip()[:60],
            }
        )
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

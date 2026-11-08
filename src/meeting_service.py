"""多人会议 · 服务层。

职责（对照 PRD 第 4/5 节）：
  1. 管会话生命周期：进入模块建 session、录制启停、结束落库；
  2. 收音频分片：本地留完整 PCM（audio_storage/<session>.pcm），转写逐段落库；
  3. 维护"环形滑动上下文队列"：最近 N 条发言，只给 AI 问答用；
  4. 一键 AI 应答：固定提示词 → 大模型；没配 Key 时用本地规则兜底，界面如实标注；
  5. 会后离线归集：把整场音频重新分人 + 逐段转写，按说话人汇总，异步执行。

不重写识别/分人算法：直接复用项目里的 local ASR（llm.transcribe）与
diarize/meeting_agent；PRD 里的"第三方云端 API"通过 llm.py 的 provider 配置切换
（AR_ASR_PROVIDER=http 等），服务层不感知厂商差异。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
import uuid
import wave
from collections import deque

import numpy as np

import agent
import diarize
import llm
import meeting_db as db
import meeting_store
import settings as settings_store
import voice
import web_search

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUDIO_DIR = os.environ.get("AR_AUDIO_DIR", os.path.join(ROOT, "audio_storage"))
SAMPLE_RATE = voice.SAMPLE_RATE

# 环形上下文队列：PRD 说"固定长度，仅保留最近 N 条对话，专供 AI 问答调用"
RING_SIZE = int(os.environ.get("AR_MEETING_CONTEXT_N", "12"))
# 会后离线归集：最多重转写多少段、音频最长多少分钟（防现场卡死）
OFFLINE_MAX_SEGMENTS = int(os.environ.get("AR_MEETING_OFFLINE_SEGMENTS", "120"))
OFFLINE_MAX_MINUTES = float(os.environ.get("AR_MEETING_OFFLINE_MINUTES", "30"))

_RING: dict[str, deque] = {}
_RING_LOCK = threading.RLock()
_OFFLINE_LOCK = threading.Lock()
_OFFLINE_BUSY: set[str] = set()


# ---------------------------------------------------------------- 基础工具


def _new_session() -> str:
    t = base64.b32encode(int(time.time() * 1000).to_bytes(6, "big")).decode().lower().rstrip("=")
    return "mtg_%s_%s" % (t, uuid.uuid4().hex[:6])


def _new_meeting_id(session: str) -> str:
    return "m_" + re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:48]


def _rel_audio(session: str) -> str:
    return "audio_storage/%s.pcm" % session


def _abs_audio(session: str) -> str:
    return os.path.join(AUDIO_DIR, session + ".pcm")


def decode_audio(payload: str) -> bytes:
    if not payload:
        return b""
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    if os.path.exists(payload):
        with open(payload, "rb") as f:
            return f.read()
    return base64.b64decode(payload)


def _wav_pcm(data: bytes) -> bytes:
    """WAV → 16k/mono/s16le 裸 PCM（写进 audio_storage/<session>.pcm）。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.getnframes()
        raw = w.readframes(frames)
    if width != 2:
        raise ValueError("只支持 16-bit PCM 音频")
    if channels > 1:
        arr = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).mean(axis=1)
        raw = np.clip(arr, -32768, 32767).astype("<i2").tobytes()
    if rate != SAMPLE_RATE:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        x = voice.resample(x, rate, SAMPLE_RATE)
        raw = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return raw


def _pcm_samples(raw: bytes) -> np.ndarray:
    if not raw:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _wav_bytes(samples: np.ndarray) -> bytes:
    x = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def _append_audio(session: str, pcm: bytes) -> None:
    if not pcm:
        return
    os.makedirs(AUDIO_DIR, exist_ok=True)
    with open(_abs_audio(session), "ab") as f:
        f.write(pcm)


def _ensure_session(session: str) -> dict:
    row = db.get_session(session)
    if row:
        return row
    return start(session=session)


# ---------------------------------------------------------------- 会话生命周期


def start(session: str | None = None) -> dict:
    """进入多人模块 / 开始录制：建一行 meeting_session（幂等）。"""
    session = (session or "").strip() or _new_session()
    row = db.get_session(session)
    if row:
        return _session_payload(row, resumed=True)
    meeting_id = _new_meeting_id(session)
    os.makedirs(AUDIO_DIR, exist_ok=True)
    audio_abs = _abs_audio(session)
    if not os.path.isfile(audio_abs):
        open(audio_abs, "ab").close()
    row = db.create_session(session, meeting_id, _rel_audio(session))
    return _session_payload(row, resumed=False)


def _session_payload(row: dict, resumed: bool = False) -> dict:
    return {
        "ok": True,
        "session": row.get("session_id"),
        "meeting_id": row.get("meeting_id") or "",
        "is_recording": bool(row.get("is_recording")),
        "create_time": row.get("create_time"),
        "audio_file": row.get("audio_file_path") or "",
        "resumed": resumed,
    }


def feed(session: str, audio: str = "", text: str = "", auto_add: bool = True) -> dict:
    """收一片会议音频 / 文本指令：跑会议图 → 写 PCM + 转写 → 返回字幕尾和状态。"""
    row = _ensure_session(session)
    t0 = time.time()
    audio_bytes = decode_audio(audio) if audio else b""
    if audio_bytes:
        try:
            _append_audio(session, _wav_pcm(audio_bytes))
        except Exception as e:
            # 音频落盘失败不能挡住转写；把原因带回给界面
            pass
    view = agent.run_feed(session, text=text or "", audio=audio or "",
                          auto_add=bool(auto_add), mode="meeting")

    new_segments = _normalize_view_segments(view.get("segments") or [])
    inserted = db.add_segments(session, new_segments) if new_segments else 0
    _ring_append(session, new_segments)

    tail = db.get_tail_segments(session, 24)
    count = db.count_segments(session)
    speakers = view.get("speakers") or []
    duration = _duration_of(tail)
    db.update_progress(session, duration, len(speakers), count, view.get("summary") or "")
    return {
        "ok": True,
        "session": session,
        "view": view,
        "segments": tail,
        "transcript_tail": tail[-4:],
        "speakers": speakers,
        "is_recording": True,
        "segment_count": count,
        "duration_sec": duration,
        "inserted": inserted,
        "ms": int((time.time() - t0) * 1000),
        "degraded": bool(view.get("degraded")),
    }


def _normalize_view_segments(segments: list[dict]) -> list[dict]:
    out = []
    for seg in segments or []:
        text = (seg.get("content") or seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "spk_id": str(seg.get("spk_id") or seg.get("spk") or ""),
            "name": str(seg.get("name") or ""),
            "start_ts": int(seg.get("start_ts") or 0),
            "end_ts": int(seg.get("end_ts") or 0),
            "content": text,
        })
    return out


def _duration_of(segments: list[dict]) -> float:
    end = 0.0
    for s in segments or []:
        try:
            end = max(end, float(s.get("end_ts") or 0) / 1000.0)
        except Exception:
            continue
    return round(end, 2)


def stop(session: str) -> dict:
    """停止录制：归档会议图状态，结束 DB 会话，保留音频与转写。"""
    row = db.get_session(session)
    if not row:
        return {"ok": False, "error": "没有这段会议"}
    archived: dict = {}
    try:
        archived = agent.archive_meeting(session) or {}
    except Exception as e:
        archived = {"ok": False, "error": str(e)}

    segments = db.get_segments(session)
    duration = _duration_of(segments)
    meeting_id = (archived.get("meeting_id") if archived.get("ok") else "") or row.get("meeting_id") or ""
    speaker_count = int(archived.get("speaker_count") or 0)
    if not speaker_count:
        speaker_count = len({s.get("spk_id") for s in segments if s.get("spk_id")})
    segment_count = len(segments)
    summary = (archived.get("summary") if archived.get("ok") else "") or row.get("summary") or ""
    transcript = "\n".join(
        "%s：%s" % ((s.get("name") or s.get("spk_id") or "发言人").strip(),
                   (s.get("content") or "").strip())
        for s in segments if (s.get("content") or "").strip()
    )
    system_prompt = (settings_store.get_settings().get("system_prompt") or "")
    try:
        brief = llm.summarize_meeting(transcript, system_prompt=system_prompt)
        degraded = False
    except llm.LLMUnavailable:
        brief = _fallback_summary(segments, archived, summary)
        degraded = True
    summary_text = brief.get("summary") or summary
    key_points = brief.get("key_points") or []
    action_items = brief.get("action_items") or []
    if not action_items:
        action_items = [a.get("task") or "" for a in (archived.get("action_items") or []) if a.get("task")]
    db.finish_session(session, duration, speaker_count, segment_count, summary,
                      summary_text=summary_text, key_points=key_points,
                      action_items=action_items)
    db.set_offline_status(session, int(row.get("offline_diarization_status") or 0))
    try:
        meeting_store.patch(session, summary_text=summary_text,
                            key_points=key_points, action_items=action_items,
                            summary_counts=summary)
    except Exception:
        pass
    return {
        "ok": True,
        "session": session,
        "meeting_id": meeting_id,
        "speaker_count": speaker_count,
        "duration_sec": duration,
        "segment_count": segment_count,
        "summary": summary,
        "summary_text": summary_text,
        "key_points": key_points,
        "action_items": action_items,
        "degraded": degraded,
        "path": archived.get("path") or meeting_store.meta_path(session),
    }


def _fallback_summary(segments: list[dict], archived: dict, counts: str) -> dict:
    """没 Key 时的总结兜底：人数/段数/待办 + 最近几条原话。"""
    key_points = []
    for s in (segments or [])[-3:]:
        content = (s.get("content") or "").strip()
        if content:
            who = (s.get("name") or "发言人").strip()
            key_points.append("%s：%s" % (who, content[:48]))
    actions = []
    for a in (archived.get("action_items") or []):
        task = (a.get("task") or "").strip()
        if task:
            owner = (a.get("owner") or "").strip()
            actions.append((owner + "：" if owner else "") + task)
    return {
        "summary": (counts or "会议已结束") + ("；" + key_points[0][:40] if key_points else ""),
        "key_points": key_points,
        "action_items": actions[:6],
    }


# ---------------------------------------------------------------- 环形上下文 + AI 问答


def _ring(session: str) -> deque:
    with _RING_LOCK:
        q = _RING.get(session)
        if q is None:
            q = deque(maxlen=RING_SIZE)
            for seg in db.get_tail_segments(session, RING_SIZE):
                q.append(seg)
            _RING[session] = q
        return q


def _ring_append(session: str, segments: list[dict]) -> None:
    if not segments:
        return
    with _RING_LOCK:
        q = _ring(session)
        seen = {(s.get("spk_id"), int(s.get("start_ts") or 0),
                 int(s.get("end_ts") or 0), s.get("content")) for s in q}
        for seg in segments:
            key = (seg.get("spk_id"), int(seg.get("start_ts") or 0),
                   int(seg.get("end_ts") or 0), seg.get("content"))
            if key in seen:
                continue
            seen.add(key)
            q.append(seg)


def context_text(session: str, limit: int = RING_SIZE) -> str:
    ring = _ring(session)
    lines = []
    for seg in list(ring)[-limit:]:
        name = (seg.get("name") or seg.get("spk_id") or "发言人").strip()
        content = (seg.get("content") or "").strip()
        if content:
            lines.append("%s：%s" % (name, content))
    return "\n".join(lines)


_QUESTION_RE = re.compile(r"[?？]|吗|什么|怎么|如何|谁|哪|几点|多少|多久|确认|安排|要不要")


def _short(text: str, n: int) -> str:
    # 只压空白，不要把英文单词之间的空格也删掉（否则 "Please confirm" 变 "Pleaseconfirm"）
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text[:n] + ("…" if len(text) > n else "")


def _retrieve_context(session: str, question: str = "") -> str:
    """本地会议文字检索：关键词/字符命中 + 近期权重，不调用任何外部搜索。"""
    question = (question or "").strip()
    if not question:
        return context_text(session)
    segments = db.get_segments(session)
    if not segments:
        return ""
    keys = set(llm.keywords_of(question, 8))
    qchars = set(re.findall(r"[\u4e00-\u9fff]", question))
    scored = []
    for i, seg in enumerate(segments):
        content = (seg.get("content") or "")
        score = sum(2.0 for k in keys if k and k in content)
        score += 0.15 * len(qchars & set(content))
        score += 0.5 * (i / max(1, len(segments) - 1))   # 越近权重越高
        scored.append((score, i, seg))
    chosen = {i for _, i, _ in sorted(scored, key=lambda x: x[0], reverse=True)[:6]}
    for i in range(max(0, len(segments) - 4), len(segments)):
        chosen.add(i)
    lines = []
    for i in sorted(chosen):
        seg = segments[i]
        name = (seg.get("name") or seg.get("spk_id") or "发言人").strip()
        content = (seg.get("content") or "").strip()
        if content:
            lines.append("%s：%s" % (name, content))
    return "\n".join(lines)


def _fallback_brief(session: str, question: str = "") -> dict:
    """没配 Key 时的本地兜底：明确说明"没有大模型"，不把问题复述一遍。"""
    ring = list(_ring(session))
    if not ring:
        return {"summary": "还没有会议内容，先按确认开始录制",
                "keywords": [], "points": [],
                "answer": "还没有听到会议内容：先按「确认」开始录制，再按左键。"}
    summary = "未配置大模型 Key，AI 回答暂不可用"
    keywords = llm.keywords_of(" ".join((s.get("content") or "") for s in ring[-4:]), 3)
    points = [
        "已保留会议转写，共 %d 条上下文" % len(ring),
        "补 AR_LLM_KEY 后可按问题生成回答",
    ]
    if question:
        points.insert(0, "收到问题，但没有模型 Key 无法作答")
    answer = "\n".join([summary] + points)
    return {"summary": summary, "keywords": keywords, "points": points[:3],
            "answer": answer}


_ASK_RE = re.compile(
    r"[?？]|吗|什么|怎么|如何|谁|哪|几|多少|多久|为什么|是否|能不能|要不要|"
    r"请问|介绍|解释|分析|说说|讲讲|列举|区别|原理|定义|是什么|有什么"
)


def _find_latest_question(session: str) -> str:
    """从后往前找最近一句"像问题"的转写，左键没输入文字时用它。"""
    for seg in reversed(db.get_segments(session)):
        text = (seg.get("content") or "").strip()
        if text and _ASK_RE.search(text):
            return text
    return ""


def ask(session: str, question: str = "") -> dict:
    """左键/文字提问：从后往前找问题 → 检索会议文本 → 提示词 → 模型回答。"""
    row = db.get_session(session)
    if not row:
        return {"ok": False, "error": "没有这段会议，先按确认开始录制"}
    t0 = time.time()
    question = (question or "").strip()
    auto_question = False
    if not question:
        question = _find_latest_question(session)
        auto_question = bool(question)
    system_prompt = (settings_store.get_settings().get("system_prompt") or "")
    context = _retrieve_context(session, question)
    web = ""
    if question and (os.environ.get("AR_WEB_SEARCH", "1") or "1").strip() != "0":
        web = web_search.context(question, 4)
    try:
        brief = llm.answer_meeting(context, question=question,
                                   system_prompt=system_prompt, web_results=web)
        degraded = False
    except llm.LLMUnavailable:
        brief = _fallback_brief(session, question)
        degraded = True
    created = db.now_text()
    rec = db.add_answer(session, context, brief.get("answer") or "", created)
    return {
        "ok": True,
        "session": session,
        "answer": brief.get("answer") or "",
        "summary": brief.get("summary") or "",
        "keywords": brief.get("keywords") or [],
        "points": brief.get("points") or [],
        "question": question,
        "auto_question": auto_question,
        "web_used": bool(web),
        "context_snapshot": context,
        "create_time": created,
        "id": rec.get("id"),
        "degraded": degraded,
        "ms": int((time.time() - t0) * 1000),
    }


# ---------------------------------------------------------------- 查询 / 控制台


def state(session: str) -> dict:
    row = db.get_session(session)
    if not row:
        return {"ok": False, "error": "没有这段会议"}
    segments = db.get_tail_segments(session, 20)
    answers = db.get_answers(session)
    last = answers[-1] if answers else None
    return {
        "ok": True,
        "session": session,
        "meeting": _meeting_dict(row),
        "speakers": _speakers_of(db.get_segments(session)),
        "segments": segments,
        "transcript_tail": segments[-4:],
        "answer": last.get("answer") if last else "",
        "answers": answers,
        "offline_summary": db.get_offline_summary(session),
        "is_recording": bool(row.get("is_recording")),
    }


def discard(session: str) -> dict:
    """进了模块又没录：把临时会话和空音频删掉，避免控制台出现空会议。"""
    row = db.get_session(session)
    if not row:
        return {"ok": True, "discarded": False}
    if db.count_segments(session) > 0 or db.count_answers(session) > 0:
        return {"ok": False, "error": "这场会议已经有内容，不能丢弃", "discarded": False}
    db.delete_session(session)
    try:
        os.remove(_abs_audio(session))
    except OSError:
        pass
    with _RING_LOCK:
        _RING.pop(session, None)
    return {"ok": True, "discarded": True}


def _speakers_of(segments: list[dict]) -> list[dict]:
    order: list[str] = []
    acc: dict[str, dict] = {}
    for seg in segments or []:
        sid = seg.get("spk_id") or ""
        if not sid:
            continue
        if sid not in acc:
            acc[sid] = {"spk_id": sid, "name": seg.get("name") or "",
                        "seconds": 0.0, "segments": 0}
            order.append(sid)
        acc[sid]["seconds"] += max(0.0, (float(seg.get("end_ts") or 0)
                                         - float(seg.get("start_ts") or 0)) / 1000.0)
        acc[sid]["segments"] += 1
        if not acc[sid]["name"] and seg.get("name"):
            acc[sid]["name"] = seg["name"]
    out = []
    for i, sid in enumerate(order):
        item = acc[sid]
        item["seconds"] = round(item["seconds"], 2)
        if not item["name"]:
            item["name"] = "发言人%d" % (i + 1)
        out.append(item)
    return out


def _meeting_dict(row: dict) -> dict:
    session = row.get("session_id") or ""
    return {
        "session_id": session,
        "meeting_id": row.get("meeting_id") or "",
        "create_time": row.get("create_time") or "",
        "finish_time": row.get("finish_time") or None,
        "duration_sec": round(float(row.get("duration_sec") or 0), 2),
        "speaker_count": int(row.get("speaker_count") or 0),
        "segment_count": int(row.get("segment_count") or 0),
        "answer_count": db.count_answers(session),
        "is_recording": bool(row.get("is_recording")),
        "offline_diarization_status": int(row.get("offline_diarization_status") or 0),
        "summary": row.get("summary") or "",
        "summary_text": row.get("summary_text") or "",
        "key_points": _json_list(row.get("key_points")),
        "action_items": _json_list(row.get("action_items")),
        "audio_file_path": row.get("audio_file_path") or "",
    }


def _json_list(raw) -> list:
    if not raw:
        return []
    try:
        got = json.loads(raw) if isinstance(raw, str) else raw
        return [str(x) for x in got] if isinstance(got, list) else []
    except Exception:
        return []


def list_meetings(limit: int = 100) -> dict:
    items = [_meeting_dict(row) for row in db.list_sessions(limit)]
    return {"ok": True, "items": items, "count": len(items)}


def detail(session: str) -> dict:
    row = db.get_session(session)
    if not row:
        return {"ok": False, "error": "没有这段会议"}
    segments = db.get_segments(session)
    return {
        "ok": True,
        "meeting": _meeting_dict(row),
        "speakers": _speakers_of(segments),
        "segments": segments,
        "answers": db.get_answers(session),
        "offline_summary": db.get_offline_summary(session),
    }


# ---------------------------------------------------------------- 会后离线归集


def offline_submit(session: str) -> dict:
    """可选加分链路：把完整音频重新做全局分人，按说话人汇总纪要。异步跑。"""
    row = db.get_session(session)
    if not row:
        return {"ok": False, "error": "没有这段会议"}
    if not os.path.isfile(_abs_audio(session)) or os.path.getsize(_abs_audio(session)) == 0:
        return {"ok": False, "error": "没有留存音频，无法离线归集"}
    with _OFFLINE_LOCK:
        if session in _OFFLINE_BUSY:
            return {"ok": True, "status": 1, "message": "已经在处理中"}
        _OFFLINE_BUSY.add(session)
    db.set_offline_status(session, 1)
    threading.Thread(target=_run_offline, args=(session,), name="meeting-offline", daemon=True).start()
    return {"ok": True, "status": 1, "message": "已提交，后台处理中"}


def _run_offline(session: str) -> None:
    try:
        with open(_abs_audio(session), "rb") as f:
            raw = f.read()
        samples = _pcm_samples(raw)
        limit = int(OFFLINE_MAX_MINUTES * 60 * SAMPLE_RATE)
        if samples.size > limit:
            samples = samples[:limit]
        if samples.size < SAMPLE_RATE * 2:
            raise ValueError("音频太短，无法离线归集")
        people = agent.store_ref.voice_index() if getattr(agent, "store_ref", None) is not None else []
        res = diarize.diarize(samples, people, per_speaker=False)
        segments = res.get("segments") or []
        if not segments:
            raise ValueError("没有检测到人声")
        names = {s.get("spk_id"): s.get("name") or "" for s in (res.get("speakers") or [])}
        groups: dict[str, dict] = {}
        used = 0
        for seg in segments:
            if used >= OFFLINE_MAX_SEGMENTS:
                break
            a = max(0, int(float(seg.get("start") or 0) * SAMPLE_RATE))
            b = min(samples.size, int(float(seg.get("end") or 0) * SAMPLE_RATE))
            if b - a < int(0.4 * SAMPLE_RATE):
                continue
            try:
                text = llm.transcribe(_wav_bytes(samples[a:b])) or ""
            except Exception:
                text = ""
            used += 1
            sid = seg.get("spk_id") or "spk_0"
            g = groups.setdefault(sid, {"spk_id": sid, "name": "", "seconds": 0.0, "points": []})
            g["seconds"] += (b - a) / SAMPLE_RATE
            if text:
                g["points"].append(_short(text, 60))
            if names.get(sid) and not g["name"]:
                g["name"] = names[sid]
        order = sorted(groups.values(), key=lambda g: -g["seconds"])
        for i, g in enumerate(order):
            if not g["name"] or g["name"] == "陌生人":
                g["name"] = "发言人%d" % (i + 1)
            g["seconds"] = round(g["seconds"], 2)
            g["points"] = g["points"][:4]
        payload = {
            "generated_at": db.now_text(),
            "engine": "pyannote-segmentation + CAM++ (offline)",
            "groups": order,
            "segment_count": len(segments),
        }
        db.save_offline_summary(session, payload)
        db.set_offline_status(session, 2)
    except Exception as e:
        db.save_offline_summary(session, {"generated_at": db.now_text(),
                                          "groups": [], "error": str(e)})
        db.set_offline_status(session, 0)
    finally:
        with _OFFLINE_LOCK:
            _OFFLINE_BUSY.discard(session)

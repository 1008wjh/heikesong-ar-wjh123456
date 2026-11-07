"""多人会议 · 持久化层（SQLite）。

PRD 推荐 H2（SpringBoot）；本项目后端是 Python 标准库 HTTP 服务，所以用
**标准库 sqlite3** 落地同样的三张表，零额外部署，迁移到 MySQL 时改 SQL 方言即可：

    meeting_session   会议会话主表（含音频路径、离线聚类状态）
    speech_record     逐段转写（spk_id / start_ts / end_ts / content）
    ai_answer_record  AI 问答历史（context_snapshot / answer / create_time）

额外的 ``meeting_offline_summary`` 用来放"会后离线归集"的结果，不污染 PRD 三表。
所有写操作串行在一个进程内锁里；HTTP 服务是多线程的，SQLite 连接按调用创建。

数据目录跟随 ``AR_DATA_DIR``（默认项目根 data/），便于演示时整个目录拷走。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.environ.get("AR_DATA_DIR", os.path.join(ROOT, "data"))
DB_PATH = os.environ.get("AR_MEETING_DB", os.path.join(DATA_DIR, "meetings.db"))

_LOCK = threading.RLock()
_READY = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_session (
    session_id                  TEXT PRIMARY KEY,
    meeting_id                  TEXT NOT NULL DEFAULT '',
    mode                        TEXT NOT NULL DEFAULT 'multi',
    is_recording                INTEGER NOT NULL DEFAULT 0,
    create_time                 TEXT NOT NULL,
    audio_file_path             TEXT NOT NULL DEFAULT '',
    finish_time                 TEXT,
    offline_diarization_status  INTEGER NOT NULL DEFAULT 0,
    duration_sec                REAL NOT NULL DEFAULT 0,
    speaker_count               INTEGER NOT NULL DEFAULT 0,
    segment_count               INTEGER NOT NULL DEFAULT 0,
    summary                     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS speech_record (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    spk_id      TEXT NOT NULL,
    start_ts    INTEGER NOT NULL,
    end_ts      INTEGER NOT NULL,
    content     TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_speech
    ON speech_record(session_id, spk_id, start_ts, end_ts, content);
CREATE INDEX IF NOT EXISTS ix_speech_session ON speech_record(session_id, start_ts);
CREATE TABLE IF NOT EXISTS ai_answer_record (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    context_snapshot TEXT NOT NULL,
    answer           TEXT NOT NULL,
    create_time      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_answer_session ON ai_answer_record(session_id, id);
CREATE TABLE IF NOT EXISTS meeting_offline_summary (
    session_id  TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init() -> None:
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        conn = connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _READY = True


def _row(conn: sqlite3.Connection, sql: str, args=()) -> dict | None:
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def _rows(conn: sqlite3.Connection, sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- 会话主表


def create_session(session: str, meeting_id: str, audio_path: str,
                   create_time: str | None = None) -> dict:
    init()
    with _LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO meeting_session"
                " (session_id, meeting_id, mode, is_recording, create_time, audio_file_path)"
                " VALUES (?, ?, 'multi', 1, ?, ?)",
                (session, meeting_id, create_time or now_text(), audio_path),
            )
            conn.commit()
            return _row(conn, "SELECT * FROM meeting_session WHERE session_id = ?", (session,)) or {}
        finally:
            conn.close()


def get_session(session: str) -> dict | None:
    init()
    with _LOCK:
        conn = connect()
        try:
            return _row(conn, "SELECT * FROM meeting_session WHERE session_id = ?", (session,))
        finally:
            conn.close()


def set_recording(session: str, recording: bool) -> None:
    init()
    with _LOCK:
        conn = connect()
        try:
            conn.execute("UPDATE meeting_session SET is_recording = ? WHERE session_id = ?",
                         (1 if recording else 0, session))
            conn.commit()
        finally:
            conn.close()


def update_progress(session: str, duration_sec: float, speaker_count: int,
                    segment_count: int, summary: str = "") -> None:
    init()
    with _LOCK:
        conn = connect()
        try:
            cur = _row(conn, "SELECT summary FROM meeting_session WHERE session_id = ?", (session,))
            set_summary = summary or (cur or {}).get("summary") or ""
            conn.execute(
                "UPDATE meeting_session SET duration_sec = ?, speaker_count = ?,"
                " segment_count = ?, summary = ? WHERE session_id = ?",
                (round(float(duration_sec or 0), 2), int(speaker_count or 0),
                 int(segment_count or 0), set_summary, session),
            )
            conn.commit()
        finally:
            conn.close()


def finish_session(session: str, duration_sec: float, speaker_count: int,
                   segment_count: int, summary: str = "",
                   finish_time: str | None = None) -> None:
    init()
    with _LOCK:
        conn = connect()
        try:
            conn.execute(
                "UPDATE meeting_session SET is_recording = 0, finish_time = ?,"
                " duration_sec = ?, speaker_count = ?, segment_count = ?, summary = ?"
                " WHERE session_id = ?",
                (finish_time or now_text(), round(float(duration_sec or 0), 2),
                 int(speaker_count or 0), int(segment_count or 0), summary, session),
            )
            conn.commit()
        finally:
            conn.close()


def set_offline_status(session: str, status: int) -> None:
    init()
    with _LOCK:
        conn = connect()
        try:
            conn.execute("UPDATE meeting_session SET offline_diarization_status = ?"
                         " WHERE session_id = ?", (int(status), session))
            conn.commit()
        finally:
            conn.close()


def delete_session(session: str) -> None:
    """真删一条会议（只用于"进了模块但什么都没录"的临时会话）。"""
    init()
    with _LOCK:
        conn = connect()
        try:
            conn.execute("DELETE FROM speech_record WHERE session_id = ?", (session,))
            conn.execute("DELETE FROM ai_answer_record WHERE session_id = ?", (session,))
            conn.execute("DELETE FROM meeting_offline_summary WHERE session_id = ?", (session,))
            conn.execute("DELETE FROM meeting_session WHERE session_id = ?", (session,))
            conn.commit()
        finally:
            conn.close()


def list_sessions(limit: int = 100) -> list[dict]:
    init()
    with _LOCK:
        conn = connect()
        try:
            return _rows(
                conn,
                "SELECT * FROM meeting_session ORDER BY create_time DESC, rowid DESC LIMIT ?",
                (max(1, min(int(limit or 100), 500)),),
            )
        finally:
            conn.close()


# ---------------------------------------------------------------- 转写记录


def add_segments(session: str, segments: list[dict]) -> int:
    """追加转写段；用唯一索引去重（跨切片重复传同一段不会翻倍）。"""
    if not segments:
        return 0
    init()
    inserted = 0
    with _LOCK:
        conn = connect()
        try:
            for seg in segments:
                content = (seg.get("content") or seg.get("text") or "").strip()
                if not content:
                    continue
                try:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO speech_record"
                        " (session_id, spk_id, start_ts, end_ts, content, name)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (session, str(seg.get("spk_id") or seg.get("spk") or ""),
                         int(seg.get("start_ts") or 0), int(seg.get("end_ts") or 0),
                         content, str(seg.get("name") or "")),
                    )
                    inserted += 1 if cur.rowcount > 0 else 0
                except sqlite3.Error:
                    continue
            conn.commit()
        finally:
            conn.close()
    return inserted


def get_segments(session: str, limit: int | None = None) -> list[dict]:
    init()
    with _LOCK:
        conn = connect()
        try:
            if limit:
                rows = _rows(conn,
                             "SELECT * FROM speech_record WHERE session_id = ?"
                             " ORDER BY start_ts ASC, id ASC LIMIT ?",
                             (session, int(limit)))
            else:
                rows = _rows(conn,
                             "SELECT * FROM speech_record WHERE session_id = ?"
                             " ORDER BY start_ts ASC, id ASC", (session,))
            return rows
        finally:
            conn.close()


def get_tail_segments(session: str, limit: int = 16) -> list[dict]:
    init()
    with _LOCK:
        conn = connect()
        try:
            rows = _rows(conn,
                         "SELECT * FROM speech_record WHERE session_id = ?"
                         " ORDER BY start_ts DESC, id DESC LIMIT ?",
                         (session, int(limit)))
            rows.reverse()
            return rows
        finally:
            conn.close()


def count_segments(session: str) -> int:
    init()
    with _LOCK:
        conn = connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM speech_record WHERE session_id = ?",
                               (session,)).fetchone()
            return int(row["n"] if row else 0)
        finally:
            conn.close()


# ---------------------------------------------------------------- AI 问答


def add_answer(session: str, context_snapshot: str, answer: str,
               create_time: str | None = None) -> dict:
    init()
    with _LOCK:
        conn = connect()
        try:
            cur = conn.execute(
                "INSERT INTO ai_answer_record (session_id, context_snapshot, answer, create_time)"
                " VALUES (?, ?, ?, ?)",
                (session, context_snapshot or "", answer or "", create_time or now_text()),
            )
            conn.commit()
            got = _row(conn, "SELECT * FROM ai_answer_record WHERE id = ?", (cur.lastrowid,))
            return got or {}
        finally:
            conn.close()


def get_answers(session: str, limit: int = 50) -> list[dict]:
    init()
    with _LOCK:
        conn = connect()
        try:
            return _rows(conn,
                         "SELECT * FROM ai_answer_record WHERE session_id = ?"
                         " ORDER BY id ASC LIMIT ?", (session, int(limit)))
        finally:
            conn.close()


def count_answers(session: str) -> int:
    init()
    with _LOCK:
        conn = connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM ai_answer_record WHERE session_id = ?",
                               (session,)).fetchone()
            return int(row["n"] if row else 0)
        finally:
            conn.close()


def get_last_answer(session: str) -> dict | None:
    init()
    with _LOCK:
        conn = connect()
        try:
            return _row(conn,
                        "SELECT * FROM ai_answer_record WHERE session_id = ?"
                        " ORDER BY id DESC LIMIT 1", (session,))
        finally:
            conn.close()


# ---------------------------------------------------------------- 离线归集


def save_offline_summary(session: str, payload: dict) -> None:
    init()
    with _LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO meeting_offline_summary (session_id, payload, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload,"
                " updated_at=excluded.updated_at",
                (session, json.dumps(payload, ensure_ascii=False), now_text()),
            )
            conn.commit()
        finally:
            conn.close()


def get_offline_summary(session: str) -> dict | None:
    init()
    with _LOCK:
        conn = connect()
        try:
            row = _row(conn, "SELECT payload FROM meeting_offline_summary WHERE session_id = ?",
                       (session,))
            if not row:
                return None
            try:
                return json.loads(row.get("payload") or "{}")
            except Exception:
                return None
        finally:
            conn.close()

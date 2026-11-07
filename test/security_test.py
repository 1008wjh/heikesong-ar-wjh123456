"""安全/资源上限回归测试（独立端口 + 临时数据目录）。

跑法：python test/security_test.py
覆盖：
  - 静态文件路径穿越 / 前缀绕过 / 绝对路径
  - 本地文件路径输入默认关闭（远端只能用 base64/dataURL）
  - 请求体上限（413）
  - 会议分片/时长上限
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
proc = None
log_fp = None
tmpdir = ""
ok_count = 0
fail_count = 0


def check(name, cond, extra=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  ok   {name} {extra}")
    else:
        fail_count += 1
        print(f"  FAIL {name} {extra}")


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def raw_get(path):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    return data


def raw_headers(path, content_length):
    """只发头不发 body，用来测"读 body 前就拒绝"。"""
    s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    s.sendall(
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        f"Content-Type: application/json\r\nContent-Length: {content_length}\r\n"
        f"Connection: close\r\n\r\n".encode()
    )
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    return data


def silent_wav_b64(seconds):
    buf = io.BytesIO()
    pcm = np.zeros(int(16000 * seconds), dtype="<i2")
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def wait_health(timeout=45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if get("/api/health")[0] == 200:
                return True
        except Exception:
            pass
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.3)
    return False


def start_server():
    global proc, tmpdir, log_fp
    tmpdir = tempfile.mkdtemp(prefix="memorylens-security-test-")
    env = os.environ.copy()
    env.update({
        "AR_PORT": str(PORT),
        "AR_DATA_DIR": os.path.join(tmpdir, "data"),
        "AR_MEETING_DIR": os.path.join(tmpdir, "meetings"),
        "AR_ASR_PROVIDER": "off",
        "AR_MAX_BODY_BYTES": "200000",
        "AR_ALLOW_LOCAL_FILES": "0",
        "AR_MEETING_MAX_CHUNKS": "2",
        "AR_MEETING_MAX_SECONDS": "3",
        "AR_MEETING_MAX_CHUNK_SECONDS": "2.5",
        "PYTHONIOENCODING": "utf-8",
    })
    log_fp = open(os.path.join(tmpdir, "server.log"), "wb")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "src", "server.py")],
        cwd=ROOT, env=env, stdout=log_fp, stderr=subprocess.STDOUT,
    )
    if not wait_health():
        try:
            log_fp.flush()
            with open(os.path.join(tmpdir, "server.log"), "rb") as f:
                print(f.read().decode("utf-8", "replace")[-3000:])
        except Exception:
            pass
        raise SystemExit("服务起不来")


def stop_server():
    global proc, tmpdir, log_fp
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        proc = None
    if log_fp is not None:
        log_fp.close()
        log_fp = None
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)
        tmpdir = ""


def main():
    print("0) 起隔离服务（端口 %d，本地文件输入关闭，body/会议上限调小）" % PORT)
    start_server()

    print("1) 静态文件穿越")
    check("正常首页可访问", get("/")[0] == 200 and b"<" in get("/")[1])
    check("正常 JS 可访问", get("/app.js")[0] == 200)
    check("普通 ../ 被拒", b" 404 " in raw_get("/../data/people.json").split(b"\r\n")[0])
    check("反斜杠 ../ 被拒", b" 404 " in raw_get("/..\\data\\people.json").split(b"\r\n")[0])
    check("编码 ..%2f 被拒", b" 404 " in raw_get("/..%2fdata/people.json").split(b"\r\n")[0])
    check("绝对路径被拒", b" 404 " in raw_get("/C:/Windows/win.ini").split(b"\r\n")[0])

    print("2) 前缀绕过（webx/secret.txt）")
    sibling = os.path.join(ROOT, "webx")
    os.makedirs(sibling, exist_ok=True)
    with open(os.path.join(sibling, "secret.txt"), "w", encoding="utf-8") as f:
        f.write("PREFIX-BYPASS")
    try:
        first = raw_get("/../webx/secret.txt").split(b"\r\n")[0]
        check("同前缀兄弟目录不能读", b" 404 " in first, first.decode("latin1"))
    finally:
        shutil.rmtree(sibling, ignore_errors=True)

    print("3) 本地文件路径默认关闭")
    src = os.path.join(ROOT, "test", "voice", "huihui_a.wav")
    code, meta = post("/api/v1/session/create", {"title": "sec"})
    sid = meta.get("sessionId", "")
    code, r = post("/api/v1/audio/upload", {"sessionId": sid, "seq": 1, "audio": src})
    check("绝对路径被拒", code != 200 and r.get("ok") is False, str(code))
    code, r = post("/api/v1/audio/upload", {"sessionId": sid, "seq": 1, "audio": silent_wav_b64(2)})
    check("base64 正常可用", code == 200 and r.get("ok") is True, str(code))

    print("4) 请求体上限")
    resp = raw_headers("/api/v1/session/create", 300000)
    check("超大 body 返回 413", b" 413 " in resp.split(b"\r\n")[0], resp.split(b"\r\n")[0].decode("latin1"))

    print("5) 会议资源上限")
    code, meta = post("/api/v1/session/create", {"title": "cap"})
    sid2 = meta["sessionId"]
    code, r = post("/api/v1/audio/upload", {"sessionId": sid2, "seq": 1, "audio": silent_wav_b64(2)})
    check("第一片正常", code == 200, str(code))
    code, r = post("/api/v1/audio/upload", {"sessionId": sid2, "seq": 2, "audio": silent_wav_b64(2)})
    check("总时长超限被拒", code == 500 and "时长" in r.get("error", ""), str(r.get("error")))
    code, meta = post("/api/v1/session/create", {"title": "chunk-cap"})
    sid3 = meta["sessionId"]
    code, r = post("/api/v1/audio/upload", {"sessionId": sid3, "seq": 1, "audio": silent_wav_b64(3)})
    check("单片超限被拒", code == 500 and "分片" in r.get("error", ""), str(r.get("error")))

    return 0


if __name__ == "__main__":
    try:
        main()
    finally:
        stop_server()
    print(f"\n{ok_count} passed, {fail_count} failed")
    raise SystemExit(1 if fail_count else 0)

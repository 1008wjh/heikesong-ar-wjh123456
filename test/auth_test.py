"""鉴权 / TLS / CSRF 回归测试。

跑法：python test/auth_test.py
说明：TLS 部分需要本机有 openssl（Git for Windows 自带）；没有就跳过并打印 SKIP。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOKEN = "test-token-123"
PORT = 8796
TLS_PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"
TLS_BASE = f"https://127.0.0.1:{TLS_PORT}"
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


def request(method, url, body=None, headers=None, context=None):
    req = urllib.request.Request(url, method=method, data=body,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15, context=context) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def raw_post(path, body, headers):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    head = f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n"
    for k, v in headers.items():
        head += f"{k}: {v}\r\n"
    head += f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    s.sendall(head.encode() + body)
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    return data


def wait_health(url_base, timeout=45.0, context=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url_base + "/api/health")
            with urllib.request.urlopen(req, timeout=2, context=context) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.3)
    return False


def start_server(port, extra_env=None):
    global proc, tmpdir, log_fp
    tmpdir = tempfile.mkdtemp(prefix="memorylens-auth-test-")
    env = os.environ.copy()
    env.update({
        "AR_PORT": str(port),
        "AR_DATA_DIR": os.path.join(tmpdir, "data"),
        "AR_MEETING_DIR": os.path.join(tmpdir, "meetings"),
        "AR_API_TOKEN": TOKEN,
        "AR_ASR_PROVIDER": "off",
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(extra_env or {})
    log_fp = open(os.path.join(tmpdir, "server.log"), "wb")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "src", "server.py")],
        cwd=ROOT, env=env, stdout=log_fp, stderr=subprocess.STDOUT,
    )


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


def openssl_path():
    p = shutil.which("openssl")
    if p:
        return p
    for c in (r"D:\git\Git\usr\bin\openssl.exe",
              r"C:\Program Files\Git\usr\bin\openssl.exe"):
        if os.path.isfile(c):
            return c
    return None


def test_auth():
    print("1) 鉴权")
    start_server(PORT)
    try:
        if not wait_health(BASE):
            raise SystemExit("服务起不来")
        check("health 不鉴权", request("GET", BASE + "/api/health")[0] == 200)
        check("people 无 token → 401", request("GET", BASE + "/api/people")[0] == 401)
        check("错误 token → 401",
              request("GET", BASE + "/api/people", headers={"X-API-Token": "bad"})[0] == 401)
        check("X-API-Token 正确 → 200",
              request("GET", BASE + "/api/people", headers={"X-API-Token": TOKEN})[0] == 200)
        check("Bearer 正确 → 200",
              request("GET", BASE + "/api/people",
                      headers={"Authorization": "Bearer " + TOKEN})[0] == 200)
        check("静态页不鉴权", request("GET", BASE + "/")[0] == 200)

        body = json.dumps({"title": "auth"}).encode()
        check("写接口无 token → 401",
              request("POST", BASE + "/api/v1/session/create", body,
                      {"Content-Type": "application/json"})[0] == 401)
        status, data = request("POST", BASE + "/api/v1/session/create", body,
                               {"Content-Type": "application/json",
                                "X-API-Token": TOKEN})
        check("写接口带 token → 200", status == 200, str(status))
        sid = json.loads(data).get("sessionId", "")
        status, _ = request("DELETE", BASE + f"/api/v1/session/{sid}",
                            headers={"X-API-Token": TOKEN})
        check("DELETE 带 token → 200", status == 200, str(status))

        print("2) CSRF / Content-Type")
        resp = raw_post("/api/v1/session/create", body,
                        {"Content-Type": "text/plain", "X-API-Token": TOKEN})
        check("text/plain → 415", b" 415 " in resp.split(b"\r\n")[0], resp.split(b"\r\n")[0].decode("latin1"))
        resp = raw_post("/api/v1/session/create", body,
                        {"Content-Type": "application/json", "X-API-Token": TOKEN,
                         "Sec-Fetch-Site": "cross-site"})
        check("cross-site → 403", b" 403 " in resp.split(b"\r\n")[0], resp.split(b"\r\n")[0].decode("latin1"))
        resp = raw_post("/api/v1/session/create", body,
                        {"Content-Type": "application/json", "X-API-Token": TOKEN,
                         "Origin": "http://evil.example"})
        check("Origin 不匹配 → 403", b" 403 " in resp.split(b"\r\n")[0], resp.split(b"\r\n")[0].decode("latin1"))

        # 无 body 的 POST 也必须过 CSRF 检查（不然跨站表单可以直接触发 finalize）
        resp = raw_post(f"/api/v1/session/{sid}/finalize", b"",
                        {"X-API-Token": TOKEN, "Sec-Fetch-Site": "cross-site"})
        check("无 body 的 POST 跨站 → 403", b" 403 " in resp.split(b"\r\n")[0],
              resp.split(b"\r\n")[0].decode("latin1"))

        # DELETE 同样要过 CSRF
        status, _ = request("DELETE", BASE + f"/api/v1/session/{sid}",
                            headers={"X-API-Token": TOKEN, "Sec-Fetch-Site": "cross-site"})
        check("DELETE 跨站 → 403", status == 403, str(status))
    finally:
        stop_server()


def test_tls():
    print("3) TLS")
    ossl = openssl_path()
    if not ossl:
        print("  SKIP 没找到 openssl，跳过 TLS 实测（代码路径见 src/server.py __main__）")
        return
    cert = os.path.join(tempfile.mkdtemp(prefix="ml-cert-"), "cert.pem")
    key = os.path.join(os.path.dirname(cert), "key.pem")
    subprocess.run([ossl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", key, "-out", cert, "-days", "1",
                    "-subj", "/CN=localhost"],
                   check=True, capture_output=True)
    start_server(TLS_PORT, {"AR_TLS_CERT": cert, "AR_TLS_KEY": key})
    try:
        ctx = ssl._create_unverified_context()
        if not wait_health(TLS_BASE, context=ctx):
            raise SystemExit("TLS 服务起不来")
        check("HTTPS health → 200",
              request("GET", TLS_BASE + "/api/health", context=ctx)[0] == 200)
        check("HTTPS + token → 200",
              request("GET", TLS_BASE + "/api/people",
                      headers={"X-API-Token": TOKEN}, context=ctx)[0] == 200)
        plain_failed = False
        try:
            request("GET", f"http://127.0.0.1:{TLS_PORT}/api/health")
        except Exception:
            plain_failed = True
        check("明文访问 TLS 端口失败", plain_failed)
    finally:
        stop_server()
        shutil.rmtree(os.path.dirname(cert), ignore_errors=True)


def main():
    test_auth()
    test_tls()
    print(f"\n{ok_count} passed, {fail_count} failed")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())

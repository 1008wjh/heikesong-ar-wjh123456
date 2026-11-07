"""多人会议 API 端到端测试：起一个隔离的数据目录 + 独立端口，不碰你真实的 people.json。

跑法：python test/meeting_api_test.py
覆盖：创建会议 → 分片上传 → 多人分离 → 实名映射 → 绑定/录入声纹 → 时间锚点回放 → 预留的 action 接口
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VOICE_DIR = os.path.join(ROOT, "test", "voice")
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
ORDER = ["huihui_a.wav", "hazel_a.wav", "zira_a.wav",
         "huihui_b.wav", "hazel_b.wav", "zira_b.wav"]
NAMES = ["测试甲", "测试乙", "测试丙"]

ok_count = 0
fail_count = 0
proc = None
log_fp = None
tmpdir = ""


def check(name, cond, extra=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  ok   {name} {extra}")
    else:
        fail_count += 1
        print(f"  FAIL {name} {extra}")


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def wav_b64(name):
    with open(os.path.join(VOICE_DIR, name), "rb") as f:
        return base64.b64encode(f.read()).decode()


def silent_wav_b64(seconds=2.0):
    buf = io.BytesIO()
    pcm = np.zeros(int(16000 * seconds), dtype="<i2")
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def delete(path):
    req = urllib.request.Request(BASE + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def wait_health(timeout=45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code, _ = get("/api/health")
            if code == 200:
                return True
        except Exception:
            pass
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.3)
    return False


def start_server():
    global proc, tmpdir, log_fp
    tmpdir = tempfile.mkdtemp(prefix="memorylens-meeting-test-")
    env = os.environ.copy()
    env.update({
        "AR_PORT": str(PORT),
        "AR_DATA_DIR": os.path.join(tmpdir, "data"),
        "AR_MEETING_DIR": os.path.join(tmpdir, "meetings"),
        "AR_ASR_PROVIDER": "off",
        "PYTHONIOENCODING": "utf-8",
    })
    # 必须落文件：服务端每个 /api/ 请求都会写日志，接 PIPE 又不读会把管道写满，
    # 服务线程阻塞后表现为客户端 timeout。
    log_fp = open(os.path.join(tmpdir, "server.log"), "wb")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "src", "server.py")],
        cwd=ROOT, env=env,
        stdout=log_fp, stderr=subprocess.STDOUT,
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
    print("0) 起隔离服务（临时数据目录，端口 %d）" % PORT)
    start_server()
    check("health", get("/api/health")[0] == 200)

    print("1) 建 3 个人并录声纹（各用第一句）")
    pids = []
    for i, name in enumerate(NAMES):
        code, r = post("/api/people/save", {"name": name, "role": "测试", "info": ["会议室测试"]})
        check(f"建人 {name}", code == 200 and r.get("ok") is True, str(code))
        pids.append(r["person"]["id"])
        code, r = post("/api/voice/enroll", {"id": pids[-1], "audio": wav_b64(ORDER[i])})
        check(f"录声纹 {name}", code == 200 and r["person"].get("voice_count") == 1, str(code))

    print("2) 创建会议")
    code, meta = post("/api/v1/session/create", {"title": "三人会议测试", "userId": "u_test"})
    sid = meta.get("sessionId", "")
    check("sessionId", code == 200 and sid.startswith("s_"), sid)
    check("初始 collecting", meta.get("status") == "collecting")

    print("3) 上传 6 个分片")
    total = 0.0
    for i, name in enumerate(ORDER, start=1):
        code, r = post("/api/v1/audio/upload", {"sessionId": sid, "seq": i, "audio": wav_b64(name)})
        check(f"分片 {i}", code == 200 and r.get("received") == i, str(code))
        total = r.get("duration", total)
    check("总时长 ≈ 32.2s", abs(total - 32.16) < 1.0, f"{total:.2f}s")

    print("4) 生成多人分离")
    t0 = time.time()
    code, r = post(f"/api/v1/session/{sid}/finalize", {})
    elapsed = time.time() - t0
    check("finalize ok", code == 200 and r.get("status") == "finalized", str(code))
    check("识别 3 人", r.get("speaker_count") == 3, str(r.get("speaker_count")))
    check("实名映射 3 个", {s.get("name") for s in r.get("speakers", [])} == set(NAMES),
          str([s.get("name") for s in r.get("speakers", [])]))
    check("分段 >= 6", len(r.get("segments", [])) >= 6, str(len(r.get("segments", []))))
    check("干净音频无重叠丢弃", r.get("dropped_overlap") == 0, str(r.get("dropped_overlap")))
    check("耗时 < 20s", elapsed < 20, f"{elapsed:.1f}s")
    spk_ids = [s["spk_id"] for s in r.get("speakers", [])]

    print("5) 查询会议 / 转写")
    code, one = get(f"/api/v1/session/{sid}")
    check("GET session", code == 200 and one.get("sessionId") == sid, str(code))
    code, tr = get(f"/api/v1/session/{sid}/transcript")
    check("GET transcript", code == 200 and len(tr.get("segments", [])) >= 6, str(code))
    check("转写带 speaker", all(seg.get("speaker") for seg in tr.get("segments", [])))

    print("6) 匿名 spk 绑定到新人 + 录入声纹")
    code, pr = post("/api/people/save", {"name": "临时同学", "role": "外部"})
    new_pid = pr["person"]["id"]
    code, r = post("/api/v1/speaker/bind", {
        "sessionId": sid, "spkId": spk_ids[0], "personId": new_pid, "enroll": True,
    })
    check("bind ok", code == 200 and r.get("ok") is True, str(code))
    bound = next(s for s in r["speakers"] if s["spk_id"] == spk_ids[0])
    check("名字已更新", bound.get("name") == "临时同学", str(bound.get("name")))
    code, tr = get(f"/api/v1/session/{sid}/transcript")
    check("转写跟着改名", any(seg.get("speaker") == "临时同学" for seg in tr["segments"]))
    code, p = get("/api/people")
    person = next(x for x in p["people"] if x["id"] == new_pid)
    check("绑定后写入声纹", person.get("voice_count") == 1, str(person.get("voice_count")))

    print("7) 时间锚点回放")
    req = urllib.request.Request(BASE + f"/api/v1/audio/play?sessionId={sid}&timestamp=10&before=1&after=1")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    check("返回 WAV", data[:4] == b"RIFF" and b"WAVE" in data[:16], ctype)
    check("切片约 2s", abs(len(data) / 2 / 16000 - 2.0) < 0.3, f"{len(data) / 32000:.2f}s")

    print("8) 眼镜轮询卡片")
    code, latest = get("/api/latest")
    check("kind=meeting", latest.get("kind") == "meeting", str(latest.get("kind")))
    card = (latest.get("faces") or [{}])[0]
    check("卡片有 2-4 行", 2 <= len(card.get("lines") or []) <= 4, str(card.get("lines")))

    print("9) 预留的按键指令接口")
    code, r = post("/api/v1/action/trigger", {"sessionId": sid, "actionType": "summary"})
    check("summary 预留", code == 200 and r.get("state") == "reserved", str(code))
    code, r = post("/api/v1/action/trigger", {"sessionId": sid, "actionType": "bad"})
    check("非法动作报错", code == 500 and r.get("ok") is False, str(code))

    print("10) 单段音频直连分离")
    code, r = post("/api/diarize", {"audio": wav_b64("huihui_a.wav")})
    check("单段 1 人", code == 200 and r.get("speaker_count") == 1, str(r.get("speaker_count")))
    check("私有质心不外泄", "_centroids" not in r)

    print("11) 错误路径")
    code, r = post("/api/v1/audio/upload", {"sessionId": "s_notexist", "audio": wav_b64(ORDER[0])})
    check("不存在的会议报错", code == 500 and r.get("ok") is False, str(code))
    code, r = post("/api/v1/speaker/bind", {"sessionId": sid, "spkId": "spk_99", "name": "测试甲"})
    check("不存在的 spk 报错", code == 500 and r.get("ok") is False, str(code))

    print("12) 重复 seq / 静音 / 重新 finalize")
    code, r = post("/api/v1/audio/upload", {"sessionId": sid, "seq": 1, "audio": wav_b64(ORDER[0])})
    check("重复 seq 报错", code == 500 and r.get("ok") is False, str(code))

    code, silent = post("/api/v1/session/create", {"title": "静音会议"})
    silent_sid = silent["sessionId"]
    post("/api/v1/audio/upload", {"sessionId": silent_sid, "seq": 1, "audio": silent_wav_b64(2)})
    code, r = post(f"/api/v1/session/{silent_sid}/finalize", {})
    check("静音 finalize 报错", code == 500 and "人声" in r.get("error", ""), str(r.get("error")))

    code, r = post("/api/v1/audio/upload", {"sessionId": sid, "seq": 7, "audio": wav_b64(ORDER[0])})
    check("追加分片成功", code == 200 and r.get("chunks") == 7, str(code))
    code, one = get(f"/api/v1/session/{sid}")
    check("追加后回到 collecting", one.get("status") == "collecting", str(one.get("status")))
    code, r = post(f"/api/v1/session/{sid}/finalize", {})
    check("重新 finalize ok", code == 200 and r.get("speaker_count") == 3, str(r.get("speaker_count")))
    check("时长增加", r.get("duration", 0) > 32, str(r.get("duration")))

    print("13) enroll=\"false\" 不能被当成 true")
    code, pr = post("/api/people/save", {"name": "不该入库", "role": "测试"})
    pid2 = pr["person"]["id"]
    code, r = post("/api/v1/speaker/bind", {
        "sessionId": sid, "spkId": spk_ids[0], "personId": pid2, "enroll": "false",
    })
    check("bind ok", code == 200, str(code))
    code, p = get("/api/people")
    person = next(x for x in p["people"] if x["id"] == pid2)
    check("false 不写声纹", person.get("voice_count") == 0, str(person.get("voice_count")))

    print("14) 删除会议")
    code, r = delete(f"/api/v1/session/{silent_sid}")
    check("DELETE 会议", code == 200 and r.get("deleted") is True, str(code))
    code, r = get(f"/api/v1/session/{silent_sid}")
    check("删后查询报错", code == 500, str(code))
    code, r = delete(f"/api/v1/session/{silent_sid}")
    check("重复删除报错", code == 500, str(code))

    print("15) 参数边界")
    code, r = post("/api/v1/audio/upload", {"sessionId": sid, "seq": 0, "audio": wav_b64(ORDER[0])})
    check("seq=0 报错", code == 500 and r.get("ok") is False, str(code))
    req = urllib.request.Request(
        BASE + f"/api/v1/audio/play?sessionId={sid}&timestamp=-1&before=1&after=1")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    check("负时间戳被钳到 0", data[:4] == b"RIFF", str(data[:4]))
    code, r = get(f"/api/v1/audio/play?sessionId={sid}&timestamp=abc")
    check("非法时间戳报错", code == 500 and r.get("ok") is False, str(code))
    return 0


if __name__ == "__main__":
    try:
        main()
    finally:
        stop_server()
    print(f"\n{ok_count} passed, {fail_count} failed")
    raise SystemExit(1 if fail_count else 0)

# -*- coding: utf-8 -*-
"""端到端测 /api/voice/identify：合成一段"主人 + 对方"的对话，看能不能只认出对方。"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, r"E:\ar-memory\src")
import voice

TMP = os.path.join(tempfile.gettempdir(), "ml-ident-test")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
os.environ["AR_DATA_DIR"] = TMP

import store

st = store.Store()
zym = voice.embed(voice.read_wav(open(r"E:\ar-memory\test\voice\zira_a.wav", "rb").read()))
hui = voice.embed(voice.read_wav(open(r"E:\ar-memory\test\voice\huihui_a.wav", "rb").read()))
owner = st.save(name="wjh", role="我", info=["主人"])
st.add_voice(owner["id"], zym)
wang = st.save(name="王姐", role="产品经理", info=["下周给报价单"])
st.add_voice(wang["id"], hui)
st.set_owner(owner["id"])

# 合成：zira(主人) 5s + 空 0.5s + huihui(对方) 5s
import numpy as np
a = voice.read_wav(open(r"E:\ar-memory\test\voice\zira_a.wav", "rb").read())[: 16000 * 5]
b = voice.read_wav(open(r"E:\ar-memory\test\voice\huihui_a.wav", "rb").read())[: 16000 * 5]
gap = np.zeros(8000, dtype=np.float32)
mix = np.concatenate([a, gap, b])
pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2").tobytes()
import struct
hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", len(pcm))
wav = hdr + pcm
uri = "data:audio/wav;base64," + base64.b64encode(wav).decode()

py = sys.executable
proc = subprocess.Popen([py, r"E:\ar-memory\src\server.py"], env={**os.environ, "AR_PORT": "8773"},
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen("http://127.0.0.1:8773/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)

    def post(path, body):
        req = urllib.request.Request("http://127.0.0.1:8773" + path,
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            print('HTTP', e.code, e.read().decode()[:400])
            raise

    r = post("/api/voice/identify", {"audio": uri})
    print(json.dumps({k: r.get(k) for k in ("ok", "status", "name", "similarity")}, ensure_ascii=False))
    for c in r.get("clusters") or []:
        print("  cluster", c)

    # 只有主人说话
    only = np.concatenate([a, gap, a])
    pcm2 = (np.clip(only, -1, 1) * 32767).astype("<i2").tobytes()
    wav2 = hdr[:4] + struct.pack("<I", 36 + len(pcm2)) + hdr[8:40] + struct.pack("<I", len(pcm2)) + pcm2
    uri2 = "data:audio/wav;base64," + base64.b64encode(wav2).decode()
    r2 = post("/api/voice/identify", {"audio": uri2})
    print("only-owner ->", r2.get("status"), r2.get("error"))
finally:
    proc.terminate()
    shutil.rmtree(TMP, ignore_errors=True)

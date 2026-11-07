import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm  # noqa: E402

print("provider:", llm.ASR_PROVIDER, "| asr ready:", llm.asr_available(), "| llm ready:", llm.available())
print("status:", llm.status())

for name in ["huihui_b", "zira_b"]:
    p = os.path.join("test", "voice", name + ".wav")
    data = io.open(p, "rb").read()
    t0 = __import__("time").time()
    try:
        text = llm.transcribe(data)
        print("%-10s %5.0fms  %s" % (name, (__import__("time").time() - t0) * 1000, text))
    except Exception as e:
        print("%-10s FAILED: %s" % (name, e))

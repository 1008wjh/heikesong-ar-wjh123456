#!/usr/bin/env bash
# 一次性排障：起 Xvfb + twm + 模拟器，然后打印窗口树和模拟器日志。
set -uo pipefail

KIT_SRC="${KIT_SRC:-/kit}"
STAGE=/opt/kit
export DISPLAY=:99
PORT="${1:-28045}"
WAIT="${2:-26}"

echo "== stage kit =="
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a "$KIT_SRC/." "$STAGE/"
find "$STAGE" -type f -exec chmod 0755 {} +
chmod -R 0644 "$STAGE/runtime/runtime/@irisview/application-api"
export FUSIONUI_DEVELOPER_KIT_ROOT="$STAGE"

echo "== xvfb =="
Xvfb :99 -screen 0 1280x960x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
sleep 2

echo "== twm =="
if [[ "${NO_TWM:-0}" == "1" ]]; then
  echo "skipped"
  TWM_PID=""
else
  twm >/tmp/twm.log 2>&1 &
  TWM_PID=$!
  sleep 1
fi

echo "== run =="
python3 "$STAGE/sdk/fusionui-app-sdk/fusionui_sdk.py" run "${APP:-/work/dist/memorylens.fuiapp}" \
  --port "$PORT" --allow-debug-unsigned --display-mode mono --client-mode normal \
  >/tmp/sim.log 2>&1 &
SIM_PID=$!

sleep "$WAIT"

if [[ "${SCAN:-0}" == "1" ]]; then
  echo "== trigger scan =="
  python3 - <<'PY'
import base64, json, os, urllib.request
backend = os.environ.get("MEMORYLENS_BACKEND", "http://host.docker.internal:8765")
img = "/work/test/messi5.jpg"
data = base64.b64encode(open(img, "rb").read()).decode()
req = urllib.request.Request(backend + "/api/scan",
    data=json.dumps({"image": "data:image/jpeg;base64," + data}).encode(),
    headers={"Content-Type": "application/json"})
print("scan ->", urllib.request.urlopen(req, timeout=15).read(120).decode())
PY
  sleep "${SCAN_WAIT:-4}"
fi

if [[ "${AGENT:-0}" == "1" ]]; then
  echo "== trigger agent feed =="
  python3 - <<'PY'
import base64, json, os, urllib.request
backend = os.environ.get("MEMORYLENS_BACKEND", "http://host.docker.internal:8765")
wav = os.environ.get("AGENT_WAV", "/work/test/voice/huihui_b.wav")
data = "data:audio/wav;base64," + base64.b64encode(open(wav, "rb").read()).decode()
req = urllib.request.Request(backend + "/api/agent/feed",
    data=json.dumps({"session": "sim-demo", "audio": data, "auto_add": False}).encode(),
    headers={"Content-Type": "application/json"})
resp = json.load(urllib.request.urlopen(req, timeout=120))
v = resp.get("view") or {}
print("agent ->", v.get("state"), v.get("person"), (v.get("rows") or [])[:2])
PY
  sleep "${AGENT_WAIT:-5}"
fi

# 想截"详情页"就点一下 Client 控制台里的「确认」按钮（坐标是 1280x960 布局下量的）
if [[ "${PRESS_CONFIRM:-0}" == "1" ]]; then
  echo "== press confirm =="
  timeout 10 xdotool mousemove "${CONFIRM_X:-1065}" "${CONFIRM_Y:-220}" click 1 2>/dev/null || echo "click failed"
  sleep 2
fi

echo "== xdotool windows =="
timeout 10 xdotool search --onlyvisible --name "" 2>/dev/null | while read -r id; do
  geom=$(timeout 5 xdotool getwindowgeometry --shell "$id" 2>/dev/null | tr '\n' ' ')
  echo "$id $geom"
done

echo "== capture =="
mkdir -p /work/test
timeout 20 import -window root /work/test/sim_root.png 2>/dev/null || echo "root capture failed"
# 逐个窗口抓，尤其是 640x480 的子窗口（真正的镜片帧缓冲）
for id in 0x200001 0x200002; do
  timeout 20 import -window "$id" "/work/test/sim_${id}.png" 2>/dev/null || echo "capture $id failed"
done
# 再看一眼（用来验证自动滚动）
if [[ "${SCAN:-0}" == "1" ]]; then
  sleep 3
  timeout 20 import -window 0x200002 /work/test/sim_0x200002_b.png 2>/dev/null || true
fi
ls -l /work/test/sim_root.png /work/test/sim_0x*.png 2>/dev/null || true
echo "== tree =="
timeout 10 xwininfo -root -tree 2>&1 | head -25
echo "== sim.log =="
grep -iE "install|version|reject|replac|package" /tmp/sim.log | head -15
tail -18 /tmp/sim.log
kill "$SIM_PID" "$TWM_PID" 2>/dev/null

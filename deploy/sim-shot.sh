#!/usr/bin/env bash
# 在容器里跑起来：Xvfb 虚拟屏 + 模拟器 + 抓一张真·镜片截图
# 由 tools/kit-docker.ps1 run 调用；也可以直接：
#   docker run --rm -v <kit>:/kit -v <proj>:/work -w /work memorylens-irisview bash /work/deploy/sim-shot.sh
set -uo pipefail

PORT="${1:-28045}"
WAIT="${2:-25}"
CLIENT_MODE="${3:-normal}"
KIT_SRC="${FUSIONUI_DEVELOPER_KIT_ROOT:-/kit}"
SDK="$KIT_SRC/sdk/fusionui-app-sdk/fusionui_sdk.py"
PKG=/work/dist/memorylens.fuiapp

if [[ ! -f "$PKG" ]]; then
  echo "先打包：tools\\kit-docker.ps1 package" >&2
  exit 2
fi

# Windows 绑定挂载进来的文件全是 0777，而运行时会校验"打包资产必须 0644/0600"。
# 所以先把开发包拷进容器自己的文件系统，并把权限摆正（约 416MB，10 秒左右）。
STAGE=/opt/kit
if [[ ! -d "$STAGE/runtime" ]]; then
  echo "== 拷开发包到容器内（修正权限）=="
  mkdir -p "$STAGE"
  cp -a "$KIT_SRC"/. "$STAGE"/
  # Windows 挂载把一切变成 0777：先统一成 0755（二进制要能执行、目录要能进），
  # 再把运行时校验的那套 application-api 资产改成严格的 0644。
  find "$STAGE" -type d -exec chmod 0755 {} +
  find "$STAGE" -type f -exec chmod 0755 {} +
  find "$STAGE/runtime/runtime/@irisview/application-api" -type f -exec chmod 0644 {} +
fi
export FUSIONUI_DEVELOPER_KIT_ROOT="$STAGE"
SDK="$STAGE/sdk/fusionui-app-sdk/fusionui_sdk.py"
export PATH="$STAGE/bin:$PATH"

export DISPLAY=:99
Xvfb :99 -screen 0 1280x960x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!
sleep 2
# 起个最小窗口管理器：没有 WM 时 windowraise 不生效，Client 控制台会一直盖住镜片
if [[ "${WITH_TWM:-0}" == "1" ]]; then
  twm >/tmp/twm.log 2>&1 &
fi
TWM_PID=$!
sleep 1
if ! xdpyinfo >/dev/null 2>&1; then
  echo "Xvfb 没起来：" >&2
  cat /tmp/xvfb.log >&2
  kill "$TWM_PID" 2>/dev/null
  kill "$XVFB_PID" 2>/dev/null
  exit 3
fi

echo "== 模拟器启动（port $PORT / $CLIENT_MODE / state ${STATE_DIR:-shared}）=="
STATE_ARGS=()
if [[ -n "${STATE_DIR:-}" ]]; then
  mkdir -p "$STATE_DIR"
  STATE_ARGS=(--state-dir "$STATE_DIR")
fi
python3 "$SDK" run "$PKG" --port "$PORT" --allow-debug-unsigned --display-mode mono \
  --client-mode "$CLIENT_MODE" "${STATE_ARGS[@]}" >/tmp/sim.log 2>&1 &
SIM_PID=$!

sleep "$WAIT"

echo "== 找眼镜窗口 =="
LENS_WIN=""
LENS_X=0; LENS_Y=0
# twm 会把窗口套一层边框，所以不能只看顶层；直接在整棵树里找 640x480 的节点
LINE=$(timeout 8 xwininfo -root -tree 2>/dev/null | awk '/640x480/ {print; exit}')
if [[ -n "$LINE" ]]; then
  LENS_WIN=$(echo "$LINE" | awk '{print $1}')
  # 形如 640x480+0+0  +0+0
  GEO=$(echo "$LINE" | awk '{for(i=1;i<=NF;i++) if ($i ~ /^640x480/) print $i}')
  LENS_X=$(echo "$GEO" | sed -n 's/.*+\([0-9-]*\)+[0-9-]*$/\1/p')
  LENS_Y=$(echo "$GEO" | sed -n 's/.*+[0-9-]*+\([0-9-]*\)$/\1/p')
  LENS_X=${LENS_X:-0}; LENS_Y=${LENS_Y:-0}
fi
echo "lens=$LENS_WIN at $LENS_X,$LENS_Y"
if [[ "${SIM_DEBUG:-0}" == "1" ]]; then
  echo "--- window tree ---"
  timeout 8 xwininfo -root -tree 2>/dev/null | head -30
fi

if [[ -n "$LENS_WIN" ]]; then
  timeout 5 xdotool windowraise "$LENS_WIN" 2>/dev/null || true
  timeout 5 xdotool windowactivate "$LENS_WIN" 2>/dev/null || true
  sleep 2
  LINE=$(timeout 8 xwininfo -root -tree 2>/dev/null | awk '/640x480/ {print; exit}')
  GEO=$(echo "$LINE" | awk '{for(i=1;i<=NF;i++) if ($i ~ /^640x480/) print $i}')
  LENS_X=$(echo "$GEO" | sed -n 's/.*+\([0-9-]*\)+[0-9-]*$/\1/p'); LENS_X=${LENS_X:-0}
  LENS_Y=$(echo "$GEO" | sed -n 's/.*+[0-9-]*+\([0-9-]*\)$/\1/p'); LENS_Y=${LENS_Y:-0}
  echo "lens now at $LENS_X,$LENS_Y"
fi

echo "== 截屏 =="
mkdir -p /work/test
import -window root /work/test/simulator.png 2>/tmp/import-root.log || {
  echo "整屏截屏失败：" >&2; cat /tmp/import-root.log >&2
}
if [[ -n "$LENS_WIN" ]]; then
  # 直接抓镜片子窗口，比裁 root 干净（不会把 Client 控制台一起截进来）
  import -window "$LENS_WIN" /work/test/glasses_lens.png 2>/dev/null \
    || convert /work/test/simulator.png -crop 640x480+${LENS_X}+${LENS_Y} +repage /work/test/glasses_lens.png
fi
ls -l /work/test/simulator.png /work/test/glasses_lens.png 2>/dev/null || true

echo "== 模拟器日志（尾部）=="
tail -n 40 /tmp/sim.log || true

kill "$SIM_PID" 2>/dev/null
wait "$SIM_PID" 2>/dev/null
kill "$TWM_PID" 2>/dev/null
kill "$XVFB_PID" 2>/dev/null
exit 0

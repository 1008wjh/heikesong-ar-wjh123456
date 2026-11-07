#!/usr/bin/env bash
# 构建/打包 probe 应用（只用于排障）
set -euo pipefail
KIT=/mnt/c/Users/Lenovo/Downloads/IrisView-Developer-Kit-v1.0.0-LLVISION-v2/IrisView-Developer-Kit-v1.0.0-LLVISION-v2/IrisView-Developer-Kit-v1.0.0
export FUSIONUI_DEVELOPER_KIT_ROOT="$KIT"
export PATH="$HOME/tools/node-v24.13.0-linux-x64/bin:$PATH"
cd /mnt/e/ar-memory
rm -rf probe/dist
python3 "$KIT/sdk/fusionui-app-sdk/fusionui_sdk.py" test probe 2>&1 | tail -1
python3 "$KIT/sdk/fusionui-app-sdk/fusionui_sdk.py" build probe 2>&1 | tail -1
python3 "$KIT/sdk/fusionui-app-sdk/fusionui_sdk.py" package probe --output /mnt/e/ar-memory/dist/probe.fuiapp --debug-unsigned 2>&1 | tail -1

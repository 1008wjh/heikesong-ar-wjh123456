# 一键部署（Docker）

两个镜像，各管一件事：

| 镜像 | 干什么 | 谁需要 |
|---|---|---|
| `memorylens`（`deploy/Dockerfile.backend`） | 跑 `src/server.py`：声纹/人脸识别 + 人脉库 + 网页控制台 | 演示时当"云"，或直接云部署 |
| `memorylens-irisview`（`deploy/Dockerfile.irisview`） | 真 Ubuntu 22.04 + Node 24.13.0 + 虚拟 X 屏，跑亮亮视野工具链 | 只在你要 `run` 模拟器 / 打包时 |

## 1. 起后端（一条命令）

```powershell
docker build -f deploy/Dockerfile.backend -t memorylens .
docker run -d --name memorylens -p 8765:8765 -v ml-data:/data memorylens
```

或者：

```powershell
cd deploy
docker compose up -d --build
```

打开 <http://127.0.0.1:8765> 就是控制台；`curl http://127.0.0.1:8765/api/health` 应该回
`{"ok": true, "threshold": 0.4, "people": 0}`。

- 数据卷 `ml-data` 挂在 `/data`：`people.json` + `photos/` + `meetings/` 都在里面，删容器不丢数据。
  想把本机现成的库直接挂进去，把 compose 里那行 `E:/ar-memory/data:/data` 的注释打开。
- 模型打在镜像里（`models/` 41MB），**运行时不联网**。
- 阈值走环境变量：`AR_MATCH_THRESHOLD`、`AR_VOICE_THRESHOLD`、`AR_VOICE_MARGIN`。
  资源上限：`AR_MAX_BODY_BYTES`、`AR_MEETING_MAX_SECONDS`、`AR_MEETING_MAX_CHUNKS`。

### 之后搬去云上

同一份镜像，差别只有三件事：

1. **改成 HTTPS**：眼镜端和网页都在公网，别明文传 wav。前面挂一层 Nginx/Caddy 就行。
2. **限制上传大小**：服务端已有 `AR_MAX_BODY_BYTES`（默认 16MB），Nginx 再给 `client_max_body_size 8m` 兜一层。
3. **加个鉴权**：至少给 `/api/*` 加个 `X-Token`，不然谁都能往你的人脉库里塞脸。
4. **关掉本地文件路径**：公网/反代部署设 `AR_ALLOW_LOCAL_FILES=0`（详见 `docs/09-安全审计.md`）。

## 2. 眼镜工具链（打包 / 跑模拟器）

```powershell
# 只做一次：构建工具链镜像（约 5 分钟，装 Ubuntu 22.04 的 GTK/GLES 依赖）
docker build -f deploy/Dockerfile.irisview -t memorylens-irisview .

# 打包
powershell -File tools\kit-docker.ps1 package

# 跑模拟器并截一张真镜片截图 → test/simulator.png
powershell -File tools\kit-docker.ps1 run
```

`tools\kit-docker.ps1` 会把开发包挂到 `/kit`、项目挂到 `/work`，再调
`/kit/sdk/fusionui-app-sdk/fusionui_sdk.py`。因为容器里是**真 22.04**，
`run` 的宿主预检（系统版本、GLES/pulse/mpg123、DISPLAY）一次过。

容器里的显示是 `Xvfb :99` + mesa 的软件光栅化（`LIBGL_ALWAYS_SOFTWARE=1`），
所以**没有窗口弹出来**，是截 `import -window root` 得到的图——这正是模拟器真实输出的画面。

## 踩过的坑

| 现象 | 原因 | 做法 |
|---|---|---|
| `apt-get update` 报 `Certificate verification failed` | 基础镜像还没装 CA，https 换源握不上手 | 换源用 **http**（`http://mirrors.aliyun.com/ubuntu`） |
| `Unable to locate package pulseaudio-utils` | 上一行导致索引没更新完 | 同上前提；索引好了就都在 |
| `docker pull ubuntu:22.04` 卡住 | Docker Hub 直连不通 | 用镜像站：`docker.m.daocloud.io/library/ubuntu:22.04` 再 `docker tag` |
| `Node.js must be exactly 24.13.0` | SDK 硬校验版本号与 POSIX 的 npm 布局 | 镜像里用官方 tar 包解到 `/opt/node` |
| `unsupported OS: ubuntu 24.04` | 宿主预检只认 22.04 / 26.04 | 容器就是 22.04 |
| `verify` 报 `debug-unsigned trust is not allowed` | `verify` 只认已签名包 | 自测用 `run --allow-debug-unsigned` |

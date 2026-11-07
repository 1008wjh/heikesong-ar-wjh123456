# 赛事 SDK / 插件 / 模拟器审查（2026-11-07）

> 目的：在选 ASR 方案、改眼镜端之前，先把赛事发的开发包、SDK 公开接口、可用"插件"能力、模拟器运行条件摸清楚。
> 结论先行：**平台不提供内置 ASR**；语音只给 16kHz PCM，转文字要应用自己接。文本输入有正式 API；没有摄像头 API。
> 相关文件：Kit `C:\Users\lenovo\Desktop\hacksong\IrisView-Developer-Kit-v1.0.0`（WSL 内 `/home/iris/IrisView/Developer-Kit`）、
> 平台文档 `C:\Users\lenovo\Desktop\hacksong\IrisView-Platform-Alpha-中英双语版-v3\平台开发文档\`、
> 赛题 `docs/leion-赛题.txt` / 赛题 PDF、启动脚本 `tools/open_sim.ps1`。

---

## 1. 结论摘要

1. **没有内置 ASR 插件**。官方输入文档原话："应用需要语音识别时，自行对接识别服务"。
   `requestVoiceInput({mode:"audio"})` 只返回规范化 PCM；运行时 JS 里虽有内部 `transcription` 模式，但公开类型（`sdk/public-api/os/input.d.ts`）只声明 `"audio"`，文档也没有入口，不能当平台能力用。
2. **音频规格正好匹配我们的切片**：16 kHz、单声道、s16le、20 ms/320 样本/640 字节一帧；单次会话最长 **60 秒**，64 帧缓冲。我们的首片 15 s、后续 55 s 切片就是按 60 s 上限设计的。
3. **文本输入有正式通道**：`takeTextInput()`（能力 `input.text`，Host 2）可拿 Client 输入框提交的一整段文本（≤4096 UTF-8 字节）。模拟器 Client 的"AI 助手"输入框就是它；`prompt-cards.fuiapp` 示例用了这条通道。它和我们现在的"短语键盘"是两条路，前者是真键盘、后者是眼镜端导航键。
4. **没有摄像头 API**。`docs/07` 的结论仍然成立；MemoryLens 用"声纹认人"而不是摄像头，是对平台限制的正确适配。
5. **SDK 是"白名单能力 + 三个模块"**：`@irisview/osx/os`、`@irisview/osx/os/input`、`@irisview/osx/fusionui`；能力必须在 `FusionUIApp.json` 里声明，新输入类能力要求 `minimumHostVersion >= 2`。
6. **模拟器必须走 WSL2 + WSLg**（Kit 里是 Linux x86-64 可执行文件，没有 Windows 原生版）。本机 WSLg 首次启动失败，`wsl --shutdown` 后恢复正常；恢复后 WSLg 同时提供 **RDPSink（输出）和 RDPSource（麦克风）**，真实语音验证从此有基础。

---

## 2. 赛题要求（从 PDF / `docs/leion-赛题.txt` 重新核对）

### 2.1 评分（100）
| 维度 | 分值 |
|---|---|
| ARUI 设计与交互 | 25 |
| 自动化与智能体执行 | 25 |
| 多模态协同 | 20 |
| 实时性与可靠性 | 10 |
| 场景价值与创新 | 10 |
| 交付与真实性 | 10 |

### 2.2 硬性验收点
- **五状态**：待命 / 处理中 / 待确认 / 完成 / 异常，可观察即可，不要求 5 个独立页面；
- **两次连续调整**：一次内容变化 + 一次重点或布局变化；
- **多模态**：语音和文本都能完成同一任务；至少一次输入方式切换、一次独立纠正、上下文保持；
- **语音自动转写本身不算第二种交互方式**（关键：文本必须是独立通道，不能只是 ASR 出的字）；
- **8 项验收用例 + 至少 10 次连续延迟测量**；
- 交付：2 页 PDF、≤120 秒视频、冻结包、**不得包含密钥**；
- 验收重点：新需求、内容修改、重点与布局、跨方式交互、歧义与确认、服务失败、连续请求旧结果作废、显示边界。

### 2.3 环境与接口
- 模拟器运行环境：Ubuntu 22.04/26.04 或 Windows 通过 **WSL2 + WSLg**，需完成音频配置；
- 接口范围：视口、字体、控件、数据格式、更新方式、错误反馈、日志；
- 明确"未在赛前资源包中开放的接口，不作为验收要求"——所以不需要猜隐藏能力。

---

## 3. 开发包 / SDK 实际内容

### 3.1 Kit 结构
| 目录 | 内容 |
|---|---|
| `bin/irisview` | 统一入口：`doctor/new/test/build/package/verify/run/state/deploy/uninstall/examples` |
| `sdk/public-api/` | TypeScript 类型声明（`os`、`os/input`、`fusionui` 控件） |
| `sdk/fusionui-app-package/` | 应用包格式、能力白名单、打包/注册 |
| `runtime/` | 内嵌 Node 24.13.0、渲染引擎、GL 桥、启动器、客户端 |
| `simulators/` | 模拟器 Linux x86-64 可执行文件（release/off 与 diagnostic/lite 两套） |
| `templates/minimal/` | 最小 FusionUI 应用模板 |
| `applications/` | 空目录（模板工具链不带示例应用） |

### 3.2 公开 API 模块
| 模块 | 能做什么 |
|---|---|
| `@irisview/osx/os` | `App.createApp` 生命周期、`Window`/`WindowProperty`、语言、渲染就绪、电量、显示电源/常亮、动画帧、游戏/媒体动作、远端媒体播放（PCM/MP3）、Dashboard 卡片 |
| `@irisview/osx/os/input` | 导航（up/down/left/right/activate/back）、`FocusGroup`、`takeTextInput`、`takeUserTextInput`（旧）、`getVoiceInputAvailability`、`requestVoiceInput` + 音频/文本事件 |
| `@irisview/osx/fusionui` | `Board`、`Text`、`Image`、`GifImage`、`Select`、`Switch`、Flex/Grid 布局、`ScrollView`、`Color`、`Font`、`TextAlign`、动画/过渡 |

### 3.3 能力白名单（`sdk/fusionui-app-package/app_package.py`）
`dashboard.provider`、`display.keep-awake`、`input.audio`、`input.game-controls`、`input.navigation`、`input.text`、`input.user-text`、`media.background`、`media.playback`、`spatial.selector`。
新输入类能力要求 `minimumHostVersion >= 2`。MemoryLens 当前声明：`display.keep-awake`、`input.audio`、`input.navigation`。

### 3.4 语音输入（`input.audio`）细节
- 请求前先 `getVoiceInputAvailability("audio")`，看 `supported/declared/canRequest/ready/reason`；
- 用户确认 + 系统授权期间状态 `pending`，开始给音频后 `recording`；
- 音频：16 kHz、单声道、s16le、20 ms 帧（320 样本 / 640 字节），64 帧缓冲，单次最长 60 s；
- 结束：`finish()` 正常收尾，或 `cancel()`；必须消费到 `end` 事件后再请求下一段；
- 失败原因包含 `permission-required/denied`、`device-unavailable`、`model-unavailable`、`overflow`、`time-limit` 等。
- **没有识别结果事件可用**（`transcription` 模式不在公开 API/文档里）。

### 3.5 文本输入（`input.text`）细节
- `takeTextInput()` 返回 `{source:"keyboard", text}`，一次一整段，≤4096 UTF-8 字节，不会截断；
- 需要 `input.text` 能力 + Host 2；与旧 `takeUserTextInput()` 分开；
- 语音结果不会混进这个队列——正好可以用来做"语音 vs 文本"的独立第二种通道；
- 模拟器 Client 的输入框（"AI 助手"标签旁）就是它；`prompt-cards.fuiapp` 是可参考的示例。

### 3.6 FusionUI 限制
- 单行 `Text` 实际能画的宽度有限（`docs/07` 实测约 110px），长文本要手动断行/滚动；
- 子控件要 `setIgnoreLayout(true)` 再手动定位；
- `versionCode` 必须递增，同版本不同内容会被拒装；
- 应用是**真 Node 24 进程**，可 `require("node:http")` / `fetch`，轮询后端没问题（我们的 700ms 轮询已验证）。

---

## 4. 模拟器运行事实

### 4.1 命令与参数
```bash
# WSL 内，Kit 根目录
bin/irisview run /path/to/app.fuiapp --allow-debug-unsigned \
  --display-mode mono|stereo \
  --audio-mode desktop|silent \
  --profile release|diagnostic \
  --port 28045
```
- 会同时出现 **Client**（应用管理/输入框）和 **Simulator**（`LLVISION Agent Platform - Mono/Stereo`）两个窗口；
- 运行期间保持终端；关闭 Client 结束整个会话；
- 默认会话端口 28045；日志/状态在 `~/IrisView-Developer-Kit-Data/commercial/<version>/shared/profiles/<profile>/logs/`。

### 4.2 WSLg 必须正常
本机首次启动时 `/mnt/wslg` 里没有 `wayland-0` / `X0` / `PulseServer`，`WSLGd` 日志是 `Connection refused`，模拟器起不来。
**解决：`wsl --shutdown` 后重新进入发行版**，WSLg 恢复，之后：
- `/mnt/wslg/runtime-dir/wayland-0`、`/mnt/wslg/.X11-unix/X0`、`/mnt/wslg/PulseServer` 都在；
- `pactl list short sources` 能看到 `RDPSource`（WSLg 麦克风），`RDPSink` 是输出；
- 模拟器窗口标题 `LLVISION Agent Platform - Mono`，应用能正常跑帧。

### 4.3 退出时的 `fb_register -19`
窗口关闭/会话断开时模拟器日志会出现 `ERROR: fb_register() failed: -19` + `system-error`，这是退出路径的报错，不代表启动失败。
启动失败的表现是：`ERROR: simulator did not open TCP port 28045 within 10s`。

### 4.4 一键脚本
`tools/open_sim.ps1`：
```powershell
.\tools\open_sim.ps1 -CheckOnly          # 只检查 WSL/Kit/WSLg/后端/网络
.\tools\open_sim.ps1 -StartBackend       # 起后端 + 模拟器（前台，Ctrl+C 结束）
.\tools\open_sim.ps1 -StartBackend -Background   # 后台
.\tools\open_sim.ps1 -RestartWsl -StartBackend  # WSLg 卡住时先重启 WSL
.\tools\open_sim.ps1 -Stop               # 结束 Client/Simulator
```
脚本会自动算 WSL→Windows 网关、设置 `MEMORYLENS_BACKEND`、透传 `AR_API_TOKEN`→`MEMORYLENS_TOKEN`，日志写到 `data/sim/`（已 gitignore）。

---

## 5. 对 MemoryLens 的直接结论

1. **ASR 只能自己接**：平台不提供，Tingwu / 本地 paraformer 都是"应用侧"选择；`input.audio` 的 60 秒上限和 20ms 帧与我们的切片完全兼容。
2. **文本通道建议升级**：现在用短语键盘（`input.navigation`）拼句子，能用但慢；可以加 `input.text`，在 Client 输入框直接打整句，同时保留短语键盘作为眼镜端兜底。这会更符合"语音/文本同一任务、切换、纠正"的验收点。
3. **"2 人 / 多人"平台不判断**：必须靠本地 diarization 的人数或 LLM 分类；Tingwu 只能给匿名说话人标签（0=自动 / 2=固定），不能直接给"这是谁"。
4. **无摄像头**：继续走声纹主线，不要为"多模态"去补摄像头。
5. **真实语音验证条件已具备**：WSLg `RDPSource` + 模拟器窗口 + 后端；下一步只需在模拟器里按一下、对着 Windows 麦克风说话，确认 `input.audio` 出 PCM 和后端卡片。

---

## 6. 待办（与交接文档对齐）

- [ ] 模拟器里真实说话 → 后端收到 16k PCM → 卡片（本轮已把窗口和 WSLg 音频修好，待跑）
- [ ] 决定 ASR 路线（本地 paraformer / 听悟 mock / 两者）
- [ ] 8 项验收用例 + 10 次延迟测量
- [ ] 冻结包：依赖锁定、无密钥、README/启动步骤
- [ ] 仍不自动 commit/push；`HANDOFF.md` 未跟踪，未改动

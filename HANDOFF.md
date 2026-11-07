# MemoryLens 交接文档：最近两版需求、实现状态与文件索引

> 生成时间：2026-11-07 22:30（Asia/Shanghai）
> 2026-11-08 更新：录音会话/30s 截断/Text 占位/字体闪烁已修，版本 **2.4.1 / 29**；
> 变更明细见 `docs/16-变更日志-2026-11-08.md`，模拟器一键启动见 `tools/open_sim.ps1`。
> 用途：换一个新的对话窗口时，把本文件路径发给 AI，即可恢复上下文，不必重新读整段历史。
> 仓库根目录（Windows）：`C:\Users\lenovo\Desktop\heikesong-ar-wjh123456`
> 当前分支：`main`，本地提交包含本轮修复（2026-11-08 已 commit；push 需要 GitHub 凭证）。
> 工作区应干净；`dist/memorylens.fuiapp`、`glasses/FusionUIApp.json` 已是 2.4.1 / 29 的打包产物。

---

## 0. 给新对话的开场提示（可直接复制）

```text
请先读取：
C:\Users\lenovo\Desktop\heikesong-ar-wjh123456\HANDOFF.md

然后：
1. 先看 git status / git log，不要重复 git pull、不要重复安装依赖；
2. 当前重点是“通义听悟 ASR 适配 + 模拟器里真实语音验证”；
3. 不要自动提交或 push，先说明计划再改；
4. 我已经选择通义听悟，不要改回其他 ASR；
5. 模拟器包在 dist/memorylens.fuiapp，版本 2.4.1 / versionCode 29。
```

---

## 1. 项目一句话

MemoryLens 是给亮亮视野（LEION）AR 眼镜做的“记忆 + 会议”智能体：

- 单人：声纹认人 → 抽要点 → 给下一步建议；
- 多人：15 秒切片分人 → 整段声纹比对 → 逐段转写 → 会议待办 → 返回归档；
- 前端：IrisView 眼镜 HUD + 短语键盘 + 五状态；
- 后端：Python stdlib HTTP + LangGraph 智能体 + 本地 CAM++/pyannote + 可选 LLM/ASR。

参赛信息：

- 赛事：JUNCTION CHINA 2026「智能穿戴」赛道；
- 主题：ARUI · 自动化 · 多模态；
- 评审以**模拟器实际运行**为主；
- 评分：ARUI 25 + 自动化 25 + 多模态 20 + 实时 10 + 场景 10 + 交付 10。

赛事原文/PDF：

- `C:\Users\lenovo\Desktop\heikesong-ar-wjh123456\JUNCTION CHINA 2026智能穿戴赛道亮亮视野LEION赛题(2).pdf`
- `C:\Users\lenovo\Desktop\heikesong-ar-wjh123456\docs\leion-赛题.txt`

赛事要点：

- 五状态：待命 / 处理中 / 待确认 / 完成 / 异常；
- 至少两次连续调整：内容变化 + 重点/布局变化；
- 语音 + 文本都能完成同一任务，至少一次输入方式切换、一次独立纠正、上下文保持；
- 语音自动转写本身不算第二种交互方式；
- 需要 8 项验收用例 + 至少 10 次连续延迟测量；
- 提交 2 页 PDF、≤120 秒视频、冻结包、无密钥。

---

## 2. 最近两版需求（重点）

### v13：LangGraph 多人节点设计

文档：`docs/13-LangGraph多人节点设计.md`

核心要求：

1. **唯一分叉**：第一次 15 秒切片判定“单人 / 多人”；
2. 单人走既有子图；多人进入多人识别窗口 / 多人 LangGraph 节点；
3. 设计完整节点流程与兜底试错；
4. 与赛事 PDF 对齐：五状态、ARUI、自动化、多模态、实时性、交付；
5. 文本输入：模拟器虚拟键盘 + AI 推荐按钮；
6. 本地代码降级为 fallback；数据库可复用；
7. 目标：多拿分，不做过度复杂。

v13 设计的多节点：

```text
ingest → route（15s 判定）
      → meeting_init → collect_window → vad → diarize
      → speaker_analyze（每个 spk 并行、整段音色一次 1:N）
      → asr_segments → timeline_merge → summary
      → arui → confirm → action_items → persist
```

兜底：

- 模型不可用 → 规则抽取；
- 分段失败 → 能量 VAD / 单说话人；
- 声纹库不可用 → 全标陌生人；
- 旧响应按 request_id 作废；
- 异常/超时/坏 JSON → 模板兜底。

### v14：简化版 + D1–D5 + 对话建议子图

文档：`docs/14-简化版LangGraph与对话建议设计.md`

D1–D5：

| 编号 | 决定 | 落地 |
|---|---|---|
| D1 | 15 秒后由**佩戴者主动确认** | 当前实现是“开录前先选单人/多人”，也是佩戴者确认；**“先录 15 秒→系统建议→再确认”尚未实现** |
| D2 | 先用 JSON，逻辑跑通再换 DB | 会议记录写 `data/meetings/agent_<session>/meta.json` |
| D3 | **虚拟短语键盘**，不做全键盘 | 3 列短语键 + 底部删除/发送/关闭 |
| D4 | 模型供应商保留接口 | DeepSeek/MiMo/规则兜底可切 |
| D5 | 依赖锁定先不管 | 冻结包阶段要补 |

v14 新增的核心：**对话建议子图**。

输入：`who_context`（这是谁）+ `scene_analyze`（单人/会议、话题、风险）。

输出：

```json
{
  "who": {"name": "", "relation": "", "last_met": ""},
  "attitude": {"recommended": "warm_direct", "why": "", "avoid": []},
  "questions": [{"text": "", "why": "", "tone": ""}],
  "replies": [{"trigger": "如果对方说…", "text": "…"}]
}
```

对应实现：

- `src/llm.py` 的 `advise()`；
- 单人第二片起在 `node_recommend` 里调用；
- 会议里说/点“给我建议”走 `meeting_advice`；
- 没 Key 时用模板兜底。

v14 的 MVP 范围：

- 做：佩戴者确认、多人分人/摘要/详情/待办、单人识别+建议、短语键盘、JSON、五状态、旧结果作废、异常降级；
- 不做：SQLite、中文全键盘/IME、流式 diarization、厂商 ASR 原生适配、依赖锁定（当时）、音频情绪识别。

### 最近新增的决定（在 v14 之后）

1. **赛事标准已读**：按 PDF 对齐，目标分数；
2. **ASR 选择：通义听悟**（用户已决定，不要再推荐别家作为主方案）；
3. 想看到**模拟器实际运行效果**；
4. 先检查代码鲁棒性，再确认模拟器能否跑、能否识别语音；
5. 不要自动截图刷屏；要么后台跑给用户看，要么给详细教程。

---

## 3. 当前已实现的功能

### 后端

| 功能 | 文件 | 状态 |
|---|---|---|
| LangGraph 单人节点 | `src/agent.py` | ✅ 已实现 |
| LangGraph 多人 `meeting` 节点 | `src/agent.py` | ✅ 已实现（本次新增） |
| 15s 首片 / 55s 后续切片 | `glasses/src/index.js` | ✅ |
| 多人分人 + 整段音色声纹 | `src/diarize.py` 的 `per_speaker=True` | ✅ 已测 29 项 |
| 逐段 ASR | `src/agent.py` + `src/llm.py` | ✅ 代码链路；真实文字需模型/Key |
| 待办抽取 | `src/agent.py` + `src/llm.analyze` | ✅ LLM + 规则兜底 |
| 会议归档 JSON | `src/agent.py: archive_meeting` | ✅ |
| 对话建议 who/attitude/questions/replies | `src/llm.py: advise` | ✅ 代码 + 模板兜底 |
| 鉴权/TLS/CSRF/资源上限 | `src/server.py` | ✅ 17 + 13 项测试 |
| 旧 v1 会议接口 | `src/meeting.py` | ✅ 保留 |

### 眼镜端

| 功能 | 状态 |
|---|---|
| 先选单人/多人 | ✅ |
| 首片 15 秒 / 后续 55 秒 | ✅ |
| 在听、卡片、详情、返回归档 | ✅ |
| 短语键盘 | ✅ |
| AI 建议按钮（`↓`） | ✅ |
| token（`MEMORYLENS_TOKEN`） | ✅ 代码已加 |
| 会议归档提示 | ✅ |

### 控制台

- 01 人物数据；
- 02 单人对话分析；
- 03 多人会议（v1 接口：上传/分离/绑人/回放）。

### 已修/已做

- 鉴权、TLS、CSRF、路径穿越、本地文件限制；
- 隐私：真实数据脱敏/seed；
- 旧结果按 request_id 作废；
- 会议 ASR 逐段贴回；
- 短语键盘文本指令接同一 session；
- 键盘打开时轮询不再覆盖界面。

---

## 4. 实测结果

最近一次全量回归（临时 seed 数据，`AR_LLM_STUB=1`）：

| 测试 | 结果 |
|---|---|
| `test/diarize_test.py` | **29 passed**（含 per_speaker 整段音色） |
| `test/agent_test.py` | **13 passed**（单人 + 多人文本 + 保存） |
| `test/meeting_api_test.py` | **55 passed** |
| `test/auth_test.py` | **17 passed** |
| `test/security_test.py` | **13 passed** |
| `test/api_test.py` | **14 passed** |
| `test/api_console.py` | **15 passed** |
| `test/voice_api_test.py` | **13 passed** |
| `test/converse_api_test.py` | **16 passed** |
| `test/glasses_sim.js` | **全部通过**（会议首片 3 人、归档） |

后端多人实测输出（`test/demo_meeting.wav`）：

```text
3 人 · 熟人 1 · 陌生 2
王姐 · 12s
陌生人 · 7s
陌生人 · 7s
待办: 下周给报价单
待办: 下周三前问一次
```

耗时约 4.2 秒 / 35.8 秒音频（TTS，CPU）。

---

## 5. 当前没完成 / 风险

| # | 事项 | 状态 | 说明 |
|---|---|---|---|
| 1 | **通义听悟原生适配** | ❌ 未实现 | 当前只支持本地 paraformer / OpenAI 兼容 HTTP；听悟需要 OSS + 异步任务 + 说话人映射 |
| 2 | **模拟器内真实语音端到端** | ❌ 未完全验证 | 模拟器能启动、能到“在听”、能出现麦克风授权面板；Xvfb 下音频注入未打通；不要宣称已验证 |
| 3 | 15s 自动建议 + 佩戴者确认 | ❌ 未实现 | 当前是开录前先选单人/多人（也是佩戴者确认） |
| 4 | 对话建议真实效果 | ⚠️ 部分 | 有 `llm.advise` + 模板；真实 who/态度/回答需配 Key 验证 |
| 5 | 真实 3 人录音验收 | ❌ 未做 | 需要真人 wav + labels，跑 `tools/eval_diarization.py` |
| 6 | 眼镜安装包 | ⚠️ 已打但未提交 | `dist/memorylens.fuiapp` 2.4.0/28 已生成；工作区未提交 |
| 7 | Docker 构建 | ❌ 未验证 | 当前 Docker Desktop 没开 |
| 8 | 浏览器 e2e（dom/e2e） | ❌ 未跑 | `test/dom_sim.js` / `test/e2e_console.js` |
| 9 | 依赖锁定 / 冻结包 | ❌ 未做 | 比赛提交前必须补 |
| 10 | push | ❌ 未 push | 本地领先 10 个提交 |

---

## 6. 所有需要的文件地址

### 仓库与源码

| 路径 | 作用 |
|---|---|
| `C:\Users\lenovo\Desktop\heikesong-ar-wjh123456` | 仓库根目录 |
| `src\server.py` | HTTP 服务、鉴权/TLS/安全、`/api/agent/*`、`/api/v1/*` |
| `src\agent.py` | LangGraph 单人 + 多人 `meeting` 节点、归档 |
| `src\llm.py` | ASR / 对话析出 / `recommend` / **`advise`** |
| `src\diarize.py` | pyannote 分段 + CAM++ 聚类 + **`per_speaker=True`** |
| `src\meeting.py` | v1 会议会话（上传/finalize/绑定/回放） |
| `src\voice.py` | CAM++ 声纹 |
| `src\store.py` | 人脉库 JSON |
| `src\cards.py` | 卡片渲染 |
| `src\face.py` | 人脸（电脑端调试） |
| `src\asr.py` | 厂商 ASR 预留适配层（未实现） |
| `web\app.js` / `web\index.html` / `web\style.css` | 控制台（01/02/03） |
| `glasses\src\index.js` | 眼镜端 FusionUI 应用 |
| `glasses\src\outbox.js` | 离线补传 |
| `glasses\FusionUIApp.json` | 版本 2.4.0 / versionCode 28 |
| `dist\memorylens.fuiapp` | 最新包（32,835 字节，2026-11-07 21:52） |
| `requirements.txt` | Python 依赖（含 langgraph） |
| `start.bat` | 一键启动（会检查 langgraph） |
| `env.local.bat.example` | Key/环境变量模板 |

### 模型与数据

| 路径 | 作用 |
|---|---|
| `models\campplus_zh_16k.onnx` | CAM++ 声纹 27MB |
| `models\diarization\pyannote-segmentation-3.0.onnx` | 分段 5.7MB |
| `models\yunet.onnx` / `models\arcface_mbf.onnx` | 人脸（调试） |
| `models\asr\paraformer-zh-small\` | **本地 ASR 模型，当前没有**；`tools/get_asr_model.ps1` 可下 |
| `data\people.json` | 演示人脉库（5 人） |
| `data\photos\` | 照片 |
| `data\meetings\` | 会议运行时数据 |
| `data\meetings\agent_<session>\meta.json` | 多人会议归档 |

### 文档

| 路径 | 作用 |
|---|---|
| `README.md` | 总览 |
| `使用说明.md` | 上手 |
| `开发者文档.md` | 架构 / API / 坑 |
| `docs\07-IrisView-工具包.md` | 开发包/模拟器 |
| `docs\08-多人识别.md` | 多人分离设计 |
| `docs\09-安全审计.md` | 安全审计 |
| `docs\10-问题清单.md` | 问题清单 |
| `docs\11-多人会议切片工作流.md` | 切片工作流设计 |
| `docs\12-赛事标准对照与改造清单.md` | 赛事差距/改法 |
| `docs\13-LangGraph多人节点设计.md` | **v13 需求** |
| `docs\14-简化版LangGraph与对话建议设计.md` | **v14 需求** |
| `docs\leion-赛题.txt` | 赛题文本 |
| `JUNCTION CHINA 2026智能穿戴赛道亮亮视野LEION赛题(2).pdf` | 赛题 PDF |

### 测试

| 路径 | 作用 |
|---|---|
| `test\agent_test.py` | 单人 + 多人文本 + 保存 |
| `test\diarize_test.py` | 29 项，含 per_speaker |
| `test\meeting_api_test.py` | 55 项 |
| `test\auth_test.py` | 17 项 |
| `test\security_test.py` | 13 项 |
| `test\api_test.py` / `test\api_console.py` / `test\voice_api_test.py` | 旧链路 |
| `test\converse_api_test.py` | 模块 02 |
| `test\glasses_sim.js` | 眼镜仿真（会议/键盘） |
| `test\demo_meeting.wav` | 3 人演示会议（35.8s，TTS） |
| `test\demo_meeting.labels.json` | 标注 |

### 工具

| 路径 | 作用 |
|---|---|
| `tools\seed_demo.py` | 造演示库 |
| `tools\make_demo_meeting.py` | 生成 3 人演示 wav + labels |
| `tools\try_agent_meeting.py` | **手动发 wav/文本给 `/api/agent/feed`** |
| `tools\eval_diarization.py` | 真实录音评估（人数/映射/准确率） |
| `tools\get_asr_model.ps1` | 下本地 paraformer |
| `tools\try_voice.py` / `tools\try_face.py` | 单条声纹/人脸 |
| `tools\kit-docker.ps1` | Docker 工具链（当前 Docker 未开） |

### WSL / 模拟器

| 路径/名称 | 作用 |
|---|---|
| WSL 发行版 `IrisView` | Ubuntu 22.04 |
| `/home/iris/IrisView/Developer-Kit` | WSL 内的开发包 |
| `C:\Users\lenovo\Desktop\hacksong\IrisView-Developer-Kit-v1.0.0` | Windows 侧开发包 |
| `/home/iris/IrisView/Developer-Kit/bin/irisview` | 打包/运行入口 |
| `/home/iris/work/memorylens` | WSL 内构建目录 |
| `/home/iris/work/memorylens.fuiapp` | WSL 内构建产物 |
| `/home/iris/IrisView-Developer-Kit-Data` | 模拟器数据/日志 |
| `dist\memorylens.fuiapp` | 已复制到 Windows 的最新包 |

---

## 7. 常用命令

### 7.1 起后端（不配 Key 也能看流程）

```powershell
cd C:\Users\lenovo\Desktop\heikesong-ar-wjh123456
python tools\seed_demo.py
$env:AR_LLM_STUB="1"
python src\server.py
```

### 7.2 不开模拟器，直接看识别

```powershell
python tools\try_agent_meeting.py test\voice\huihui_b.wav --mode single
python tools\try_agent_meeting.py test\demo_meeting.wav --archive
```

### 7.3 自动测试

```powershell
python test\agent_test.py
python test\diarize_test.py
python test\meeting_api_test.py
python test\auth_test.py
python test\security_test.py
node test\glasses_sim.js
```

### 7.4 查 WSL 里的 Windows 地址（PowerShell 不要用 `\$3`）

```powershell
wsl -d IrisView -e bash -lc 'ip route show default'
```

输出形如：

```text
default via 172.19.16.1 dev eth0
```

自动取：

```powershell
$line = (wsl -d IrisView -e bash -lc 'ip route show default' | Select-Object -First 1)
$hostIp = ($line -split '\s+')[2]
$hostIp
```

### 7.5 起可见模拟器

先让后端监听所有网卡：

```powershell
$env:AR_HOST="0.0.0.0"
$env:AR_LLM_STUB="1"
python src\server.py
```

再：

```powershell
$hostIp = "172.19.16.1"   # 换成你查到的
$backend = "http://${hostIp}:8765"

wsl -d IrisView -e env DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir "MEMORYLENS_BACKEND=$backend" bash -lc 'cd /home/iris/IrisView/Developer-Kit && bin/irisview run /mnt/c/Users/lenovo/Desktop/heikesong-ar-wjh123456/dist/memorylens.fuiapp --allow-debug-unsigned --display-mode mono --audio-mode desktop'
```

验证后端可达：

```powershell
wsl -d IrisView -e curl -s http://172.19.16.1:8765/api/health
```

### 7.6 重新打包（WSL 内，不依赖 Docker）

```powershell
wsl -d IrisView -e bash -lc "rm -rf /home/iris/work/memorylens && cp -r '/mnt/c/Users/lenovo/Desktop/heikesong-ar-wjh123456/glasses' /home/iris/work/memorylens && find /home/iris/work/memorylens -type d -exec chmod 755 {} + && find /home/iris/work/memorylens -type f -exec chmod 644 {} +"
```

```powershell
wsl -d IrisView -e bash -lc "cd /home/iris/IrisView/Developer-Kit && bin/irisview test /home/iris/work/memorylens"
wsl -d IrisView -e bash -lc "cd /home/iris/IrisView/Developer-Kit && bin/irisview build /home/iris/work/memorylens"
```

打包需要 Git revision：

```powershell
$rev = git -C C:\Users\lenovo\Desktop\heikesong-ar-wjh123456 rev-parse HEAD
wsl -d IrisView -e bash -lc "cd /home/iris/IrisView/Developer-Kit && bin/irisview package /home/iris/work/memorylens --output /home/iris/work/memorylens.fuiapp --debug-unsigned --source-revision $rev --source-state dirty"
```

复制回 Windows：

```powershell
wsl -d IrisView -e bash -lc "cp /home/iris/work/memorylens.fuiapp /mnt/c/Users/lenovo/Desktop/heikesong-ar-wjh123456/dist/memorylens.fuiapp"
```

---

## 8. 通义听悟接入（下一步重点）

用户已决定使用**通义听悟**。当前**未接**。

官方能力：

- 通义听悟：语音转写、离线说话人区分（2 人 / 多人）、全文摘要、章节、发言总结、待办、问答回顾；
- 输入一般走 **OSS 地址**；
- 文档：<https://help.aliyun.com/zh/tingwu/features>

可选替代（仅备选，不改变用户选择）：

- 阿里 Paraformer-v2：`diarization_enabled` + `speaker_count`，需公网 URL；
  <https://www.alibabacloud.com/help/en/model-studio/paraformer-recorded-speech-recognition-restful-api>
- 腾讯 `CreateRecTask` + `16k_zh_en_meeting` + `SpeakerDiarization=1`；
  <https://cloud.tencent.com/document/api/1093/37823>

接入需要：

1. 阿里云 AccessKey/听悟 API Key；
2. OSS bucket（音频上传、生成可访问 URL）；
3. 异步任务提交 + 轮询；
4. 听悟返回的说话人/时间戳，映射到本地 `person_id`（可用本地 CAM++ 对每个说话人重提一次）；
5. 失败兜底：听悟不可用 → 本地 paraformer 或规则；
6. 密钥只放 `env.local.bat` 或环境变量，**不要进仓库**。

---

## 9. 模拟器真实验证建议

已验证：

- 包能 build/package；
- 模拟器能加载包，HUD 显示 `MemoryLens / 按一下开始听`；
- 模式选择、短语键盘、`在听`、麦克风授权面板出现；
- 后端 wav 多人识别通过。

未验证：

- 模拟器麦克风真实说话 → 卡片；
- 通义听悟转写；
- 真机。

建议：

1. 先用 **可见 WSLg 窗口**（7.5），用户自己点，确认能不能看到窗口；
2. 若 WSLg 音频没有，先用 **`node test/glasses_sim.js`** 验证 HUD 逻辑；
3. 真实语音用 **Windows 麦克风** 或 **PulseAudio 虚拟源**；
4. 要自动化跑，建议写一个固定脚本（启动 Pulse null sink、播放 wav、点击授权），不要手工截图。

---

## 10. 当前 Git 状态

```text
HEAD: fb9b513 tools: 手动试多人/单人 LangGraph 节点
737030a feat(agent): 多人会议 LangGraph 节点 ... + 归档
5e6eee7 merge: 远端 LangGraph 智能体/短语键盘 + 本地安全与多人会议
```

未提交：

- `dist/memorylens.fuiapp`（新包 2.4.0/28）
- `glasses/FusionUIApp.json`（版本号 2.4.1 / 29）

**没有 push。**

新对话如果继续改代码，先决定是否提交这两个文件；不要直接 `git pull` 覆盖。

---

## 11. 下一步建议（按优先级）

1. **接听悟**：OSS 上传 + 异步任务 + 轮询 + 说话人映射；
2. **模拟器可见验证**：WSLg 窗口 + Windows 麦克风；
3. **15 秒自动建议 + 佩戴者确认**：把当前“先选模式”改成“先录 15 秒 → 建议 → 确认”；
4. **真实 3 人录音验收**：`tools/eval_diarization.py`，调 `AR_DIAR_MERGE_SIM`；
5. **8 项验收用例 + 10 次延迟**；
6. **冻结包**：依赖锁定、README、启动步骤、无密钥；
7. **提交/推送**：当前本地领先 10 个提交。

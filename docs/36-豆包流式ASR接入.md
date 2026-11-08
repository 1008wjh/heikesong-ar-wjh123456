# 豆包流式 ASR 接入方案（2.0）

> 目标：会议实时转写主路径走豆包流式语音识别 2.0；失败/超时回退本地 SenseVoice。
> 分人不交给普通流式 ASR：说话人仍由本地 CAM++ + 全局 AHC 负责。

## 1. 结论

可以，豆包流式 2.0 适合做实时转写主路径。但建议保持下面的边界：

```text
豆包流式 ASR  = 听写（文字 + 标点 + 时间戳）
本地 CAM++    = 声纹 embedding / 临时说话人
本地 AHC      = 停录后全局重新分人
本地 SenseVoice = 云端失败兜底
```

不要让豆包流式接口同时承担“分人 + 聚类”；普通流式 ASR 通常只回文字，分人仍要靠本地 embedding。

## 2. 需要的 API 信息

接入前需要确认/提供：

- **豆包语音 API Key**：控制台创建，请求头 `x-api-key`；新版接口不需要 AppID
- 模型：实时语音识别用 `bigmodel_async`，一句话识别用 `bigmodel_nostream`
- Resource ID / Endpoint（以官方 WebSocket 文档为准）

> 注意：火山 IAM 的 Access Key ID / Secret Access Key 是云资源管理密钥，
> **不是**豆包语音的 `x-api-key`，不能直接用于语音识别。
- 接口地址和协议（WebSocket 或 HTTP chunk）
- 是否支持：
  - 16k / 单声道 / PCM；
  - 中英混合；
  - 中间结果（interim）；
  - 标点；
  - 句子级时间戳；
  - 热词/替换词；
  - 说话人分离（如果有，可作为辅助，不替代 AHC）。
- 控制台是否已开通、并发/时长配额。

## 3. 推荐链路

### 实时

```text
眼镜采集 PCM
→ 每 200–500ms 发一小段到后端
→ 后端维护一条豆包流式会话
→ interim/final 文本按时间写回 meeting ring
→ 本地 CAM++ 对同一段音频打临时 speaker 标签
```

现在的 6–8 秒切片是为了兼容本地 ASR；如果要做真正的流式，客户端要改成更小的 chunk，否则还是会等 6–8 秒才出字。

### 停录后

```text
整场 PCM
→ 本地 CAM++ 全量 embedding
→ 全局 AHC 重新聚类
→ 合并短簇
→ 用最终 speaker 修正转写和总结
```

## 4. 配置（env.local.bat）

```bat
set "AR_ASR_PROVIDER=doubao_stream"
set "AR_DOUBAO_API_KEY=..."
set "AR_DOUBAO_MODEL=bigmodel_async"
set "AR_DOUBAO_ENDPOINT=wss://..."     REM 以控制台文档为准
set "AR_DOUBAO_RESOURCE_ID=..."        REM 以控制台文档为准
set "AR_DOUBAO_TIMEOUT=3"
set "AR_ASR_FALLBACK=local"
```

字段名最终按火山/豆包控制台文档调整。

## 5. 代码接入点

1. `src/llm.py` 增加 `transcribe` 的 `doubao_stream` provider；
2. 新建 `src/asr_doubao.py`：负责 WebSocket 建连、发送音频帧、收 interim/final、重连；
3. `meeting_service` 增加流式会话管理：
   - 每个 meeting 一条上游流；
   - 失败/超时 → 本地 SenseVoice 按段兜底；
   - 记录 `asr_provider / asr_ms / fallback / error`；
4. 客户端 `glasses/src/index.js` 把会议音频切片从 6–8 秒降到 200–500ms；
5. 保留 `/api/meeting/feed` 作为非流式兜底路径，避免云不可用时整个会议不可用。

## 6. 验收

- 同一段 30 秒会议音频：
  - 豆包流式输出与本地 SenseVoice 对比错字率；
  - 首字延迟 / 最终文本延迟；
  - 断网后是否自动回退本地；
- 分人：
  - 3 人 demo 仍为 3 人；
  - 同一人换语气不再新增发言人；
  - 停录后 AHC 合并结果与人工标注对比（DER）。
- 成本/隐私：
  - 音频上云需用户知情；
  - 记录每次调用时长/失败率。

## 7. 风险

- 流式协议、鉴权、二进制帧格式必须以官方文档为准，不能凭猜测写；
- 云端长连接断开需要重连和补发；
- 网络抖动会导致文字跳变，必须保留 final 结果，不要用 interim 覆盖已确认文本；
- 豆包接口若只回文字，不能解决“7 个发言人”问题，分人仍要做 AHC。

## 8. 当前状态

- 已接入 `bigmodel_async` + `x-api-key`（`src/asr_doubao.py`），已验证 Key 可用；
- `AR_ASR_PROVIDER=doubao` 时主路径走豆包，失败/超时回退本地 SenseVoice；
- 实测一段 6 秒会议音频：第一次冷启动约 12 秒，之后约 1.2 秒；文字准确率明显好于本地；
- 当前眼镜端仍是 6–8 秒切片，所以只是“每个切片用豆包”，还不是边说话边出字；
- 下一步：客户端改为 200–500ms 小片持续发送，后端维护一条连续 WebSocket，才能实现真正的实时字幕；
- 分人仍由本地 CAM++ + 全局 AHC 负责，豆包返回的 `speaker_id` 只作为辅助参考。

# MemoryLens 验收测试包（可提交）

这个目录是**可以直接提交/复现的测试材料**：8 项验收用例 + 10 次连续延迟。

## 怎么跑

1. 启动后端（项目根目录）：

   ```powershell
   start.bat
   ```

2. 跑测试（默认 demo 账号，跑完自动还原人物库）：

   ```powershell
   python run_selftest.py
   ```

   或者指定账号 / 地址 / 输出文件：

   ```powershell
   python run_selftest.py --base http://127.0.0.1:8765 --user demo --pass demo123 --out report.json
   ```

3. 控制台里也可以可视化跑：登录后进入「04 验收测试」→「开始测试」→「导出 JSON」。

## 内容

| 文件 | 说明 |
|---|---|
| `run_selftest.py` | 登录 → 调 `/api/selftest/run` → 保存 report.json / 打印汇总 |
| `expected.json` | 8 项用例的预期结果与延迟阈值（p50 ≤ 2000ms、p95 ≤ 3500ms） |
| `samples/huihui_b.wav` | 语音用例样例（王姐，TTS 合成） |
| `samples/silence.wav` | 服务失败用例样例（静音） |
| `samples/constructed_meeting.wav` | 双人会议样例（Huihui + Hazel，TTS 合成） |
| `report.json` | 最近一次实际运行结果（提交时一起带上） |

## 8 项用例

1. 新需求：新输入 → 处理 → 生成 ARUI
2. 内容修改：换条件（只保留待办 / 日期 / 显示全部）
3. 重点与布局：优先日期 + 精简行数
4. 跨方式交互：语音认出王姐 → 文本换成 Lena → 再语音仍 Lena
5. 歧义与确认：换成王 → 待确认；说全名才改
6. 服务失败：静音音频 → 明确提示（真实失败路径，非注入）
7. 连续请求：慢请求被新请求顶掉 → 返回过期
8. 显示边界：超长输入不溢出，详情行受控

> 说明：测试在服务端当前登录用户的数据上跑，开始前快照 `people.json`，结束后还原；
> 样例音频是 TTS 合成，不含真人录音。

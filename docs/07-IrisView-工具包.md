# IrisView 开发者工具包：是什么、怎么用、我们怎么接

工具包位置：`C:\Users\Lenovo\Downloads\IrisView-Developer-Kit-v1.0.0-LLVISION-v2`
（解压后根目录 `IrisView-Developer-Kit-v1.0.0/`，175 个文件、416MB）

---

## 一句话

这是亮亮视野那副 AR 眼镜的**应用开发套件**：你写一个 Node.js 小应用，用它的
FusionUI 画界面，它负责把画面投到镜片上。没眼镜也能开发——包里带一个
**眼镜模拟器**（一个窗口，模拟单眼/双眼画面），跑起来看到的就是镜片上的样子。

它**不是**手机 App SDK，也不是 Unity 插件。核心就三样东西：

| 目录 | 是什么 |
|---|---|
| `sdk/` | 命令行工具（`irisview`）、应用包格式、TypeScript 类型声明（`public-api/`） |
| `runtime/` | 真机/模拟器上跑的东西：`irisnode`（内嵌 Node 24）、渲染引擎、GL 桥、启动器 |
| `simulators/` | 眼镜模拟器可执行文件（Linux x86-64） |

---

## 应用长什么样

一个普通 Node 24 的 CommonJS 工程，入口 `src/index.js`，用两个模块：

```js
const { App, AppLifecycle, Window, WindowProperty } = require("@irisview/osx/os");
const { Board, Text, Color, Font, FusionRoot, TextAlign } = require("@irisview/osx/fusionui");
```

- `os`：应用生命周期、开窗口、读电量、保持屏幕常亮、拿键盘输入、拿语音
- `fusionui`：真正画东西的——`Board`（画布）、`Text`、`Image`、`GifImage`、
  `FlexLayout` / `GridLayout` / `ScrollView`、`Color` / `Font` / `TextAlign`

一个最小应用（官方模板）就是：开一个 0.64×0.48 米的窗口 → 拿 FusionRoot →
铺一块 640×480 的黑 Board → 在中间写一行字。

清单文件 `FusionUIApp.json` 决定它怎么被安装：

```json
{
  "id": "demo.memorylens",
  "displayName": { "en": "MemoryLens", "zh-CN": "记忆眼镜" },
  "capabilities": ["display.keep-awake"],
  "minimumHostVersion": 1,
  "role": "ordinary",
  "version": "1.0.0", "versionCode": 1
}
```

能申请的能力是白名单（`sdk/fusionui-app-package/app_package.py:46`）：
`dashboard.provider`、`display.keep-awake`、`input.audio`、`input.game-controls`、
`input.navigation`、`input.text`、`input.user-text`、`media.background`、
`media.playback`、`spatial.selector`。新的输入类能力要求 `minimumHostVersion >= 2`。

---

## 关键限制（决定了我们的架构）

**1. 没有摄像头 API。** 全仓搜 `camera` 只命中 Node 内嵌的 Unicode 表。
眼镜能拿到的输入只有：导航（上/下/左/右/确认/返回）、键盘文本（`takeTextInput`）、
语音（`requestVoiceInput({mode:"audio"})` → 16kHz 单声道 s16le PCM，20ms 一帧，单次最长 60s）。

→ 所以"抓拍"必须由手机或笔记本完成，眼镜只负责**显示**。这正好是我们已经做好的分工。

**2. 是个真 Node 进程。** 官方说"API 路由，不是沙箱"（`application-api-loader.js:80`），
所以 `require("node:http")` / 全局 `fetch` 都能用——我们要轮询后台，这条路是通的。

**3. 宿主平台要求死**：`doctor` 只认 **Ubuntu 22.04 或 26.04** + x86_64，
外加一堆图形库（libGLESv2、libgtk-3、libmpg123、libpulse、libatomic…）。
Windows 和 WSL 的 Ubuntu 24.04 都不在名单里。

---

## 命令（在 WSL/Linux 里跑）

```bash
export FUSIONUI_DEVELOPER_KIT_ROOT=/path/to/IrisView-Developer-Kit-v1.0.0
K=$FUSIONUI_DEVELOPER_KIT_ROOT/sdk/fusionui-app-sdk/fusionui_sdk.py

python3 $K doctor                       # 体检：系统/依赖/Node
python3 $K new  ~/ml --id demo.memorylens --name MemoryLens --name-zh 记忆眼镜
python3 $K test ~/ml                    # 静态检查 + 依赖合同校验
python3 $K build ~/ml                   # 输出 dist/（android + linux 两套）
python3 $K package ~/ml --output ~/memorylens.irisapp --debug-unsigned
python3 $K run ~/memorylens.irisapp --port 28045 --allow-debug-unsigned
python3 $K deploy ...                   # 推到真机（adb）
```

`run` 的常用参数：`--display-mode mono|stereo`（单眼/双眼）、
`--audio-mode desktop|silent`、`--profile release|diagnostic`。

**Node 必须正好 24.13.0**（不是 24.15，也不是 22）。SDK 还会去找
`<node>/../lib/node_modules/npm/bin/npm-cli.js`——这是 POSIX 布局，
Windows 版 Node 的目录结构不满足，所以 Windows 上直接跑不了。

---

## 本机实测结论（2026-11-06）

能跑通的：

```
test    → status: pass（3 个文件、2 个 JS）
build   → dist/（android + linux）
package → memorylens.irisapp（8513 字节，debug-unsigned）
run     → 卡在宿主预检
```

`run` 的报错很具体：

```
operatingSystem: {"id":"ubuntu","supported":["ubuntu 22.04","ubuntu 26.04"],"version":"24.04"}
dependencies:launcher: ["libmpg123.so.0 => not found","libpulse.so.0 => not found"]
dependencies:simulatorBinding: ["libGLESv2.so.2 => not found","libatomic.so.1 => not found"]
```

本机是 WSL Ubuntu 24.04、没有 sudo 密码、docker 拉 `ubuntu:22.04` 又拉不动，
所以**模拟器没能在本机跑起来**。应用逻辑另外验证过（见下）。

在你有 root 的 Ubuntu 22.04 上，装完依赖即可：

```bash
cd $FUSIONUI_DEVELOPER_KIT_ROOT
sudo ./bin/install-host-dependencies      # 装 GL/GTK/音频那批库
./bin/check-host                          # 应该全绿
python3 $K run ~/memorylens.irisapp --allow-debug-unsigned
```

WSL 用户如果不想动系统库，可以用 docker：

```bash
docker run --rm -it --device /dev/dri -v $PWD:/w ubuntu:22.04 bash
# 容器里 apt install 依赖 + 装 Node 24.13.0 + xvfb-run 跑 run
```

（本机 `docker pull` 超时，这条没验证。）

---

## 我们怎么接（已经写好了）

```
手机/笔记本浏览器                     眼镜（FusionUI 应用）
  抓拍 → POST /api/scan  ──┐         ┌── 每 700ms GET /api/latest
                           ▼         ▼
                     src/server.py（同一个进程）
                     识别 → 卡片 → _LAST 快照
```

- 后端新增 `GET /api/latest`：返回最近一次识别的 `{rev, faces, ms, at}`，
  每扫一次 `rev+1`。眼镜只在 `rev` 变化时重画，不会每帧抖。
- 眼镜端源码：`glasses/src/index.js`（约 200 行），布局是
  品牌行 / 状态行 / 姓名 40px / 身份 20px / 相似度 / 三条信息 / 页脚。
- 打包产物：`glasses/dist/memorylens.irisapp`。
- 连哪台机器由环境变量 `MEMORYLENS_BACKEND` 决定，默认 `http://127.0.0.1:8765`；
  真机演示时改成笔记本在热点里的 IP，例如 `http://192.168.43.7:8765`。

### 已验证（不是"应该能跑"）

`node test/glasses_sim.js` 把 `@irisview/osx/*` 换成记录文字的最小替身，
用真实后端跑一遍，断言 HUD 最终写出的文字：

```
ok  后端在线
ok  HUD 建好（品牌 + 状态 + 名字 + 身份 + 相似度 + 3 行 + 页脚）  控件数=9
ok  扫码成功  15ms
ok  HUD 出现名字「里奥」
ok  HUD 出现身份
ok  HUD 出现三条信息
ok  HUD 有相似度
ok  陌生人显示「陌生人」
ok  陌生人提示去录入
全部通过
```

`irisview test` / `build` / `package` 也都是真跑过的（见上）。

---

## 演示脚本（眼镜那一段）

1. 电脑上 `start.bat` 起服务（8765），手机/另一个浏览器打开 `http://<电脑IP>:8765`。
2. 眼镜端（模拟器或真机）启动 MemoryLens，镜片上先显示"等待手机抓拍…"。
3. 手机对着人抓拍 → 眼镜 **半秒内**浮出：姓名 / 身份 / 三条信息。
4. 换一个陌生人 → 眼镜显示"陌生人 · 不在人脉库"，手机端点「记住他」。
5. 再抓一次 → 同一副眼镜，同一个人，卡片变青色。

评委问"眼镜自己怎么拍照"——就答：**这一代眼镜没有摄像头，所以拍照在手机上，
眼镜负责在你不方便看手机的时候把答案递到你眼前。** 这是取巧，也是真实的产品约束。

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
python3 $K package ~/ml --output ~/memorylens.fuiapp --debug-unsigned
python3 $K run ~/memorylens.fuiapp --port 28045 --allow-debug-unsigned
python3 $K deploy ...                   # 推到真机（adb）
```

本项目里这些路径都写好了，直接：

```bash
wsl -d Ubuntu bash /mnt/e/ar-memory/tools/kit.sh test
wsl -d Ubuntu bash /mnt/e/ar-memory/tools/kit.sh build
wsl -d Ubuntu bash /mnt/e/ar-memory/tools/kit.sh package   # → dist/memorylens.fuiapp
```

> 包后缀是 `.fuiapp`；`versionCode` 每次发布必须递增（同码不同内容会被拒装），
> 我们现在是 `1.6.2 / versionCode 18`。`verify` 只认已签名的包，debug-unsigned 会被它拒绝，
> 这是正常的——自测用 `run --allow-debug-unsigned`。

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

## 我们怎么接（v2：声纹为主）

```
眼镜（FusionUI 应用）                      手机/笔记本浏览器
  按一下 → input.audio 采 16k PCM           抓拍 → POST /api/scan
      → 攒成 WAV → POST /api/voice/scan           │
                    │                             │
                    └──────────┬──────────────────┘
                               ▼
                      src/server.py（同一个进程）
                  voice.py / face.py → 卡片 → _LAST 快照
                               │
                    每 700ms GET /api/latest（rev 变了才重画）
```

- 后端新增 `GET /api/latest`：返回最近一次结果的 `{rev, faces, ms, at, kind}`，每次识别 `rev+1`。
- 新增 `POST /api/voice/scan`（PCM/wav → CAM++ → 余弦 + margin）与 `POST /api/voice/enroll`（绑声纹）。
- 眼镜端源码：`glasses/src/index.js`，HUD 是窄列布局：品牌行 / 状态行 / 姓名 / 身份 / 相似度 / 六行信息 / 页脚。
  之所以是窄列、每行不超过 6 个汉字，是因为**镜片上单个 Text 只画得出约 110px**（见下面"排版坑"）。
- 状态行会区分来源：`听到 · 53ms`（声纹）或 `看到 · 34ms`（人脸），评委一眼能看懂。
- 打包：`powershell -File tools/kit-docker.ps1 package`（容器里跑，不依赖 WSL）
  → `dist/memorylens.fuiapp`（2.0.0 / versionCode 22 / debug-unsigned，24944 B，
  sha256 `0c75f41ff8cd0b756b4fa22e6c21dcda0885a0fff03ea3a697b4a38cdba19d72`）。
- 信息多的时候**自动滚动**：一屏 6 行，超过就每 1.4s 往下滚 1 行，到底从头开始；
  页脚显示 `1/2` 这样的页码。进后台会自动停滚。
  两张对比图：`test/glasses_scroll_a.png`（第 1 屏）、`test/glasses_scroll_b.png`（滚了 3 行）。
- 真机镜片截图：`test/glasses_card.png`（简单卡片）、`test/glasses_detail.png`（按「确认」展开的详情页），
  都是模拟器 mono 模式，单色绿。
- 能力声明：`display.keep-awake` + `input.audio` + `input.navigation`，`minimumHostVersion: 2`。
- 连哪台机器由 `MEMORYLENS_BACKEND` 决定，默认 `http://127.0.0.1:8765`。

### 两档卡片与三个动作（v3）

平台只给 6 个离散动作，**没有双击/长按**（`takeNavigationAction()` 只有 up/down/left/right/activate/back），
所以"先看简单数据、再展开复杂数据"用一个三态状态机实现：

| 屏幕状态 | 按「确认」 | 按「返回」 |
|---|---|---|
| 空 | 开始录音 | — |
| 简单卡片 | `GET /api/card/<id>/full` → 详情页 | 清屏 |
| 详情页 | 收起，回简单卡片 | 清屏 |

- 简单数据（`simple`）跟着卡片一起下发；复杂数据（`complex`）按需拉，**没按就永远不出网**，顺带就是隐私闸门。
- JSON 是服务端摊平成一行行字的（`src/cards.py: flatten`），眼镜端不解析 JSON。
- 一屏 6 行、超出自动滚动；详情页复用同一套滚动组件，只是页脚变成 `确认 收起 · 返回 清屏 · 1/2`。
- 联调捷径：`MEMORYLENS_AUTO_DETAIL=1` 时认出已知的人会自动翻到详情页，方便截图。

### 排版坑（都是实跑踩出来的）

1. **单个 Text 只画 ~110px**：超过就硬切，跟控件宽度、字号、`setWidth/setMaxWidth`、
   `LONG_DOT_END/LONG_WRAP` 都无关（设了反而更糟）。所以 HUD 用窄列 + 手动按字宽断句。
2. **子控件必须 `setIgnoreLayout(true)`**，否则 `Board` 的布局会覆盖 `setPosition`。
3. **`versionCode` 不递增，客户端会继续跑旧包**（画面完全不变、sha 一样），每次改代码都要 bump。
4. 模拟器要抓 `0x200002` 这个子窗口；没有窗口管理器时 `windowraise` 无效，
   而装了 twm 反而会让 GTK 客户端卡在启动阶段 —— 所以最终用 `NO_TWM=1` 直接抓子窗口。
5. Windows 挂载进容器的文件是 0777，运行时要 0644 —— `deploy/sim-shot.sh` 会先拷到 `/opt/kit` 再改权限。


### 已验证（不是"应该能跑"）

`node test/glasses_sim.js` 把 `@irisview/osx/*` 换成记录文字的最小替身，**再塞一个假麦克风**
（把 `test/voice/*.wav` 拆成 640 字节一帧喂进去），用真实后端跑一遍：

```
ok  后端在线
ok  注册王姐声纹
ok  HUD 建好                    控件数=12
ok  录音中状态
ok  HUD 出现「王姐」            （huihui_b，SIM 0.94）
ok  HUD 出现身份
ok  HUD 出现两条信息
ok  状态是「听到」
ok  按一下展开详情
ok  详情页有内容
ok  再按一下回到简单卡片
ok  返回键清屏
ok  陌生声音显示「陌生人」       （zira，SIM 0.30）
ok  陌生声音提示录入
ok  人脸结果也能上屏（里奥）
ok  状态是「看到」
全部通过
```

> 注意：开发包现在放在 `E:\ar-memory\IrisView-Developer-Kit-v1.0.0-LLVISION-v2\...`
> （三个 `IrisView-Developer-Kit-v1.0.0` 嵌套那层），`tools/kit-docker.ps1` 会自动找到它，
> 也可以用 `-Kit` 或环境变量 `IRIS_KIT` 指定。

`irisview test` / `build` / `package` 也都是真跑过的（见上）。

---

## 演示脚本（眼镜那一段）

1. 电脑上 `start.bat` 起服务（8765）。
2. 控制台里给队友「录声纹」：编辑 → ◉ 录声纹（3 秒）→ 说"我是 XXX" → 保存。
3. 眼镜端（模拟器或真机）启动 MemoryLens，镜片显示"按一下就说 · 说完自动认人"。
4. 队友走过来打个招呼，你按一下 → **半秒内**镜片浮出：姓名 / 身份 / 三条信息。
5. 换个没录过的人说话 → 镜片显示"陌生的声音 / 对着控制台说一句就能记住"。
6. 现场给他录一句 → 再按一下 → 同一个人，卡片变青色。

评委问"眼镜怎么拍脸"——就答：**这一代眼镜没有摄像头，但有麦克风；我们让"认出熟人"
不依赖摄像头，靠声音就够了。真要拍照时，手机补一刀，结果还是回到同一块镜片。**

---

# MemoryLens

中文 | [English](README.md)

本项目使用 IrisView 应用脚手架。随包资源放在 `src/`。应用持久文件存于 `FUSIONUI_APP_DATA_DIR` 提供的私有目录，不从 Client 安装目录或用户 Home 推导路径。

应用代码使用生成的辅助函数：

```js
const fs = require("node:fs");
const { applicationDataPath } = require("./application-data");

fs.writeFileSync(applicationDataPath("settings.json"), "{}\n");
```

临时文件使用 `require("node:os").tmpdir()`，可能随时被清理。

开发要求 Node.js 24.13.0 与 npm 11.6.2。先运行 `npm ci --offline --ignore-scripts --no-audit --no-fund`，再执行 SDK 的 test、build、package、verify。生成的锁文件没有第三方依赖。开发者可自行配置 `package.json`、脚本和依赖；添加依赖后生成并保留锁文件供 `npm ci` 使用。 先执行自己的构建，将 TypeScript 和依赖库编译打包到 `src/index.js` 及必要资源， 保留 `src/XRManifest.xml`，再执行 SDK 命令。SDK 不安装依赖、不执行 npm scripts， 接收预构建工程时不强制要求锁文件。根目录 `node_modules` 不会被复制进应用包； 所需运行资源和第三方许可声明应放入 `src/`。

# MemoryLens

[中文](README.zh-CN.md) | English

This project uses the IrisView application scaffold. Keep packaged resources under `src/`. Store application-owned persistent files under the private directory provided by `FUSIONUI_APP_DATA_DIR`; never build a path from the Client installation directory or a user home directory.

Use the generated helper from application code:

```js
const fs = require("node:fs");
const { applicationDataPath } = require("./application-data");

fs.writeFileSync(applicationDataPath("settings.json"), "{}\n");
```

Temporary files belong under `require("node:os").tmpdir()` and may be removed at any time.

Development requires Node.js 24.13.0 and npm 11.6.2. Run `npm ci --offline --ignore-scripts --no-audit --no-fund`, then the SDK test, build, package and verify commands. The generated lock has zero third-party dependencies. You may customize `package.json`, scripts and dependencies. After adding dependencies, generate and keep your lockfile for `npm ci`. Run your own build before the SDK commands to compile TypeScript and bundle libraries into `src/index.js` and required resources; keep `src/XRManifest.xml`. SDK commands do not install dependencies or execute npm scripts, and do not require a lockfile for prebuilt projects. Root `node_modules` is not copied into the application; include required runtime resources and third-party license notices in `src/`.

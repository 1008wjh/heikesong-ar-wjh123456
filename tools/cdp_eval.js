/* 在页面里执行一段 JS（默认从 stdin 或 argv[2] 文件读），用于端到端验证真实摄像头路径 */
const http = require('http');
const fs = require('fs');

const PORT = Number(process.env.CDP_PORT || 9222);

function get(path) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path }, (r) => {
      let b = '';
      r.on('data', (d) => (b += d));
      r.on('end', () => res(b));
    }).on('error', rej);
  });
}

(async () => {
  const expr = process.argv[2] && fs.existsSync(process.argv[2])
    ? fs.readFileSync(process.argv[2], 'utf8')
    : process.argv[2];
  if (!expr) throw new Error('no expression');

  const list = JSON.parse(await get('/json/list'));
  const match = process.env.CDP_MATCH || '127.0.0.1:8765';
  const pages = list.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  const page = pages.find((t) => String(t.url || '').includes(match)) || pages[0];
  const ws = new WebSocket(page.webSocketDebuggerUrl);

  const out = await new Promise((res, rej) => {
    ws.onopen = () => ws.send(JSON.stringify({
      id: 1, method: 'Runtime.evaluate',
      params: { expression: `(async () => { ${expr} })()`, returnByValue: true, awaitPromise: true },
    }));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id === 1) {
        if (m.result.exceptionDetails) rej(new Error(JSON.stringify(m.result.exceptionDetails.exception || m.result.exceptionDetails)));
        else res(m.result.result.value);
      }
    };
    ws.onerror = () => rej(new Error('ws error'));
    setTimeout(() => rej(new Error('timeout')), 20000);
  });
  ws.close();
  console.log(typeof out === 'string' ? out : JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.error('eval failed:', e.message); process.exit(1); });

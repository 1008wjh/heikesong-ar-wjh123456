/* 用 CDP 直接问浏览器：结果面板里到底有几个卡片、在哪。
   用法：先启动带 --remote-debugging-port=9222 的 Edge，再 node tools/cdp_probe.js [选择器] */
const http = require('http');

const PORT = 9222;

function get(path) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path }, (r) => {
      let b = '';
      r.on('data', (d) => (b += d));
      r.on('end', () => res(b));
    }).on('error', rej);
  });
}

const EXPR = `(() => {
  const rs = [...document.querySelectorAll('.res')];
  const results = document.getElementById('results');
  return JSON.stringify({
    resCount: rs.length,
    resultsChildren: results ? results.children.length : -1,
    resultsRect: results ? results.getBoundingClientRect().toJSON() : null,
    lensRect: document.getElementById('lens').getBoundingClientRect().toJSON(),
    items: rs.map(r => ({
      cls: r.className,
      text: (r.innerText || '').replace(/\\n/g, ' | '),
      rect: r.getBoundingClientRect().toJSON(),
    })),
  });
})()`;

(async () => {
  const list = JSON.parse(await get('/json/list'));
  const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!page) throw new Error('no page target');

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  const out = await new Promise((res, rej) => {
    ws.onopen = () => ws.send(JSON.stringify({
      id: 1, method: 'Runtime.evaluate',
      params: { expression: EXPR, returnByValue: true, awaitPromise: true },
    }));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id === 1) {
        if (m.result && m.result.exceptionDetails) rej(new Error(JSON.stringify(m.result.exceptionDetails)));
        else res(m.result.result.value);
      }
    };
    ws.onerror = (e) => rej(new Error('ws error'));
    setTimeout(() => rej(new Error('timeout')), 8000);
  });
  ws.close();
  console.log(out);
  process.exit(0);
})().catch((e) => { console.error('probe failed:', e.message); process.exit(1); });

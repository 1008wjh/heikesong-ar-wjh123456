/* 通过 CDP 截图（合成器真实输出），用来区分"页面真有问题"和"headless 截图残影" */
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
  const out = process.argv[2] || 'shot.png';
  const list = JSON.parse(await get('/json/list'));
  const match = process.env.CDP_MATCH || '127.0.0.1:8765';
  const pages = list.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  const page = pages.find((t) => String(t.url || '').includes(match)) || pages[0];
  const ws = new WebSocket(page.webSocketDebuggerUrl);

  const shot = await new Promise((res, rej) => {
    ws.onopen = () => ws.send(JSON.stringify({
      id: 2, method: 'Page.captureScreenshot',
      params: { format: 'png', captureBeyondViewport: false },
    }));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id === 2) res(m.result.data);
    };
    ws.onerror = () => rej(new Error('ws error'));
    setTimeout(() => rej(new Error('timeout')), 10000);
  });
  ws.close();
  fs.writeFileSync(out, Buffer.from(shot, 'base64'));
  console.log('saved ' + out);
  process.exit(0);
})().catch((e) => { console.error(e.message); process.exit(1); });

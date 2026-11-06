/* 从浏览器缓存里把页面资源原样取回来（本地文件被写坏时的救命工具）
   用法：node tools/cdp_source.js http://127.0.0.1:8765/app.js out.js */
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
  const url = process.argv[2];
  const out = process.argv[3];
  const list = JSON.parse(await get('/json/list'));
  const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();

  const send = (method, params) => new Promise((res, rej) => {
    const my = ++id;
    pending.set(my, res);
    ws.send(JSON.stringify({ id: my, method, params }));
    setTimeout(() => pending.has(my) && rej(new Error('timeout ' + method)), 8000);
  });

  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    const r = pending.get(m.id);
    if (!r) return;
    pending.delete(m.id);
    m.error ? r(Promise.reject(new Error(m.error.message))) : r(m.result);
  };

  await new Promise((res) => (ws.onopen = res));
  await send('Page.enable').catch(() => {});
  const tree = await send('Page.getFrameTree');
  const frameId = tree.frameTree.frame.id;
  const r = await send('Page.getResourceContent', { frameId, url });
  const buf = r.base64Encoded ? Buffer.from(r.content, 'base64') : Buffer.from(r.content, 'utf8');
  fs.writeFileSync(out, buf);
  console.log('saved ' + out + ' (' + buf.length + ' bytes)');
  process.exit(0);
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });

/* 用 CDP 真的往输入框里敲键（走 keydown → char，能暴露"全局快捷键吃键"的问题）
   用法：node tools/cdp_type.js "#noteIn" "hello world" */
const http = require('http');

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
  const sel = process.argv[2];
  const text = process.argv[3] || '';
  const list = JSON.parse(await get('/json/list'));
  const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();

  const send = (method, params) => new Promise((res, rej) => {
    const my = ++id;
    pending.set(my, res);
    ws.send(JSON.stringify({ id: my, method, params }));
    setTimeout(() => { if (pending.has(my)) rej(new Error('timeout ' + method)); }, 8000);
  });

  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    const r = pending.get(m.id);
    if (!r) return;
    pending.delete(m.id);
    if (m.error) r(Promise.reject(new Error(m.error.message)));
    else r(m.result);
  };

  await new Promise((res) => (ws.onopen = res));

  // headless 里要先让页面成为前台目标，Input 事件才会被派发
  await send('Page.bringToFront').catch(() => {});
  await send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});

  // 先聚焦目标输入框
  await send('Runtime.evaluate', {
    expression: `(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return 'no-such-element'; e.focus(); return 'focused'; })()`,
    returnByValue: true,
  });

  for (const ch of text) {
    await send('Input.dispatchKeyEvent', {
      type: 'keyDown', text: ch, unmodifiedText: ch,
      key: ch === ' ' ? ' ' : ch,
      code: ch === ' ' ? 'Space' : 'Key' + ch.toUpperCase(),
      windowsVirtualKeyCode: ch === ' ' ? 32 : ch.toUpperCase().charCodeAt(0),
      nativeVirtualKeyCode: ch === ' ' ? 32 : ch.toUpperCase().charCodeAt(0),
    });
    await send('Input.dispatchKeyEvent', { type: 'char', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  }

  const out = await send('Runtime.evaluate', {
    expression: `JSON.stringify({
      value: document.querySelector(${JSON.stringify(sel)}).value,
      active: document.activeElement && document.activeElement.id,
      lensMsg: (document.getElementById('lensMsg')||{}).textContent,
      frozen: !!(window.state && state.frozen)
    })`,
    returnByValue: true,
  });
  console.log(out.result.value);
  ws.close();
  process.exit(0);
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });

/* 控制台 CRUD 的真实浏览器测试：新增（带图）→ 列表出现 → 编辑 → 删除。
   走的是页面里真实的点击和 fetch，不是直接调 API。
   用法：node test/e2e_console.js     （需要 Edge 已用 --remote-debugging-port=PORT 打开页面）
   环境变量 CDP_PORT（默认 9228）、CDP_URL（默认 http://127.0.0.1:8765/?demo=2） */
const http = require('http');

const PORT = Number(process.env.CDP_PORT || 9228);
let passed = 0, failed = 0;

function check(name, ok, extra = '') {
  if (ok) { passed++; console.log('  ok   ' + name + ' ' + extra); }
  else { failed++; console.log('  FAIL ' + name + ' ' + extra); }
}

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
  const list = JSON.parse(await get('/json/list'));
  const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let seq = 0;
  const waiting = new Map();

  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    const w = waiting.get(m.id);
    if (w) { waiting.delete(m.id); w(m); }
  };
  await new Promise((r) => (ws.onopen = r));

  async function evaluate(expr) {
    const id = ++seq;
    ws.send(JSON.stringify({
      id, method: 'Runtime.evaluate',
      params: { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true },
    }));
    const m = await new Promise((r) => waiting.set(id, r));
    if (m.result && m.result.exceptionDetails) {
      throw new Error(m.result.exceptionDetails.exception.description || 'eval failed');
    }
    return m.result.result.value;
  }

  // 0. 清掉上次跑剩的测试数据，保证可重复运行
  const cleaned = await evaluate(`
    const r = await (await fetch('/api/people')).json();
    const junk = r.people.filter((p) => p.name === '测试甲');
    for (const p of junk) {
      await fetch('/api/people/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: p.id }) });
    }
    await loadPeople();
    return { removed: junk.length, count: state.people.length };
  `);
  console.log(`  ..   清理上次数据 ${cleaned.removed} 条，现在 ${cleaned.count} 人`);

  // 1. 打开新增弹窗，塞一张图 + 填字段（图片走 fetch/FileReader，模拟上传）
  const opened = await evaluate(`
    document.querySelector('#btnAdd').click();
    const r = await fetch('/demo/demo1.jpg');
    const b = await r.blob();
    pendingImage = await new Promise((res) => {
      const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(b);
    });
    setEdPhoto(pendingImage);
    document.querySelector('#edName').value = '测试甲';
    document.querySelector('#edRole').value = '投资人 · 朋友的朋友';
    document.querySelector('#ed1').value = '答应下周看 demo';
    document.querySelector('#ed2').value = '喜欢喝冰美式';
    return { open: !document.querySelector('#editor').hidden, hasImg: !!document.querySelector('#edPhoto').src };
  `);
  check('新增弹窗打开', opened.open);
  check('照片预览生效', opened.hasImg);

  // 2. 点保存
  const saved = await evaluate(`
    document.querySelector('#edSave').click();
    const list = () => [...document.querySelectorAll('#people li.person')];
    for (let i = 0; i < 50; i++) {
      await new Promise((r) => setTimeout(r, 100));
      if (list().some((x) => x.querySelector('b').textContent === '测试甲')) break;
    }
    const items = list().map((li) => li.querySelector('b').textContent);
    const li = list().find((x) => x.querySelector('b').textContent === '测试甲');
    return {
      closed: document.querySelector('#editor').hidden,
      inList: items.includes('测试甲'),
      role: li && li.querySelector('.role').textContent,
      avatar: !!(li && li.querySelector('img.av').src),
      count: document.querySelector('#count').textContent,
    };
  `);
  check('保存后弹窗关闭', saved.closed);
  check('列表里出现', saved.inList, saved.count);
  check('身份显示正确', saved.role === '投资人 · 朋友的朋友');
  check('列表里有头像', saved.avatar);

  // 3. 编辑：改身份 + 改信息 1
  const edited = await evaluate(`
    const li = [...document.querySelectorAll('#people li.person')].find((x) => x.querySelector('b').textContent === '测试甲');
    li.click();
    document.querySelector('#edRole').value = '投资人';
    document.querySelector('#ed1').value = '改过的信息一';
    document.querySelector('#edSave').click();
    for (let i = 0; i < 50; i++) {
      await new Promise((r) => setTimeout(r, 100));
      const p = state.people.find((x) => x.name === '测试甲');
      if (p && p.role === '投资人') break;
    }
    const li2 = [...document.querySelectorAll('#people li.person')].find((x) => x.querySelector('b').textContent === '测试甲');
    const p = state.people.find((x) => x.name === '测试甲');
    return { role: li2 && li2.querySelector('.role').textContent, info: p && p.info[0], count: state.people.length };
  `);
  check('编辑身份生效', edited.role === '投资人');
  check('编辑信息生效', edited.info === '改过的信息一');

  // 4. 删除（confirm 在 headless 下默认返回 false，这里覆盖掉）
  const deleted = await evaluate(`
    window.confirm = () => true;
    const li = [...document.querySelectorAll('#people li.person')].find((x) => x.querySelector('b').textContent === '测试甲');
    li.querySelector('.acts button.ghost').click();
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 100));
      if (!state.people.some((x) => x.name === '测试甲')) break;
    }
    return { gone: !state.people.some((x) => x.name === '测试甲'), count: state.people.length };
  `);
  check('删除后从列表消失', deleted.gone, deleted.count + ' 人');

  console.log(`\n${passed} passed, ${failed} failed`);
  ws.close();
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e.message); process.exit(1); });

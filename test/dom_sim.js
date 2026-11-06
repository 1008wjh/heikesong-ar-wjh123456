/* 前端回归测试（极简 DOM 替身跑 web/app.js）
   守住三条：① 抓拍一次只识别一次，不做轮询（所以按钮不会被每帧重建）
             ② 陌生人卡片带「记住他」，点了出输入框
             ③ 入库后同一帧重识别 → 同一个人变青色 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ctxStub = new Proxy({}, {
  get: (t, k) => (k in t ? t[k] : () => {}),
  set: (t, k, v) => { t[k] = v; return true; },
});

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.parent = null;
    this.style = {};
    this._cls = '';
    this._text = '';
    this.disabled = false;
    this.hidden = false;
    this.onclick = null;
  }
  set className(v) { this._cls = v; }
  get className() { return this._cls; }
  set textContent(v) { this._text = v; this.children.forEach((c) => { c.parent = null; }); this.children = []; }
  get textContent() { return this._text; }
  set innerHTML(_v) { this.children.forEach((c) => { c.parent = null; }); this.children = []; }
  get innerHTML() { return ''; }
  get childNodes() { return this.children; }
  get classList() {
    const self = this;
    const has = (c) => self._cls.split(' ').includes(c);
    return {
      add(c) { if (!has(c)) self._cls = (self._cls + ' ' + c).trim(); },
      remove(c) { self._cls = self._cls.split(' ').filter((x) => x !== c).join(' '); },
      toggle(c, on) { const want = on === undefined ? !has(c) : !!on; if (want) this.add(c); else this.remove(c); },
    };
  }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChildren(...cs) { this.children.forEach((c) => { c.parent = null; }); this.children = []; cs.forEach((c) => this.appendChild(c)); }
  remove() {
    if (this.parent) {
      const i = this.parent.children.indexOf(this);
      if (i >= 0) this.parent.children.splice(i, 1);
      this.parent = null;
    }
  }
  get isConnected() { let p = this; while (p.parent) p = p.parent; return p === doc.body; }
  get offsetHeight() { return 92; }
  getBoundingClientRect() { return { width: 800, height: 500 }; }
  find(fn) {
    for (const c of this.children) {
      if (fn(c)) return c;
      const r = c.find(fn);
      if (r) return r;
    }
    return null;
  }
  querySelector(sel) {
    const cls = sel.replace('.', '');
    return this.find((e) => e._cls.split(' ').includes(cls));
  }
  getContext() { return ctxStub; }
  toDataURL() { return 'data:image/jpeg;base64,STUB'; }
  focus() { this.focused = true; }
  addEventListener() {}
}

const ids = ['video', 'overlay', 'lens', 'results', 'hint', 'btnCam', 'btnStop', 'btnShot',
  'btnRetake', 'fileIn', 'latency', 'liveState', 'people', 'count', 'thresholdTag',
  'detail', 'detailName', 'detailMeta', 'notes', 'noteIn', 'btnNote', 'lensMsg'];
const byId = {};
ids.forEach((id) => { byId[id] = new El('div'); byId[id].id = id; });

const doc = {
  body: new El('body'),
  createElement: (t) => new El(t),
  querySelector: (sel) => byId[sel.replace('#', '')] || new El('div'),
  addEventListener() {},
};
byId.results.parent = doc.body;
byId.video.videoWidth = 640;
byId.video.videoHeight = 480;

/* 后端替身：第一次扫描是陌生人，录入之后再扫就是同一个人 */
let known = false;
let scanCalls = 0;
const facesNow = () => (known
  ? [{ status: 'known', name: '张三', lines: ['队友 · 产品', '上次见面 3 天前'], similarity: 0.93, box: [120, 100, 160, 160] }]
  : [{ status: 'unknown', name: '陌生人', lines: ['不在你的人脉库里', '按「记住他」录入一张脸'], similarity: -1, box: [120, 100, 160, 160] }]);

const sandbox = {
  document: doc,
  window: { addEventListener() {} },
  location: { search: '' },
  navigator: { mediaDevices: {} },
  alert: (m) => { throw new Error('alert: ' + m); },
  console,
  setTimeout,
  clearTimeout,
  Image: class {
    constructor() { this.width = 640; this.height = 480; this.naturalWidth = 640; this.naturalHeight = 480; }
    set src(_v) { setTimeout(() => this.onload && this.onload(), 0); }
    decode() { return Promise.resolve(); }
  },
  fetch: async (url) => {
    if (url.includes('/api/people')) {
      return { json: async () => ({ people: known ? [{ id: 'p1', name: '张三', embedding_count: 1 }] : [], threshold: 0.4 }) };
    }
    if (url.includes('/api/enroll')) {
      known = true;
      return { json: async () => ({ ok: true, id: 'p1' }) };
    }
    if (url.includes('/api/scan')) {
      scanCalls++;
      return { json: async () => ({ ok: true, ms: 41, size: [640, 480], faces: facesNow() }) };
    }
    return { json: async () => ({ ok: false, error: 'unexpected ' + url }) };
  },
};
sandbox.globalThis = sandbox;

const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'app.js' });

let pass = 0, fail = 0;
const ok = (cond, msg) => {
  if (cond) { pass++; console.log('  ok   ' + msg); }
  else { fail++; console.log('  FAIL ' + msg); }
};
const resList = () => byId.results.children;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  await sandbox.showShot('data:image/jpeg;base64,FROZEN');
  await sleep(20);

  ok(scanCalls === 1, '抓拍一次只请求一次识别（无轮询 → 不会每帧重建按钮）');
  ok(resList().length === 1, '结果面板出现 1 条');
  const res = resList()[0];
  ok(res._cls.includes('unknown'), '陌生人用琥珀色样式');
  ok(res.querySelector('.numbadge').textContent === '1', '卡片编号 1，和画面上的框对应');
  ok(!!res.querySelector('.act'), '带「记住他」按钮');

  const btn = res.querySelector('.act').children[0];
  btn.onclick();
  const act = res.querySelector('.act');
  ok(act.children.length === 3, '点「记住他」：出现 输入框/保存/取消');
  ok(act.children[0].tag !== 'button', '第一格是输入框');

  act.children[0].textContent = '张三';
  act.children[0].value = '张三';
  await act.children[1].onclick();
  await sleep(20);

  ok(scanCalls === 2, '入库后对同一帧重识别一次');
  ok(resList()[0]._cls.includes('known'), '同一个人立刻变青色（闭环走通）');
  ok(resList()[0].querySelector('.nm').textContent === '张三', '卡片显示名字');

  sandbox.retake();
  ok(resList().length === 0, '重拍：结果面板清空');

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();

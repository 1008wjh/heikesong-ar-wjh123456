/* 控制台前端回归（极简 DOM 替身跑 web/app.js）
   守四条：① 列表按人名渲染 ② 点开某人表单被正确回填
          ③ 保存时提交的 JSON 里有 id/name/info/simple ④ 坏 JSON 被拦住 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.parentNode = null;
    this.value = '';
    this.hidden = false;
    this.disabled = false;
    this.files = [];
    this._cls = '';
    this._text = '';
    this.onclick = null;
    this.oninput = null;
    this.onchange = null;
  }
  set className(v) { this._cls = v; }
  get className() { return this._cls; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text; }
  set innerHTML(_v) { this.children = []; }
  get innerHTML() { return ''; }
  get classList() {
    const self = this;
    const has = (c) => self._cls.split(/\s+/).includes(c);
    return {
      add(c) { if (!has(c)) self._cls = (self._cls + ' ' + c).trim(); },
      remove(c) { self._cls = self._cls.split(/\s+/).filter((x) => x !== c).join(' '); },
      toggle(c, on) { const want = on === undefined ? !has(c) : !!on; if (want) this.add(c); else this.remove(c); },
    };
  }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  append(...cs) {
    cs.forEach((c) => {
      if (typeof c === 'string') { const t = new El('#text'); t.textContent = c; this.appendChild(t); }
      else this.appendChild(c);
    });
  }
  focus() { this.focused = true; }
}

const ids = ['build', 'count', 'thresholdTag', 'search', 'btnAdd', 'btnAdd2', 'people', 'empty',
  'editor', 'edTitle', 'edMeta', 'edName', 'edRole', 'ed1', 'ed2', 'ed3', 'edSimple', 'edComplex',
  'edPhoto', 'edPhotoHint', 'edVoice', 'edVoiceHint', 'edDelete', 'edCancel', 'edSave', 'edFile', 'toast'];
const byId = {};
ids.forEach((id) => { byId[id] = new El(id === 'people' ? 'ul' : 'div'); byId[id].id = id; });

const doc = {
  querySelector: (sel) => byId[sel.replace('#', '')] || null,
  querySelectorAll: () => [],
  createElement: (t) => new El(t),
  addEventListener() {},
};

const people = [
  { id: 'p1', name: '里奥', role: '球王 · 老友', info: ['答应送他球衣', '住在迈阿密'],
    photo: 'photos/p1.jpg', voice_count: 1, embedding_count: 1, meet_count: 2,
    simple: { '身份': '队友' }, complex: { '家庭': { '宠物': '一只猫' } } },
  { id: 'p2', name: '王姐', role: '产品经理', info: [], photo: '', voice_count: 0, embedding_count: 0 },
];

let savedBody = null;
const fetchStub = async (url, opt) => {
  if (url === '/api/people') return { json: async () => ({ people, threshold: 0.4 }) };
  if (url === '/api/people/save') {
    savedBody = JSON.parse(opt.body);
    return { json: async () => ({ ok: true, person: { id: 'p1' }, faces: 1 }) };
  }
  return { json: async () => ({ ok: false, error: 'unexpected ' + url }) };
};

const sandbox = {
  document: doc,
  window: { addEventListener() {} },
  navigator: {},
  console,
  fetch: fetchStub,
  setTimeout,
  clearTimeout,
  JSON,
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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  await sleep(20);

  ok(byId.people.children.length === 2, '列表渲染出 2 个人');
  const first = byId.people.children[0];
  ok(first.className.includes('person'), '列表项带 .person 样式');
  const who = first.children.find((c) => c.className === 'who');
  ok(who && who.children[0].textContent === '里奥', '第一行是里奥');
  ok(byId.count.textContent.includes('2'), '右上角人数正确');

  sandbox.openEditor('p1');
  ok(byId.editor.hidden === false, '打开编辑器');
  ok(byId.empty.hidden === true, '空状态收起');
  ok(byId.edName.value === '里奥', '名字回填');
  ok(byId.ed1.value === '答应送他球衣', '信息 1 回填');
  ok(byId.edSimple.value.includes('"身份"'), '简单数据回填成 JSON 文本');
  ok(byId.edDelete.hidden === false, '编辑时出现删除按钮');

  byId.edName.value = '里奥2';
  byId.edSimple.value = '{"身份":"队友","上次见面":"上周"}';
  byId.edComplex.value = '';
  await sandbox.savePerson();
  ok(!!savedBody, '保存时发出了请求');
  ok(savedBody.id === 'p1' && savedBody.name === '里奥2', '提交了 id 与改后的名字');
  ok(savedBody.simple && savedBody.simple['身份'] === '队友', '简单数据被解析成对象提交');
  ok(!('complex' in savedBody), '复杂数据留空时不提交');
  ok(byId.editor.hidden === true, '保存后回到空状态');

  sandbox.openEditor(null);
  byId.edName.value = '新人';
  byId.edSimple.value = '{oops';
  savedBody = null;
  await sandbox.savePerson();
  ok(savedBody === null, '坏 JSON 不发请求');
  ok(byId.toast.hidden === false && byId.toast.className.includes('bad'), '坏 JSON 弹出错误提示');

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();

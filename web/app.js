/* MemoryLens 前端
   流程：开摄像头只做预览（不识别、不弹提示）→ 用户按「抓拍」→ 一帧送后端
        → 画面冻结、画框标号 → 结果固定显示在左下角面板（绝不跟脸移动）
   所以按钮永远停在原地，不会被下一帧重建或推着跑。 */

const $ = (s) => document.querySelector(s);

// 改代码就 +1，页脚会显示。看到 build 不是最新的，说明浏览器还在跑旧脚本。
const BUILD = 10;
const buildEl = $('#build');
if (buildEl) buildEl.textContent = 'build ' + BUILD;

const video = $('#video');
const overlay = $('#overlay');
const ctx = overlay.getContext('2d');
const lens = $('#lens');
const results = $('#results');
const hint = $('#hint');
const btnCam = $('#btnCam');
const btnStop = $('#btnStop');
const btnShot = $('#btnShot');
const btnRetake = $('#btnRetake');
const fileIn = $('#fileIn');

const state = {
  live: false,
  busy: false,
  frozen: null,   // 抓拍下来的那一帧（dataURL）
  img: null,      // 同一帧的 Image 对象
  faces: [],
  size: null,
  detailId: null,
  people: [],
};

let enrollCard = null;   // 当前处于"录入中"的卡片（全局最多一张）

/* ---------------- 小工具 ---------------- */

function el(cls, text) {
  const d = document.createElement('div');
  if (cls) d.className = cls;
  if (text !== undefined) d.textContent = text;
  return d;
}

function mkBtn(text, fn) {
  const b = document.createElement('button');
  b.textContent = text;
  if (fn) b.onclick = fn;
  return b;
}

function post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((x) => x.json());
}

function loadImg(url) {
  return new Promise((res, rej) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = () => rej(new Error('图片解码失败'));
    img.src = url;
  });
}

/* ---------------- 摄像头：只预览，不识别 ---------------- */

async function startCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    hint.classList.add('gone');
    state.live = true;
    clearOverlay();
    setMode('live');
    fitCanvas();
  } catch (e) {
    alert('打不开摄像头：' + e.message + '，可以点「用照片测试」。');
  }
}

function stopCamera() {
  state.live = false;
  const s = video.srcObject;
  if (s) s.getTracks().forEach((t) => t.stop());
  video.srcObject = null;
  state.frozen = null;
  state.img = null;
  state.faces = [];
  state.size = null;
  results.innerHTML = '';
  results.classList.remove('show');
  clearOverlay();
  setMode('idle');
}

function setMode(mode) {
  const idle = mode === 'idle';
  const frozen = mode === 'frozen';
  btnCam.disabled = !idle;
  btnStop.disabled = idle;
  btnShot.hidden = frozen;
  btnShot.disabled = !state.live;
  btnRetake.hidden = !frozen;
  $('#liveState').textContent = idle ? '● 待机' : frozen ? '● 已抓拍' : '● 预览中';
  $('#liveState').classList.toggle('off', idle);
  $('#lensMsg').textContent = idle
    ? '先预览，按「抓拍」才识别'
    : frozen
      ? '这是刚才那一帧 · 按「重拍」可继续'
      : '画面在动，按「抓拍」拍一张';
  hint.classList.toggle('gone', !idle);
}

/* ---------------- 抓拍 ---------------- */

const grab = document.createElement('canvas');
const gctx = grab.getContext('2d');

function frameDataUrl(maxW = 960) {
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw) return null;
  const scale = Math.min(1, maxW / vw);
  grab.width = Math.round(vw * scale);
  grab.height = Math.round(vh * scale);
  gctx.drawImage(video, 0, 0, grab.width, grab.height);
  return { url: grab.toDataURL('image/jpeg', 0.85), w: grab.width, h: grab.height };
}

async function capture() {
  if (!state.live || state.busy) return;
  const f = frameDataUrl(960);
  if (!f) return;
  await showShot(f.url);
}

async function showShot(url) {
  state.busy = true;
  state.frozen = url;
  lens.classList.add('snap');
  setTimeout(() => lens.classList.remove('snap'), 300);

  const img = await loadImg(url);
  state.img = img;
  state.faces = [];
  state.size = null;
  setMode('frozen');
  drawFrame(img, [], null);                 // 先冻住画面，用户立刻看到"拍到了"

  results.innerHTML = '';
  results.appendChild(el('res empty', '正在识别…'));
  results.classList.add('show');

  try {
    const r = await post('/api/scan', { image: url });
    if (!r.ok) throw new Error(r.error || '识别失败');
    $('#latency').textContent = r.ms + ' ms';
    state.faces = r.faces || [];
    state.size = r.size || null;
    drawFrame(img, state.faces, state.size);
    renderResults();
  } catch (e) {
    results.innerHTML = '';
    results.appendChild(el('res empty', '识别失败：' + e.message));
    $('#latency').textContent = '失败';
  } finally {
    state.busy = false;
  }
}

function retake() {
  if (state.live) {
    state.frozen = null;
    state.img = null;
    state.faces = [];
    state.size = null;
    results.innerHTML = '';
    results.classList.remove('show');
    clearOverlay();
    setMode('live');
  } else {
    stopCamera();
  }
}

/* ---------------- 画面：冻结帧 + 框 + 编号 ---------------- */

// 画布用 CSS 像素：画框坐标和页面位置共用一套坐标系。
// 注意必须每次绘制前对齐尺寸，否则 canvas 会保持默认 300x150 被 CSS 拉伸（数字会变得巨大）。
function fitCanvas() {
  const r = lens.getBoundingClientRect();
  const w = Math.round(r.width) || 800;
  const h = Math.round(r.height) || 600;
  if (overlay.width !== w || overlay.height !== h) {
    overlay.width = w;
    overlay.height = h;
  }
  return { w, h };
}

// 视频是 object-fit:cover，且显示时做了水平镜像
function coverMap(sw, sh, W, H) {
  const s = Math.max(W / sw, H / sh);
  return { s, ox: (W - sw * s) / 2, oy: (H - sh * s) / 2 };
}

function bracket(c, x, y, w, h, len) {
  c.beginPath();
  c.moveTo(x, y + len); c.lineTo(x, y); c.lineTo(x + len, y);
  c.moveTo(x + w - len, y); c.lineTo(x + w, y); c.lineTo(x + w, y + len);
  c.moveTo(x + w, y + h - len); c.lineTo(x + w, y + h); c.lineTo(x + w - len, y + h);
  c.moveTo(x + len, y + h); c.lineTo(x, y + h); c.lineTo(x, y + h - len);
  c.stroke();
}

function drawFrame(img, faces, size) {
  const { w: W, h: H } = fitCanvas();
  ctx.clearRect(0, 0, W, H);
  const sw = img.naturalWidth || img.width;
  const sh = img.naturalHeight || img.height;
  const { s, ox, oy } = coverMap(sw, sh, W, H);

  // 镜像绘制：和预览时的镜像画面完全一致，冻结的瞬间不会有跳变
  ctx.save();
  ctx.translate(W, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(img, ox, oy, sw * s, sh * s);
  ctx.restore();

  if (!faces || !size) return;

  faces.forEach((f, i) => {
    const [bx, by, bw, bh] = f.box;
    const x = W - (bx + bw) * s - ox;       // 镜像
    const y = by * s + oy;
    const w = bw * s, h = bh * s;
    const known = f.status === 'known';

    ctx.strokeStyle = known ? 'rgba(53,224,208,.9)' : 'rgba(255,180,84,.9)';
    ctx.lineWidth = 1.6;
    bracket(ctx, x, y, w, h, Math.min(w, h) * 0.22);

    // 编号：框上的小方块和左下角面板里的编号一一对应
    const cz = 20;
    const cx = Math.max(2, x);
    const cy = y - cz - 4 < 2 ? y + 4 : y - cz - 4;
    ctx.fillStyle = known ? 'rgba(53,224,208,.92)' : 'rgba(255,180,84,.92)';
    ctx.fillRect(cx, cy, cz, cz);
    ctx.fillStyle = '#04110f';
    ctx.font = '600 12px "Cascadia Code", Consolas, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(i + 1), cx + cz / 2, cy + cz / 2 + .5);
  });
}

function clearOverlay() {
  ctx.clearRect(0, 0, overlay.width, overlay.height);
}

/* ---------------- 结果面板（固定位置，一次渲染，不随帧刷新） ---------------- */

function renderResults() {
  results.innerHTML = '';
  if (!state.faces.length) {
    results.appendChild(el('res empty', '没看到脸，按「重拍」再拍一张'));
    results.classList.add('show');
    return;
  }
  state.faces.slice(0, 3).forEach((f, i) => results.appendChild(makeRes(f, i)));
  if (state.faces.length > 3) {
    results.appendChild(el('res more', '还有 ' + (state.faces.length - 3) + ' 张脸没显示'));
  }
  results.classList.add('show');
}

function makeRes(f, i) {
  const known = f.status === 'known';
  const d = el('res ' + (known ? 'known' : 'unknown'));
  d.__f = f;
  d.__i = i;

  const hd = el('hd');
  hd.appendChild(el('numbadge', String(i + 1)));
  hd.appendChild(el('nm', f.name));
  if (f.similarity >= 0) hd.appendChild(el('sim', 'SIM ' + f.similarity.toFixed(2)));
  d.appendChild(hd);

  if (f.role) d.appendChild(el('role', f.role));
  (f.lines || []).forEach((ln) => d.appendChild(el('ln', ln)));

  if (!known) {
    const act = el('act');
    act.appendChild(mkBtn('记住他', () => startEnroll(d, f, i)));
    d.appendChild(act);
  }
  return d;
}

/* ---------------- 录入：陌生人 → 入库 ---------------- */

function startEnroll(res, f, i) {
  // 同一时刻只允许一张卡片处于"录入中"，否则会出现两个「记住他」/两个输入框
  if (enrollCard && enrollCard !== res) resetEnroll(enrollCard);
  const act = res.querySelector('.act');
  if (!act || act.querySelector('input')) return;
  enrollCard = res;
  if (f) { res.__f = f; res.__i = i; }
  act.innerHTML = '';
  const input = document.createElement('input');
  input.placeholder = '他的名字';
  const ok = mkBtn('保存');
  const cancel = mkBtn('取消');
  act.append(input, ok, cancel);
  input.focus();

  const face = f || res.__f;
  cancel.onclick = () => resetEnroll(res);

  ok.onclick = async () => {
    const name = input.value.trim();
    if (!name) return input.focus();
    if (!state.frozen) return alert('画面没了，重拍一张');
    ok.disabled = true;
    const r = await post('/api/enroll', {
      name,
      image: state.frozen,
      box: [face.box[0] + face.box[2] / 2, face.box[1] + face.box[3] / 2],
    });
    if (!r.ok) {
      ok.disabled = false;
      return alert('录入失败：' + r.error);
    }
    await loadPeople();
    // 同一帧再识别一次：刚才的陌生人应该立刻变成青色
    await rescan();
  };
}

// 把某张卡片从"录入中"还原成「记住他」按钮
function resetEnroll(res) {
  if (enrollCard === res) enrollCard = null;
  if (!res || !res.isConnected) return;
  const act = res.querySelector('.act');
  if (!act) return;
  act.innerHTML = '';
  act.appendChild(mkBtn('记住他', () => startEnroll(res, res.__f, res.__i)));
}

async function rescan() {
  if (!state.frozen || !state.img) return;
  try {
    const r = await post('/api/scan', { image: state.frozen });
    if (!r.ok) return;
    $('#latency').textContent = r.ms + ' ms';
    state.faces = r.faces || [];
    state.size = r.size || null;
    drawFrame(state.img, state.faces, state.size);
    renderResults();
  } catch (_) { /* 静默：界面保持原样 */ }
}

/* ---------------- 控制台：人脉库增删改查 ---------------- */

async function loadPeople() {
  const r = await fetch('/api/people').then((x) => x.json());
  state.people = r.people || [];
  $('#count').textContent = `库中 ${state.people.length} 人`;
  $('#thresholdTag').textContent = '阈值 ' + r.threshold.toFixed(2);
  renderPeople();
}

function renderPeople() {
  const ul = $('#people');
  const q = ($('#search').value || '').trim().toLowerCase();
  const list = state.people.filter(
    (p) => !q || (p.name + ' ' + (p.role || '')).toLowerCase().includes(q)
  );
  ul.innerHTML = '';
  if (!list.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = state.people.length ? '没有匹配的人' : '还没有人，点「+ 新增」录一个';
    ul.appendChild(li);
    return;
  }
  list.forEach((p) => {
    const li = document.createElement('li');
    li.className = 'person';

    const av = document.createElement('img');
    av.className = 'av';
    av.alt = '';
    if (p.photo) av.src = '/api/photo?id=' + encodeURIComponent(p.id) + '&t=' + Date.now();
    else av.classList.add('ph');

    const who = document.createElement('div');
    who.className = 'who';
    const nm = document.createElement('b');
    nm.textContent = p.name;
    const rl = document.createElement('span');
    rl.className = 'role';
    rl.textContent = p.role || '未填身份';
    who.append(nm, rl);

    const acts = document.createElement('div');
    acts.className = 'acts';
    const ed = mkBtn('编辑', () => openEditor(p.id));
    ed.className = 'mini';
    const del = mkBtn('删', () => deletePerson(p));
    del.className = 'mini ghost';
    acts.append(ed, del);

    li.append(av, who, acts);
    li.onclick = (e) => { if (e.target.tagName !== 'BUTTON') openEditor(p.id); };
    ul.appendChild(li);
  });
}

/* ---------------- 新增 / 编辑弹窗 ---------------- */

let editing = null;      // null = 新增
let pendingImage = null; // 这次新选的照片（dataURL）

function openEditor(id) {
  editing = (id && state.people.find((p) => p.id === id)) || null;
  pendingImage = null;
  $('#edTitle').textContent = editing ? '编辑 · ' + editing.name : '新增人物';
  $('#edName').value = editing ? editing.name : '';
  $('#edRole').value = editing ? editing.role || '' : '';
  const info = (editing && editing.info) || [];
  ['#ed1', '#ed2', '#ed3'].forEach((s, i) => { $(s).value = info[i] || ''; });
  setEdPhoto(editing && editing.photo
    ? '/api/photo?id=' + encodeURIComponent(editing.id) + '&t=' + Date.now()
    : '');
  $('#edPhotoHint').textContent = '正脸、光线均匀、别戴帽子';
  $('#edDelete').hidden = !editing;
  $('#editor').hidden = false;
  setTimeout(() => $('#edName').focus(), 30);
}

const BLANK_IMG = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

function setEdPhoto(src) {
  const img = $('#edPhoto');
  if (src) {
    img.src = src;
    img.classList.remove('empty');
  } else {
    img.src = BLANK_IMG;          // 清空时给个透明占位，否则浏览器会画出"碎图"图标
    img.classList.add('empty');
  }
}

function closeEditor() {
  $('#editor').hidden = true;
  editing = null;
  pendingImage = null;
}

async function savePerson() {
  const name = $('#edName').value.trim();
  if (!name) { $('#edName').focus(); return alert('先填名字'); }
  if (!editing && !pendingImage) return alert('新增需要上传一张正脸照，否则认不出他');
  const body = {
    id: editing ? editing.id : '',
    name,
    role: $('#edRole').value.trim(),
    info: ['#ed1', '#ed2', '#ed3'].map((s) => $(s).value.trim()),
  };
  if (pendingImage) body.image = pendingImage;

  const btn = $('#edSave');
  btn.disabled = true;
  btn.textContent = '保存中…';
  try {
    const r = await post('/api/people/save', body);
    if (!r.ok) throw new Error(r.error || '保存失败');
    // 先解锁按钮再关弹窗：否则连点两次会点到一个 disabled 的按钮
    btn.disabled = false;
    btn.textContent = '保存';
    closeEditor();
    await loadPeople();
    if (state.frozen) await rescan();          // 画面还冻着的话，卡片立刻更新
    $('#lensMsg').textContent = '已保存：' + name +
      (r.faces > 1 ? `（图里 ${r.faces} 张脸，取了最大那张）` : '');
  } catch (e) {
    alert('保存失败：' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '保存';
  }
}

async function deletePerson(p) {
  if (!p) return;
  if (!confirm(`删除「${p.name}」？照片和底片会一起删掉。`)) return;
  const r = await post('/api/people/delete', { id: p.id });
  if (!r.ok) return alert('删除失败：' + r.error);
  if (editing && editing.id === p.id) closeEditor();
  await loadPeople();
  if (state.frozen) await rescan();
}

/* ---------------- 用照片测试 ---------------- */

function readAsDataURL(file) {
  return new Promise((res) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.readAsDataURL(file);
  });
}

fileIn.onchange = async () => {
  const file = fileIn.files[0];
  if (!file) return;
  const url = await readAsDataURL(file);
  hint.classList.add('gone');
  await showShot(url);
};

/* ---------------- 演示模式（没摄像头/断网的兜底） ---------------- */

async function demoMode(n) {
  const img = new Image();
  img.src = `/demo/demo${n}.jpg`;
  await img.decode();
  const c = document.createElement('canvas');
  c.width = Math.min(960, img.width);
  c.height = Math.round(img.height * (c.width / img.width));
  c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
  hint.classList.add('gone');
  await showShot(c.toDataURL('image/jpeg', 0.85));
  $('#lensMsg').textContent = '演示模式 · 照片回放';
}

const demoArg = location.search.match(/demo=(\d)/);
if (demoArg) demoMode(demoArg[1]);

/* ---------------- 绑定 ---------------- */

btnCam.onclick = startCamera;
btnStop.onclick = stopCamera;
btnShot.onclick = capture;
btnRetake.onclick = retake;

// 控制台
$('#btnAdd').onclick = () => openEditor(null);
$('#search').oninput = renderPeople;
$('#edClose').onclick = closeEditor;
$('#edCancel').onclick = closeEditor;
$('#edSave').onclick = savePerson;
$('#edDelete').onclick = () => deletePerson(editing);
$('#edFile').onchange = async () => {
  const f = $('#edFile').files[0];
  if (!f) return;
  pendingImage = await readAsDataURL(f);
  setEdPhoto(pendingImage);
  $('#edPhotoHint').textContent = '保存时会把这张脸存进人脉库';
  $('#edFile').value = '';
};

document.addEventListener('keydown', (e) => {
  if (e.isComposing) return;   // 输入法组字中，任何快捷键都不该抢
  if (!$('#editor').hidden) {
    if (e.key === 'Escape') closeEditor();
    return;                    // 弹窗打开时不触发抓拍/重拍
  }
  const t = e.target;
  const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  if (typing) return;
  if (e.code === 'Space') { e.preventDefault(); capture(); }
  if (e.key === 'r' || e.key === 'R') retake();
});

window.addEventListener('resize', () => {
  if (!state.frozen) return;
  drawFrame(state.img, state.faces, state.size);   // drawFrame 内部会重新对齐画布尺寸
});

loadPeople();

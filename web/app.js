/* MemoryLens 控制台（纯后台页面，不含摄像头）
   左侧人脉库列表，右侧编辑表单：照片 / 名字 / 身份 / 三条信息 / 两档 JSON / 声纹。
   后端接口：GET /api/people、POST /api/people/save、POST /api/people/delete、
             GET /api/photo?id=、POST /api/voice/enroll */

const $ = (s) => document.querySelector(s);

// 改代码就 +1，页脚会显示。看到 build 不是最新的，说明浏览器还在跑旧脚本。
const BUILD = 13;

const state = { people: [], threshold: 0.4 };

let editing = null;       // 正在编辑的人（null = 新增）
let pendingImage = null;  // 这次新选的照片（dataURL）
let pendingVoice = null;  // 这次新录的声纹（dataURL wav），保存时一起提交
let voiceRecording = false;
let voiceRec = null;

const VOICE_SECONDS = 3;
const BLANK_IMG = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

/* ---------------- 小工具 ---------------- */

function post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((x) => x.json());
}

let toastTimer = null;
function toast(msg, bad) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = msg;
  t.hidden = false;
  t.classList.toggle('bad', !!bad);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
}

function photoUrl(p) {
  return '/api/photo?id=' + encodeURIComponent(p.id) + '&t=' + (p.photo ? Date.now() : 0);
}

/* ---------------- 列表 ---------------- */

async function loadPeople() {
  const r = await fetch('/api/people').then((x) => x.json());
  state.people = r.people || [];
  state.threshold = r.threshold || 0.4;
  $('#count').textContent = `库中 ${state.people.length} 人`;
  $('#thresholdTag').textContent = '阈值 ' + Number(state.threshold).toFixed(2);
  renderPeople();
}

function renderPeople() {
  const ul = $('#people');
  const q = (($('#search') && $('#search').value) || '').trim().toLowerCase();
  const list = state.people.filter(
    (p) => !q || (p.name + ' ' + (p.role || '')).toLowerCase().includes(q)
  );
  ul.innerHTML = '';
  if (!list.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = state.people.length ? '没有匹配的人' : '还没有人，点「+ 新增人物」';
    ul.appendChild(li);
    return;
  }
  list.forEach((p) => {
    const li = document.createElement('li');
    li.className = 'person';
    if (editing && editing.id === p.id) li.classList.add('sel');

    const av = document.createElement('img');
    av.className = 'av';
    av.alt = '';
    if (p.photo) av.src = photoUrl(p);
    else av.classList.add('ph');

    const who = document.createElement('div');
    who.className = 'who';
    const nm = document.createElement('b');
    nm.textContent = p.name;
    const rl = document.createElement('span');
    rl.className = 'role';
    rl.textContent = p.role || '未填身份';
    if (p.voice_count > 0) {
      const vt = document.createElement('i');
      vt.className = 'voicetag';
      vt.textContent = '声';
      vt.title = '已录声纹，眼镜能听声认人';
      rl.append(' ', vt);
    }
    who.append(nm, rl);

    const acts = document.createElement('div');
    acts.className = 'acts';
    const ed = document.createElement('button');
    ed.className = 'mini';
    ed.textContent = '编辑';
    ed.onclick = (e) => { e.stopPropagation(); openEditor(p.id); };
    const del = document.createElement('button');
    del.className = 'mini ghost';
    del.textContent = '删';
    del.onclick = (e) => { e.stopPropagation(); deletePerson(p); };
    acts.append(ed, del);

    li.append(av, who, acts);
    li.onclick = () => openEditor(p.id);
    ul.appendChild(li);
  });
}

/* ---------------- 编辑器 ---------------- */

function jsonText(obj) {
  if (!obj || typeof obj !== 'object' || !Object.keys(obj).length) return '';
  return JSON.stringify(obj, null, 2);
}

function parseJsonField(sel) {
  const box = $(sel);
  const text = (box && box.value || '').trim();
  if (!text) return null;
  try {
    const val = JSON.parse(text);
    if (val && typeof val === 'object' && !Array.isArray(val)) return val;
    throw new Error('要是一个 JSON 对象，比如 {"身份":"队友"}');
  } catch (e) {
    const label = box && box.parentNode ? box.parentNode.textContent || '' : sel;
    throw new Error(label.split('\n')[0].slice(0, 24) + ' 有问题：' + e.message);
  }
}

function setEdPhoto(src) {
  const img = $('#edPhoto');
  if (!img) return;
  if (src) {
    img.src = src;
    img.classList.remove('empty');
  } else {
    img.src = BLANK_IMG;          // 清空时给个透明占位，否则浏览器会画出"碎图"图标
    img.classList.add('empty');
  }
}

function metaText(p) {
  if (!p) return '还没保存 · 保存后才会出现在镜片上';
  const bits = [];
  if (p.first_met) bits.push('首次 ' + String(p.first_met).slice(0, 10));
  if (p.last_met) bits.push('最近 ' + String(p.last_met).slice(0, 10));
  bits.push('见面 ' + (p.meet_count || 0) + ' 次');
  bits.push('底片 ' + (p.embedding_count || 0) + ' 张');
  if (p.voice_count) bits.push('声纹 ' + p.voice_count + ' 条');
  return bits.join(' · ');
}

function openEditor(id) {
  editing = (id && state.people.find((p) => p.id === id)) || null;
  pendingImage = null;
  pendingVoice = null;
  voiceRecording = false;

  $('#edTitle').textContent = editing ? editing.name : '新增人物';
  $('#edMeta').textContent = metaText(editing);
  $('#edName').value = editing ? editing.name : '';
  $('#edRole').value = editing ? editing.role || '' : '';
  const info = (editing && editing.info) || [];
  ['#ed1', '#ed2', '#ed3'].forEach((s, i) => { $(s).value = info[i] || ''; });
  $('#edSimple').value = jsonText(editing && editing.simple);
  $('#edComplex').value = jsonText(editing && editing.complex);
  setEdPhoto(editing && editing.photo ? photoUrl(editing) : '');
  $('#edPhotoHint').textContent = editing && editing.photo
    ? '已有一张底片。换照片会重新算特征。'
    : '正脸、光线均匀、别戴帽子。';

  const vc = (editing && editing.voice_count) || 0;
  $('#edVoiceHint').textContent = vc > 0
    ? '已有 ' + vc + ' 条声纹，重录会覆盖。'
    : '说一句「我是 XXX」，眼镜就能听声认人。';
  $('#edVoice').textContent = '◉ 录声纹（3 秒）';
  $('#edDelete').hidden = !editing;

  $('#empty').hidden = true;
  $('#editor').hidden = false;
  renderPeople();
  setTimeout(() => { const n = $('#edName'); if (n) n.focus(); }, 30);
}

function closeEditor() {
  stopVoiceIfRecording();
  $('#editor').hidden = true;
  $('#empty').hidden = false;
  editing = null;
  pendingImage = null;
  pendingVoice = null;
  voiceRecording = false;
  renderPeople();
}

async function savePerson() {
  const name = $('#edName').value.trim();
  if (!name) { $('#edName').focus(); return toast('先填名字', true); }
  if (!editing && !pendingImage) return toast('新增需要上传一张正脸照，否则认不出他', true);

  const body = {
    id: editing ? editing.id : '',
    name,
    role: $('#edRole').value.trim(),
    info: ['#ed1', '#ed2', '#ed3'].map((s) => $(s).value.trim()),
  };
  try {
    const simple = parseJsonField('#edSimple');
    const complex_ = parseJsonField('#edComplex');
    if (simple) body.simple = simple;
    if (complex_) body.complex = complex_;
  } catch (e) {
    return toast(e.message, true);
  }
  if (pendingImage) body.image = pendingImage;

  const btn = $('#edSave');
  btn.disabled = true;
  btn.textContent = '保存中…';
  try {
    const r = await post('/api/people/save', body);
    if (!r.ok) throw new Error(r.error || '保存失败');
    let voiceNote = '';
    if (pendingVoice) {
      const vr = await post('/api/voice/enroll', { id: r.person.id, audio: pendingVoice });
      if (!vr.ok) throw new Error(vr.error || '声纹保存失败');
      voiceNote = ' · 声纹 ' + vr.seconds + 's';
    }
    // 先解锁按钮再切换：否则连点两次会点到一个 disabled 的按钮
    btn.disabled = false;
    btn.textContent = '保存';
    closeEditor();
    await loadPeople();
    toast('已保存：' + name + voiceNote + (r.faces > 1 ? `（图里 ${r.faces} 张脸，取了最大那张）` : ''));
  } catch (e) {
    toast('保存失败：' + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '保存';
  }
}

async function deletePerson(p) {
  if (!p) return;
  if (!confirm(`删除「${p.name}」？照片和底片会一起删掉。`)) return;
  const r = await post('/api/people/delete', { id: p.id });
  if (!r.ok) return toast('删除失败：' + r.error, true);
  if (editing && editing.id === p.id) closeEditor();
  await loadPeople();
  toast('已删除：' + p.name);
}

/* ---------------- 声纹录制 ---------------- */

function floatToWav(chunks, sampleRate) {
  let total = 0;
  chunks.forEach((c) => { total += c.length; });
  const buf = new ArrayBuffer(44 + total * 2);
  const view = new DataView(buf);
  const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + total * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, total * 2, true);
  let off = 44;
  chunks.forEach((c) => {
    for (let i = 0; i < c.length; i++) {
      const s = Math.max(-1, Math.min(1, c[i]));
      view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      off += 2;
    }
  });
  return buf;
}

function blobToDataURL(blob) {
  return new Promise((res) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.readAsDataURL(blob);
  });
}

function stopVoiceIfRecording() {
  if (!voiceRec) return;
  const r = voiceRec;
  voiceRec = null;
  voiceRecording = false;
  clearTimeout(r.timer);
  try { r.proc.disconnect(); } catch (e) { /* 已断开 */ }
  try { r.ctx.close(); } catch (e) { /* 已关闭 */ }
  try { r.stream.getTracks().forEach((t) => t.stop()); } catch (e) { /* 已停止 */ }
  return r;
}

async function recordVoice() {
  if (voiceRecording) {                       // 再点一次 = 提前结束
    const r = stopVoiceIfRecording();
    if (r) await finishVoice(r);
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return toast('这个浏览器不给用麦克风，换 Chrome/Edge 打开', true);
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
  } catch (e) {
    return toast('拿不到麦克风：' + e.message, true);
  }
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const ctx = new AudioCtx({ sampleRate: 16000 });
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  proc.onaudioprocess = (ev) => { chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0))); };
  source.connect(proc);
  proc.connect(ctx.destination);

  voiceRec = {
    ctx, stream, proc, chunks,
    timer: setTimeout(async () => {
      const r = stopVoiceIfRecording();
      if (r) await finishVoice(r);
    }, VOICE_SECONDS * 1000),
  };
  voiceRecording = true;
  $('#edVoice').textContent = '◉ 正在录…（再点结束）';
  $('#edVoiceHint').textContent = '说话中，约 ' + VOICE_SECONDS + ' 秒';
}

async function finishVoice(r) {
  const sampleRate = r.ctx.sampleRate || 16000;
  const wav = floatToWav(r.chunks, sampleRate);
  const blob = new Blob([wav], { type: 'audio/wav' });
  pendingVoice = await blobToDataURL(blob);
  $('#edVoice').textContent = '◉ 重录声纹（3 秒）';
  $('#edVoiceHint').textContent = '已录 ' + (blob.size / 32000).toFixed(1) + ' 秒，保存时一起写入。';
}

/* ---------------- 绑定 ---------------- */

function bind() {
  const buildEl = $('#build');
  if (buildEl) buildEl.textContent = 'build ' + BUILD;

  const add = () => openEditor(null);
  if ($('#btnAdd')) $('#btnAdd').onclick = add;
  if ($('#btnAdd2')) $('#btnAdd2').onclick = add;
  if ($('#search')) $('#search').oninput = renderPeople;
  if ($('#edCancel')) $('#edCancel').onclick = closeEditor;
  if ($('#edSave')) $('#edSave').onclick = savePerson;
  if ($('#edVoice')) $('#edVoice').onclick = recordVoice;
  if ($('#edDelete')) $('#edDelete').onclick = () => deletePerson(editing);

  if ($('#edFile')) {
    $('#edFile').onchange = async () => {
      const f = $('#edFile').files[0];
      if (!f) return;
      pendingImage = await readAsDataURL(f);
      setEdPhoto(pendingImage);
      $('#edPhotoHint').textContent = '保存时会把这张脸存进人脉库。';
      $('#edFile').value = '';
    };
  }

  const fmtBtns = document.querySelectorAll('[data-fmt]');
  if (fmtBtns && fmtBtns.forEach) {
    fmtBtns.forEach((b) => {
      b.onclick = () => {
        const box = $(b.getAttribute('data-fmt'));
        try {
          const val = JSON.parse(box.value);
          box.value = JSON.stringify(val, null, 2);
        } catch (e) {
          toast('这段 JSON 不合法：' + e.message, true);
        }
      };
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.isComposing) return;   // 输入法组字中，任何快捷键都不该抢
    if (e.key === 'Escape' && !$('#editor').hidden) closeEditor();
  });
}

function readAsDataURL(file) {
  return new Promise((res) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.readAsDataURL(file);
  });
}

/* ---------------- 启动 ---------------- */

bind();
loadPeople();

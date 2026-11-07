/* MemoryLens 控制台（纯后台页面，不含摄像头）
   左侧人脉库列表，右侧编辑表单：照片 / 名字 / 身份 / 三条信息 / 两档 JSON / 声纹。
   后端接口：GET /api/people、POST /api/people/save、POST /api/people/delete、
             GET /api/photo?id=、POST /api/voice/enroll */

const $ = (s) => document.querySelector(s);

// 改代码就 +1，页脚会显示。看到 build 不是最新的，说明浏览器还在跑旧脚本。
const BUILD = 19;

const state = { people: [], threshold: 0.4 };

let editing = null;       // 正在编辑的人（null = 新增）
let pendingImage = null;  // 这次新选的照片（dataURL）
let pendingVoice = null;  // 这次新录的声纹（dataURL wav），保存时一起提交
let voiceRecording = false;
let voiceRec = null;

const VOICE_SECONDS = 7;
const BLANK_IMG = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

// 控制台选的麦克风（存本地）。换设备是"录不到声音"最常见的原因。
const MIC_KEY = 'ml.mic';
function storeGet(k) {
  try { return window.localStorage.getItem(k) || ''; } catch (e) { return ''; }
}
function storeSet(k, v) {
  try { window.localStorage.setItem(k, v); } catch (e) { /* 无痕模式 */ }
}
let voiceDeviceId = storeGet(MIC_KEY);

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
  renderOwnerWarn();
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
    if (p.owner) {
      const ow = document.createElement('i');
      ow.className = 'ownertag';
      ow.textContent = '主人';
      ow.title = '分轨时会被剔除的就是这个人';
      nm.append(' ', ow);
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
    if (!p.owner) {
      const own = document.createElement('button');
      own.className = 'mini ghost';
      own.textContent = '设为主人';
      own.onclick = async (e) => {
        e.stopPropagation();
        const r = await post('/api/people/owner', { id: p.id });
        if (!r.ok) return toast('设主人失败：' + r.error, true);
        await loadPeople();
        toast('已把「' + p.name + '」设为主人');
      };
      acts.append(own);
    }
    acts.append(ed, del);

    li.append(av, who, acts);
    li.onclick = () => openEditor(p.id);
    ul.appendChild(li);
  });
}

/* ---------------- 主人声纹提醒 ---------------- */

// 识别人时会先剔除"主人"那一簇；主人没有声纹时这一步等于没做，
// 主人自己说话就可能被当陌生人建号。这里给出一个一键去录的入口。
function renderOwnerWarn() {
  const box = $('#ownerWarn');
  if (!box) return;
  const owner = state.people.find((p) => p.owner);
  const missing = owner && !(owner.voice_count > 0);
  box.hidden = !missing;
  if (!missing) return;
  box.innerHTML = '';
  const p = document.createElement('p');
  p.textContent = '主人「' + owner.name + '」还没录声纹：识别人时不会剔除你自己，'
    + '容易被当成陌生人建号。';
  const btn = document.createElement('button');
  btn.className = 'mini';
  btn.textContent = '录主人声纹（' + VOICE_SECONDS + ' 秒）';
  btn.onclick = () => {
    openEditor(owner.id);
    const v = $('#edVoice');
    if (v && v.scrollIntoView) v.scrollIntoView({ block: 'center' });
  };
  box.append(p, btn);
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
  const isOwner = !!(editing && editing.owner);
  $('#edVoiceHint').textContent = isOwner
    ? (vc > 0
      ? '主人已有 ' + vc + ' 条声纹，重录会覆盖；识别人时会先剔除这一簇。'
      : '主人声纹用来在识别人时剔除你自己，录 ' + VOICE_SECONDS + ' 秒即可。')
    : (vc > 0
      ? '已有 ' + vc + ' 条声纹，重录会覆盖。'
      : '说一句「我是 XXX」，眼镜就能听声认人。');
  $('#edVoice').textContent = (isOwner ? '◉ 录主人声纹（' : '◉ 录声纹（') + VOICE_SECONDS + ' 秒）';
  $('#edDelete').hidden = !editing;

  $('#empty').hidden = true;
  $('#editor').hidden = false;
  listMics();
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
    toast('已保存：' + name + voiceNote
      + (!pendingImage && !pendingVoice && !editing ? '（没照片没声纹，先靠控制台记录）' : '')
      + (r.faces > 1 ? `（图里 ${r.faces} 张脸，取了最大那张）` : ''));
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

async function listMics() {
  const sel = $('#edMic');
  if (!sel || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
  let mics = [];
  try {
    mics = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audioinput');
  } catch (e) {
    return;
  }
  if (!mics.length) return;
  sel.innerHTML = '';
  mics.forEach((d, i) => {
    const o = document.createElement('option');
    o.value = d.deviceId;
    o.textContent = d.label || ('麦克风 ' + (i + 1));
    sel.appendChild(o);
  });
  if (voiceDeviceId && mics.some((d) => d.deviceId === voiceDeviceId)) sel.value = voiceDeviceId;
  sel.onchange = () => {
    voiceDeviceId = sel.value || '';
    storeSet(MIC_KEY, voiceDeviceId);
    toast('已切换麦克风');
  };
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
    const audio = { channelCount: 1 };
    if (voiceDeviceId) audio.deviceId = { exact: voiceDeviceId };
    stream = await navigator.mediaDevices.getUserMedia({ audio });
  } catch (e) {
    if (!voiceDeviceId) return toast('拿不到麦克风：' + e.message, true);
    // 记住的麦克风拔掉了：清掉选择，退回系统默认再试一次
    voiceDeviceId = '';
    storeSet(MIC_KEY, '');
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
    } catch (e2) {
      return toast('拿不到麦克风：' + e2.message, true);
    }
  }
  listMics();                                 // 拿到权限后设备名才可见
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  // 用设备默认采样率：强设 16k 在部分声卡上会录出一段静音（"没有声音"就是这么来的）
  const ctx = new AudioCtx();
  if (ctx.state === 'suspended') { try { await ctx.resume(); } catch (e) { /* 忽略 */ } }
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  let peak = 0;
  let lastUi = 0;
  const startedAt = Date.now();
  proc.onaudioprocess = (ev) => {
    const ch = new Float32Array(ev.inputBuffer.getChannelData(0));
    chunks.push(ch);
    for (let i = 0; i < ch.length; i++) {
      const a = Math.abs(ch[i]);
      if (a > peak) peak = a;
    }
    const now = Date.now();
    if (now - lastUi < 250) return;
    lastUi = now;
    const secs = ((now - startedAt) / 1000).toFixed(1);
    const hint = $('#edVoiceHint');
    if (!hint) return;
    hint.textContent = peak > 0.01
      ? '正在录 ' + secs + ' 秒 · 电平 ' + Math.round(peak * 100) + '%'
      : '正在录 ' + secs + ' 秒 · 还没听到声音，看一下麦克风';
  };
  source.connect(proc);
  proc.connect(ctx.destination);

  voiceRec = {
    ctx, stream, proc, chunks,
    peak: () => peak,
    timer: setTimeout(async () => {
      const r = stopVoiceIfRecording();
      if (r) await finishVoice(r);
    }, VOICE_SECONDS * 1000),
  };
  voiceRecording = true;
  $('#edVoice').textContent = '◉ 正在录…（再点结束）';
  $('#edVoiceHint').textContent = '说话中，约 ' + VOICE_SECONDS + ' 秒 · 等电平动起来';
}

async function finishVoice(r) {
  const sampleRate = r.ctx.sampleRate || 16000;
  const total = r.chunks.reduce((n, c) => n + c.length, 0);
  const secs = total / sampleRate;
  const peak = r.peak ? r.peak() : 0;
  const ownerVoice = !!(editing && editing.owner);
  const label = (ownerVoice ? '◉ 重录主人声纹（' : '◉ 重录声纹（') + VOICE_SECONDS + ' 秒）';
  if (secs < 1 || peak < 0.01) {
    // 服务端 PEAK_MIN=300（约 -40dB），这里先挡一道，免得保存时才报"没有声音"
    pendingVoice = null;
    $('#edVoice').textContent = label;
    $('#edVoiceHint').textContent =
      '这段几乎没有声音（' + secs.toFixed(1) + ' 秒 · 电平 ' + Math.round(peak * 100) + '%）：'
      + '换个麦克风，或检查 Windows 的默认输入设备，再录一次。';
    toast('没录到声音：换个麦克风再试', true);
    return;
  }
  const wav = floatToWav(r.chunks, sampleRate);
  const blob = new Blob([wav], { type: 'audio/wav' });
  pendingVoice = await blobToDataURL(blob);
  $('#edVoice').textContent = label;
  $('#edVoiceHint').textContent = '已录 ' + secs.toFixed(1) + ' 秒（电平 '
    + Math.round(peak * 100) + '%），保存时一起写入。';
}

/* ---------------- 绑定 ---------------- */

/* ---------------- 模块 02：单人对话分析 ---------------- */

const CONV_CHUNK_MS = 6000;   // 每 6 秒切一段送上去转写，避免一次性传几分钟音频
const CONV_MIN_FRAMES = 3;    // 攒够 3 帧（约 0.2s）才切，避免碎片

const conv = {
  llm: null,
  person: null,
  session: null,
  serverText: '',
  running: false,
  ctx: null,
  stream: null,
  proc: null,
  buf: [],
  flushing: false,
  startedAt: 0,
  ticker: null,
  chunkTimer: null,
  items: [],
};

function setModule(name) {
  const onConv = name === 'conv';
  const onPreset = name === 'preset';
  const showPeople = !onConv && !onPreset;
  const convEl = $('#conv');
  if (convEl) convEl.hidden = !onConv;
  if ($('#presets')) $('#presets').hidden = !onPreset;
  if ($('#contentPeople')) $('#contentPeople').hidden = !showPeople;
  if ($('#listcol')) $('#listcol').hidden = !showPeople;
  [['#modPeople', showPeople], ['#modConv', onConv], ['#modPreset', onPreset]].forEach(([sel, on]) => {
    const el = $(sel);
    if (!el) return;
    el.classList.toggle('is-active', !!on);
    el.setAttribute('aria-current', on ? 'page' : 'false');
  });
  if (onConv) {
    convBindOnce();
    renderConvPeople();
    loadConvLlm();
  }
  if (onPreset) {
    presetBindOnce();
    loadPresets();
  }
}

/* ---------------- 预设按钮（镜片主页那三个） ---------------- */
const presets = { items: [], busy: false, bound: false };

function mk(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

async function loadPresets() {
  try {
    const res = await fetch('/api/presets');
    const data = await res.json();
    presets.items = (data.items || []).map((x) => ({ id: x.id, label: x.label, prompt: x.prompt }));
    renderPresets();
    const cfg = await (await fetch('/api/settings')).json();
    if ($('#holdSec')) $('#holdSec').value = cfg.hold_sec || 30;
    if ($('#firstHoldSec')) $('#firstHoldSec').value = cfg.first_hold_sec || 9;
    if ($('#identifySec')) $('#identifySec').value = cfg.identify_sec || 20;
  } catch (e) {
    if ($('#presetMsg')) $('#presetMsg').textContent = '读不到预设：' + e.message;
  }
}

function renderPresets() {
  const box = $('#presetList');
  if (!box) return;
  box.replaceChildren();
  presets.items.forEach((it, i) => {
    const row = mk('div', 'preset-row');
    const idx = mk('span', 'preset-idx', String(i + 1));
    const label = mk('input', 'preset-label');
    label.placeholder = '按钮名（≤4 字）';
    label.value = it.label || '';
    label.oninput = () => { it.label = label.value; };
    const prompt = mk('input', 'preset-prompt');
    prompt.placeholder = '提示词：按下确认时一起发给模型';
    prompt.value = it.prompt || '';
    prompt.oninput = () => { it.prompt = prompt.value; };
    const del = mk('button', 'btn ghost tiny', '删');
    del.type = 'button';
    del.onclick = () => { presets.items.splice(i, 1); renderPresets(); };
    row.append(idx, label, prompt, del);
    box.append(row);
  });
  if (!presets.items.length) {
    box.append(mk('p', 'tiny', '还没有按钮，点右上角「＋ 加一个」。'));
  }
}

async function savePresets() {
  if (presets.busy) return;
  presets.busy = true;
  const btn = $('#presetSave');
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/presets/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: presets.items }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '保存失败');
    const cfgRes = await fetch('/api/settings/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hold_sec: Number(($('#holdSec') || {}).value || 30),
        first_hold_sec: Number(($('#firstHoldSec') || {}).value || 9),
        identify_sec: Number(($('#identifySec') || {}).value || 20),
      }),
    });
    const cfg = await cfgRes.json();
    if (!cfg.ok) throw new Error(cfg.error || '时长保存失败');
    presets.items = data.items || presets.items;
    renderPresets();
    if ($('#presetMsg')) $('#presetMsg').textContent = '已保存 · 眼镜下次启动生效';
  } catch (e) {
    if ($('#presetMsg')) $('#presetMsg').textContent = '保存失败：' + e.message;
  } finally {
    presets.busy = false;
    if (btn) btn.disabled = false;
  }
}

function presetBindOnce() {
  if (presets.bound) return;
  presets.bound = true;
  if ($('#presetAdd')) {
    $('#presetAdd').onclick = () => {
      if (presets.items.length >= 3) {
        if ($('#presetMsg')) $('#presetMsg').textContent = '镜片主页面只放得下 3 个按钮';
        return;
      }
      presets.items.push({ id: 'p' + Date.now().toString(36), label: '', prompt: '' });
      renderPresets();
    };
  }
  if ($('#presetSave')) $('#presetSave').onclick = savePresets;
}

async function loadConvLlm() {
  if (conv.llm) return;
  try {
    const r = await fetch('/api/converse/llm').then((x) => x.json());
    conv.llm = r;
  } catch (e) {
    conv.llm = { available: false };
  }
  const banner = $('#convBanner');
  if (banner) {
    const asrOk = conv.llm.asrAvailable !== false;
    if (conv.llm.stub) {
      banner.hidden = false;
      banner.textContent = '当前是 stub 模式（AR_LLM_STUB=1），返回的是假数据，只用于测试。';
    } else if (!asrOk) {
      banner.hidden = false;
      banner.innerHTML = '没有可用的转写通道：放一份本地模型到 <code>models/asr/</code>，'
        + '或设 <code>AR_ASR_KEY</code> 用云端转写。';
    } else if (!conv.llm.available) {
      banner.hidden = false;
      banner.innerHTML = '转写用 <b>' + (conv.llm.asrProvider === 'local' ? '本地模型' : conv.llm.asr)
        + '</b>，但没配文本模型 Key（<code>AR_LLM_KEY</code> / <code>MIMO_API_KEY</code> / <code>DEEPSEEK_API_KEY</code>）：'
        + '能录音能转写，只是「分析」按不动。';
    } else {
      banner.hidden = true;
    }
  }
  if (conv.person) $('#convRec').disabled = !asrOk;
  const an = $('#convAnalyze');
  if (an) an.disabled = !conv.llm.available || !($('#convText') && $('#convText').value.trim());
}

function convFiltered() {
  const q = ($('#convSearch') ? $('#convSearch').value : '').trim().toLowerCase();
  return state.people.filter((p) => {
    if (!q) return true;
    return (p.name || '').toLowerCase().includes(q) || (p.role || '').toLowerCase().includes(q);
  });
}

function makeDot() {
  const d = document.createElement('div');
  d.className = 'dot';
  return d;
}

function renderConvPeople() {
  const ul = $('#convPeople');
  if (!ul) return;
  ul.innerHTML = '';
  const list = convFiltered();
  if (!list.length) {
    const li = document.createElement('li');
    li.className = 'conv-empty';
    li.textContent = '没有匹配的人；先去「01 人物数据」建一个';
    ul.appendChild(li);
    return;
  }
  list.forEach((p) => {
    const li = document.createElement('li');
    if (conv.person && conv.person.id === p.id) li.classList.add('is-locked');
    const src = p.photo ? photoUrl(p) : '';
    if (src) {
      const img = document.createElement('img');
      img.src = src;
      img.alt = '';
      img.onerror = () => { img.replaceWith(makeDot()); };
      li.appendChild(img);
    } else {
      li.appendChild(makeDot());
    }
    const box = document.createElement('div');
    const nm = document.createElement('div');
    nm.className = 'nm';
    nm.textContent = p.name;
    const rl = document.createElement('div');
    rl.className = 'rl';
    rl.textContent = p.role || '未填身份';
    box.appendChild(nm);
    box.appendChild(rl);
    li.appendChild(box);

    const tag = document.createElement('span');
    tag.className = 'lock';
    tag.textContent = conv.person && conv.person.id === p.id ? '已锁定' : '锁定';
    li.appendChild(tag);

    li.onclick = () => lockPerson(p);
    ul.appendChild(li);
  });
}

async function lockPerson(p) {
  if (conv.running) return toast('先停止录音再换人', true);
  conv.person = p;
  conv.items = [];
  const r = await post('/api/converse/start', { person_id: p.id });
  if (!r.ok) { conv.person = null; return toast(r.error || '锁定失败', true); }
  conv.session = r.session;
  conv.serverText = '';
  if ($('#convText')) $('#convText').value = '';
  if ($('#convResult')) $('#convResult').hidden = true;
  $('#convTitle').textContent = p.name;
  $('#convMeta').textContent = (p.role || '未填身份') + ' · 已锁定，会话 ' + r.session;
  convState('锁定成功，按「开始录音」');
  $('#convAnalyze').disabled = true;
  $('#convRec').disabled = !(conv.llm && conv.llm.available);
  renderConvPeople();
}

function convState(txt) {
  if ($('#convState')) $('#convState').textContent = txt;
}

function convSecs() {
  const s = Math.floor((Date.now() - conv.startedAt) / 1000);
  if ($('#convSecs')) $('#convSecs').textContent = conv.running ? s + ' 秒' : '待机';
}

async function convStart() {
  if (!conv.person) return toast('先锁一个人', true);
  if (!conv.llm || !conv.llm.available) return toast('没配 MIMO_API_KEY，转写用不了', true);
  if (conv.running) return;
  try {
    conv.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    return toast('拿不到麦克风：' + e.message, true);
  }
  conv.ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = conv.ctx.createMediaStreamSource(conv.stream);
  conv.proc = conv.ctx.createScriptProcessor(4096, 1, 1);
  conv.proc.onaudioprocess = (ev) => {
    conv.buf.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
  };
  src.connect(conv.proc);
  conv.proc.connect(conv.ctx.destination);   // 不接出去某些浏览器不回调；输出是静音，不会啸叫

  conv.running = true;
  conv.buf = [];
  conv.startedAt = Date.now();
  conv.ticker = setInterval(convSecs, 500);
  conv.chunkTimer = setInterval(() => convFlush(false), CONV_CHUNK_MS);
  $('#convRec').hidden = true;
  $('#convStop').hidden = false;
  convState('录音中…每 6 秒自动送一段去转写');
}

async function convFlush(final) {
  if (conv.flushing) return;
  if (!conv.buf.length) return;
  if (!final && conv.buf.length < CONV_MIN_FRAMES) return;
  conv.flushing = true;
  const chunks = conv.buf;
  conv.buf = [];
  try {
    const sr = conv.ctx ? conv.ctx.sampleRate : 16000;
    const wav = floatToWav(chunks, sr);
    const url = await blobToDataURL(new Blob([wav], { type: 'audio/wav' }));
    const r = await post('/api/converse/chunk', { session: conv.session, audio: url });
    if (!r.ok) throw new Error(r.error || '转写失败');
    conv.serverText = r.transcript;
    if ($('#convText')) $('#convText').value = conv.serverText;
    convState('已转写 ' + r.transcript.length + ' 字 · ' + r.seconds + ' 秒');
  } catch (e) {
    toast('这段没转成：' + e.message, true);
  } finally {
    conv.flushing = false;
  }
}

async function convStop() {
  if (!conv.running) return;
  conv.running = false;
  clearInterval(conv.ticker);
  clearInterval(conv.chunkTimer);
  try { conv.proc.disconnect(); } catch (e) { /* 已断开 */ }
  try { conv.ctx.close(); } catch (e) { /* 已关闭 */ }
  try { conv.stream.getTracks().forEach((t) => t.stop()); } catch (e) { /* 已停止 */ }
  conv.proc = null;
  conv.ctx = null;
  conv.stream = null;
  $('#convRec').hidden = false;
  $('#convStop').hidden = true;
  convSecs();
  await convFlush(true);
  convState(conv.serverText ? '录音结束，可以点「分析这段对话」' : '这次没听到内容');
  $('#convAnalyze').disabled = !conv.llm || !conv.llm.available || !conv.serverText.trim();
}

async function convAnalyze() {
  if (!conv.session) return toast('先锁一个人', true);
  const box = ($('#convText') ? $('#convText').value : '').trim();
  if (!box) return toast('还没有内容，先录一段或粘一段', true);
  $('#convAnalyze').disabled = true;
  const old = $('#convAnalyze').textContent;
  $('#convAnalyze').textContent = '分析中…';
  try {
    if (box !== conv.serverText) {
      const r1 = await post('/api/converse/text', { session: conv.session, text: box, replace: true });
      if (!r1.ok) throw new Error(r1.error || '文本没存上');
      conv.serverText = r1.transcript;
    }
    const r = await post('/api/converse/analyze', { session: conv.session });
    if (!r.ok) throw new Error(r.error || '分析失败');
    conv.items = (r.items || []).map((it) => ({ ...it, on: true }));
    $('#convSummary').textContent = r.summary || '（模型没给概括）';
    renderConvItems();
    $('#convResult').hidden = false;
    convState('分析完成 · ' + conv.items.length + ' 条要点');
  } catch (e) {
    toast('分析失败：' + e.message, true);
  } finally {
    $('#convAnalyze').textContent = old;
    $('#convAnalyze').disabled = false;
  }
}

function renderConvItems() {
  const ul = $('#convItems');
  if (!ul) return;
  ul.innerHTML = '';
  conv.items.forEach((it, i) => {
    const li = document.createElement('li');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = it.on !== false;
    cb.onchange = () => { conv.items[i].on = cb.checked; refreshConvSave(); };
    const k = document.createElement('span');
    k.className = 'k';
    k.textContent = it.key || '其他';
    const v = document.createElement('span');
    v.textContent = it.value;
    if (it.quote) {
      const q = document.createElement('span');
      q.className = 'q';
      q.textContent = '（原话：' + it.quote + '）';
      v.appendChild(document.createElement('br'));
      v.appendChild(q);
    }
    li.appendChild(cb);
    li.appendChild(k);
    li.appendChild(v);
    ul.appendChild(li);
  });
  refreshConvSave();
}

function refreshConvSave() {
  const n = conv.items.filter((i) => i.on !== false).length;
  const btn = $('#convSave');
  if (!btn) return;
  btn.disabled = n === 0;
  btn.textContent = n ? '写入档案（' + n + ' 条）' : '没有可写的';
}

async function convSave() {
  const picked = conv.items.filter((i) => i.on !== false)
    .map((i) => ({ key: i.key, value: i.value }));
  if (!picked.length) return;
  const btn = $('#convSave');
  btn.disabled = true;
  btn.textContent = '写入中…';
  try {
    const r = await post('/api/converse/save', { session: conv.session, items: picked });
    if (!r.ok) throw new Error(r.error || '写入失败');
    await loadPeople();
    renderConvPeople();
    toast('已写进 ' + r.person.name + ' 的档案：' + r.saved + ' 条');
    convState('已写入档案 · 复杂数据里多了「对话要点」');
  } catch (e) {
    toast('写入失败：' + e.message, true);
  } finally {
    refreshConvSave();
  }
}

function convClear() {
  if (conv.running) return toast('先停止录音', true);
  if ($('#convText')) $('#convText').value = '';
  if ($('#convResult')) $('#convResult').hidden = true;
  conv.items = [];
  conv.serverText = '';
  if (conv.session && $('#convText')) {
    post('/api/converse/text', { session: conv.session, text: '', replace: true });
  }
  $('#convAnalyze').disabled = true;
  convState('已清空，可以重新录');
}

let convBound = false;
function convBindOnce() {
  if (convBound) return;
  convBound = true;
  if ($('#convSearch')) $('#convSearch').oninput = renderConvPeople;
  if ($('#convText')) {
    // 手打/粘贴也要能直接分析：有字就把按钮点亮
    $('#convText').oninput = () => {
      $('#convAnalyze').disabled = !conv.llm || !conv.llm.available || !$('#convText').value.trim();
    };
  }
  if ($('#convRec')) $('#convRec').onclick = convStart;
  if ($('#convStop')) $('#convStop').onclick = convStop;
  if ($('#convAnalyze')) $('#convAnalyze').onclick = convAnalyze;
  if ($('#convSave')) $('#convSave').onclick = convSave;
  if ($('#convClear')) $('#convClear').onclick = convClear;
}

function bind() {
  const buildEl = $('#build');
  if (buildEl) buildEl.textContent = 'build ' + BUILD;

  const add = () => openEditor(null);
  if ($('#btnAdd')) $('#btnAdd').onclick = add;
  if ($('#btnAdd2')) $('#btnAdd2').onclick = add;
  if ($('#modPeople')) $('#modPeople').onclick = () => setModule('people');
  if ($('#modConv')) $('#modConv').onclick = () => setModule('conv');
  if ($('#modPreset')) $('#modPreset').onclick = () => setModule('preset');
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

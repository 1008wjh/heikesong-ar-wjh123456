/* MemoryLens 控制台（纯后台页面，不含摄像头）
   左侧人脉库列表，右侧编辑表单：照片 / 名字 / 身份 / 三条信息 / 两档 JSON / 声纹。
   后端接口：GET /api/people、POST /api/people/save、POST /api/people/delete、
             GET /api/photo?id=、POST /api/voice/enroll */

const $ = (s) => document.querySelector(s);

/* ---------------- 登录态：所有 /api 请求带 Bearer，401 回登录页 ---------------- */
(function authSetup() {
  const token = localStorage.getItem('ml_token') || '';
  if (!token) { location.replace('login.html'); return; }
  const _fetch = window.fetch.bind(window);
  window.fetch = (url, opts = {}) => {
    const u = String(url);
    if (u.indexOf('/api/') === 0) {
      const headers = Object.assign({}, opts.headers || {});
      headers['Authorization'] = 'Bearer ' + token;
      opts = Object.assign({}, opts, { headers });
    }
    return _fetch(url, opts).then((r) => {
      if (r.status === 401) {
        localStorage.removeItem('ml_token');
        location.replace('login.html');
      }
      return r;
    });
  };
  _fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + token } })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (!d || !d.ok) return;
      const el = document.getElementById('whoami');
      if (el) el.textContent = (d.user && (d.user.name || d.user.username)) || d.user.username;
    })
    .catch(() => {});
})();

// 改代码就 +1，页脚会显示。看到 build 不是最新的，说明浏览器还在跑旧脚本。
const BUILD = 25;

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
  const hasClip = !!(editing && editing.voice_clip_at);
  if (hasClip) $('#edVoiceHint').textContent += ' 最近一次注册录音已保存，可重播核对。';
  $('#edVoice').textContent = (isOwner ? '◉ 录主人声纹（' : '◉ 录声纹（') + VOICE_SECONDS + ' 秒）';

  // 重播最近一次注册时的录音（旧记录没有存录音，重录一次就有）
  const play = $('#edVoicePlay');
  if (play) {
    play.disabled = !hasClip;
    play.textContent = hasClip ? '▶ 重播声纹' : '▶ 重播声纹（重录后可用）';
    play.onclick = () => {
      if (!editing || !hasClip) return;
      const audio = new Audio('/api/voice/clip?id=' + encodeURIComponent(editing.id) + '&t=' + Date.now());
      audio.play().catch((err) => toast('播放失败：' + err.message, true));
      toast('正在重播最近一次注册录音');
    };
  }
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
  sel.innerHTML = '';
  const def = document.createElement('option');
  def.value = '';
  def.textContent = '系统默认（跟随系统）';
  sel.appendChild(def);
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
    toast(voiceDeviceId ? '已切换麦克风' : '已切回系统默认麦克风');
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

/* ---------------- 模块 04：多人会议 ---------------- */

// 后端接口（由后端同学实现）：
//   GET  /api/meetings              列表
//   GET  /api/meetings/<session>    详情：meeting / speakers / segments / answers / offline_summary
//   POST /api/meeting/offline       提交离线归集（异步，status 0→1→2）
// segments 的 start_ts / end_ts 是毫秒。

const mtg = {
  items: [],
  loading: false,
  bound: false,
  current: null,      // 正在看的 session_id
  detail: null,       // 详情接口的原始数据
  pollTimer: null,
  pollCount: 0,
  submitting: false,
};

const MTG_OFFLINE = {
  0: { text: '未提交', cls: 'is-0' },
  1: { text: '处理中', cls: 'is-1' },
  2: { text: '已完成', cls: 'is-2' },
};

function offlineInfo(n) {
  const k = Number(n);
  if (!isFinite(k)) return MTG_OFFLINE[0];
  return MTG_OFFLINE[k] || { text: '状态 ' + k, cls: 'is-0' };
}

function pad2(n) {
  return (n < 10 ? '0' : '') + n;
}

// 秒 → mm:ss（超过一小时补小时位，避免出现 123:45 这种）
function fmtDur(sec) {
  const n = Number(sec);
  if (!isFinite(n) || n < 0) return '—';
  const t = Math.floor(n);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return h ? h + ':' + pad2(m) + ':' + pad2(s) : pad2(m) + ':' + pad2(s);
}

function fmtMs(ms) {
  return fmtDur(Number(ms) / 1000);
}

// 说话人累计时长：不到一分钟给「24.7 秒」，更长用 mm:ss
function fmtSecShort(sec) {
  const n = Number(sec);
  if (!isFinite(n) || n < 0) return '—';
  if (n < 60) return n.toFixed(1) + ' 秒';
  return fmtDur(n);
}

// "2026-11-08 12:00:00" → "2026-11-08 12:00"
function fmtTime(s) {
  const t = String(s == null ? '' : s).trim();
  return t ? t.slice(0, 16) : '—';
}

function truncateText(s, n) {
  const t = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n) + '…' : t;
}

// 读 JSON：接口没实现/返回错误码时给出能看懂的信息，不抛白屏
async function getJson(url) {
  let res;
  try {
    res = await fetch(url);
  } catch (e) {
    throw new Error('后端连不上（' + (e.message || '网络错误') + '）');
  }
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    throw new Error('返回的不是 JSON（HTTP ' + res.status + '）');
  }
  if (!res.ok || (data && data.ok === false)) {
    const msg = (data && data.error) || ('HTTP ' + res.status);
    if (res.status === 404) throw new Error('接口还没实现（' + msg + '）');
    throw new Error(msg);
  }
  return data;
}

function setMtgErr(text) {
  const box = $('#mtgErr');
  if (!box) return;
  box.replaceChildren();
  if (!text) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const p = mk('p', null, text);
  const retry = mk('button', 'mini', '重试');
  retry.type = 'button';
  retry.onclick = () => loadMeetings({ toast: true });
  box.append(p, retry);
}

function setMtgDErr(text) {
  const box = $('#mtgDErr');
  if (!box) return;
  box.textContent = text || '';
  box.hidden = !text;
}

async function loadMeetings(opts) {
  const o = opts || {};
  if (mtg.loading) return;
  mtg.loading = true;
  try {
    const data = await getJson('/api/meetings');
    mtg.items = Array.isArray(data.items) ? data.items : [];
    setMtgErr('');
    renderMtgList();
    if (o.toast) toast('已刷新 · ' + mtg.items.length + ' 场会议');
  } catch (e) {
    mtg.items = [];
    setMtgErr('读不到会议列表：' + e.message);
    renderMtgList();
    if (o.toast) toast('读不到会议列表：' + e.message, true);
  } finally {
    mtg.loading = false;
  }
}

function renderMtgList() {
  const ul = $('#mtgList');
  if (!ul) return;
  ul.replaceChildren();
  const hasErr = $('#mtgErr') && !$('#mtgErr').hidden;
  if ($('#mtgEmpty')) $('#mtgEmpty').hidden = mtg.items.length > 0 || !!hasErr;
  if (!mtg.items.length) return;

  mtg.items.forEach((item) => {
    const sid = item.session_id || '';
    const li = mk('li', 'mtg-row');
    li.tabIndex = 0;
    li.setAttribute('role', 'button');
    li.setAttribute('aria-label', '打开会议 ' + (item.summary || sid));
    li.onclick = () => openMeeting(sid);
    li.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openMeeting(sid);
      }
    };

    const when = mk('div', 'mtg-when');
    when.append(
      mk('b', 'mtg-date', fmtTime(item.create_time)),
      mk('span', 'mtg-dur', '时长 ' + fmtDur(item.duration_sec))
    );

    const info = mk('div', 'mtg-info');
    const line = mk('div', 'mtg-line');
    line.append(mk('span', 'mtg-sum', item.summary_text || item.summary || '（没有摘要）'));
    if (Number(item.is_recording)) line.append(mk('i', 'mtg-rec', '录音中'));
    info.append(
      line,
      mk('div', 'mtg-stats',
        Number(item.speaker_count || 0) + ' 人 · '
        + Number(item.segment_count || 0) + ' 段 · '
        + Number(item.answer_count || 0) + ' 条问答')
    );

    const st = offlineInfo(item.offline_diarization_status);
    const side = mk('div', 'mtg-side');
    side.append(mk('span', 'mtg-badge ' + st.cls, '离线归集 ' + st.text));
    side.append(mk('span', 'mtg-go', '查看 →'));

    li.append(when, info, side);
    ul.append(li);
  });
}

function showMtgDetail(on) {
  if ($('#mtgListView')) $('#mtgListView').hidden = !!on;
  if ($('#mtgDetailView')) $('#mtgDetailView').hidden = !on;
}

function mtgStopPoll() {
  if (mtg.pollTimer) {
    clearInterval(mtg.pollTimer);
    mtg.pollTimer = null;
  }
}

// status=1（处理中）时每 3 秒悄悄刷一次，最多 60 秒；
// 离开详情/返回列表会清掉，避免后台一直打请求。
function startMtgPoll(sessionId) {
  mtgStopPoll();
  mtg.pollCount = 0;
  mtg.pollTimer = setInterval(() => {
    if (mtg.current !== sessionId || ($('#meetings') && $('#meetings').hidden)) {
      mtgStopPoll();
      return;
    }
    mtg.pollCount += 1;
    if (mtg.pollCount > 20) {
      mtgStopPoll();
      return;
    }
    loadMeetingDetail(sessionId, { quiet: true });
  }, 3000);
}

async function openMeeting(sessionId) {
  if (!sessionId) return toast('这条会议缺少 session_id', true);
  mtgStopPoll();
  mtg.current = sessionId;
  mtg.detail = null;
  showMtgDetail(true);
  renderMtgLoading();
  const detailView = $('#mtgDetailView');
  if (detailView && detailView.scrollTo) detailView.scrollTo({ top: 0 });
  await loadMeetingDetail(sessionId);
}

function backToMtgList() {
  mtgStopPoll();
  mtg.current = null;
  mtg.detail = null;
  showMtgDetail(false);
  if ($('#meetings') && !$('#meetings').hidden) loadMeetings({ quiet: true });
}

function renderMtgLoading() {
  if ($('#mtgSid')) $('#mtgSid').textContent = mtg.current || '—';
  if ($('#mtgDTitle')) $('#mtgDTitle').textContent = '读取中…';
  if ($('#mtgDMeta')) $('#mtgDMeta').textContent = '';
  setMtgDErr('');
  if ($('#mtgFacts')) $('#mtgFacts').replaceChildren(mk('p', 'hint', '正在读取会议数据…'));
  if ($('#mtgSegs')) $('#mtgSegs').replaceChildren();
  if ($('#mtgAnswers')) $('#mtgAnswers').replaceChildren();
  if ($('#mtgSegCount')) $('#mtgSegCount').textContent = '…';
  if ($('#mtgAnsCount')) $('#mtgAnsCount').textContent = '…';
  if ($('#mtgOffline')) {
    $('#mtgOffline').replaceChildren(mk('h3', null, '离线归集纪要'), mk('p', 'hint', '正在读取…'));
  }
}

async function loadMeetingDetail(sessionId, opts) {
  const o = opts || {};
  const btn = $('#mtgDRefresh');
  if (!o.quiet && btn) btn.disabled = true;
  try {
    const data = await getJson('/api/meetings/' + encodeURIComponent(sessionId));
    if (mtg.current !== sessionId) return;   // 已经返回列表或换了一场
    mtg.detail = data;
    setMtgDErr('');
    renderMtgDetail(data);
    const st = Number((data.meeting || {}).offline_diarization_status || 0);
    if (st === 1) startMtgPoll(sessionId);
    else mtgStopPoll();
  } catch (e) {
    if (mtg.current !== sessionId) return;
    setMtgDErr('读不到这场会议：' + e.message);
    if ($('#mtgDTitle')) $('#mtgDTitle').textContent = '读不到这场会议';
    resetMtgSections('会议数据没读到，点「刷新」再试一次。');
    if (!o.quiet) toast('读不到这场会议：' + e.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 详情读取失败时，把四个区域都换成可读的占位，避免停在「读取中…」
function resetMtgSections(text) {
  if ($('#mtgFacts')) $('#mtgFacts').replaceChildren(mk('p', 'hint', text));
  if ($('#mtgSegs')) $('#mtgSegs').replaceChildren();
  if ($('#mtgAnswers')) $('#mtgAnswers').replaceChildren();
  if ($('#mtgSegCount')) $('#mtgSegCount').textContent = '0 段';
  if ($('#mtgAnsCount')) $('#mtgAnsCount').textContent = '0 条';
  if ($('#mtgOffline')) {
    $('#mtgOffline').replaceChildren(mk('h3', null, '离线归集纪要'), mk('p', 'hint', text));
  }
}

function renderMtgDetail(data) {
  const m = (data && data.meeting) || {};
  if ($('#mtgSid')) $('#mtgSid').textContent = m.session_id || mtg.current || '—';
  if ($('#mtgDTitle')) $('#mtgDTitle').textContent = m.summary_text || m.summary || '多人会议';
  const bits = [];
  if (m.create_time) bits.push('创建 ' + fmtTime(m.create_time));
  if (m.finish_time) bits.push('结束 ' + fmtTime(m.finish_time));
  else if (Number(m.is_recording)) bits.push('录音中');
  if (m.meeting_id) bits.push('编号 ' + m.meeting_id);
  if ($('#mtgDMeta')) $('#mtgDMeta').textContent = bits.join(' · ') || '—';
  renderMtgFacts(m, data || {});
  renderMtgSegments(data || {});
  renderMtgAnswers(data || {});
  renderMtgOffline(m, data || {});
}

function renderMtgFacts(m, data) {
  const box = $('#mtgFacts');
  if (!box) return;
  box.replaceChildren();
  const speakers = Array.isArray(data.speakers) ? data.speakers : [];
  const facts = [
    ['时长', fmtDur(m.duration_sec)],
    ['说话人数', Number(m.speaker_count || speakers.length || 0) + ' 人'],
    ['转写段数', Number(m.segment_count || 0) + ' 段'],
    ['AI 问答', Number(m.answer_count || 0) + ' 条'],
    ['创建时间', fmtTime(m.create_time)],
    ['结束时间', m.finish_time ? fmtTime(m.finish_time) : (Number(m.is_recording) ? '录音中' : '—')],
  ];
  facts.forEach(([k, v]) => {
    const cell = mk('div', 'mtg-fact');
    cell.append(mk('span', 'mtg-fact-k', k), mk('b', 'mtg-fact-v', v));
    box.append(cell);
  });

  if (m.summary_text) {
    const cell = mk('div', 'mtg-fact is-wide');
    cell.append(mk('span', 'mtg-fact-k', '会议总结'));
    cell.append(mk('b', 'mtg-fact-v', m.summary_text));
    box.append(cell);
  }
  if (Array.isArray(m.key_points) && m.key_points.length) {
    const cell = mk('div', 'mtg-fact is-wide');
    cell.append(mk('span', 'mtg-fact-k', '关键结论'));
    cell.append(mk('b', 'mtg-fact-v', m.key_points.join(' · ')));
    box.append(cell);
  }
  if (Array.isArray(m.action_items) && m.action_items.length) {
    const cell = mk('div', 'mtg-fact is-wide');
    cell.append(mk('span', 'mtg-fact-k', '待办'));
    cell.append(mk('b', 'mtg-fact-v', m.action_items.join(' · ')));
    box.append(cell);
  }

  if (speakers.length) {
    const cell = mk('div', 'mtg-fact is-wide');
    cell.append(mk('span', 'mtg-fact-k', '发言人'));
    const chips = mk('div', 'mtg-chips');
    speakers.forEach((s, i) => {
      const n = speakerNo(String(s.spk_id || ''), null, i + 1);
      chips.append(mk('span', 'mtg-chip', 'S' + n + ' ' + (s.name || '发言人')));
    });
    cell.append(chips);
    box.append(cell);
  }

  const audio = mk('div', 'mtg-fact is-wide');
  audio.append(mk('span', 'mtg-fact-k', '音频文件'));
  audio.append(mk('b', 'mtg-fact-v', m.audio_file_path || '—'));
  box.append(audio);
}

function speakerNo(spkId, byId, fallback) {
  if (byId && byId[spkId] != null) return byId[spkId];
  const hit = /(\d+)\s*$/.exec(String(spkId || ''));
  if (hit) {
    const n = Number(hit[1]);
    if (n > 0) return n;
  }
  return fallback || 0;
}

function speakerIndexOf(speakers) {
  const map = {};
  (speakers || []).forEach((s, i) => {
    if (s && s.spk_id) map[s.spk_id] = i + 1;
  });
  return map;
}

function renderMtgSegments(data) {
  const ul = $('#mtgSegs');
  if (!ul) return;
  ul.replaceChildren();
  const segs = (Array.isArray(data.segments) ? data.segments : []).slice()
    .sort((a, b) => Number(a.start_ts || 0) - Number(b.start_ts || 0));
  if ($('#mtgSegCount')) $('#mtgSegCount').textContent = segs.length + ' 段';
  if (!segs.length) {
    ul.append(mk('li', 'mtg-none', '还没有转写内容（可能是静音，或转写模型没配）。'));
    return;
  }
  const byId = speakerIndexOf(data.speakers);
  segs.forEach((seg) => {
    const n = speakerNo(String(seg.spk_id || ''), byId, 0);
    const name = seg.name || '发言人';
    const li = mk('li', 'mtg-seg');
    const who = mk('span', 'mtg-who spk' + (n % 4));
    who.textContent = n ? 'S' + n + ' ' + name : name;
    who.title = String(seg.spk_id || '');
    const ts = mk('span', 'mtg-ts', fmtMs(seg.start_ts));
    ts.title = '结束 ' + fmtMs(seg.end_ts);
    li.append(who, ts, mk('p', 'mtg-text', String(seg.content || '')));
    ul.append(li);
  });
}

function renderMtgAnswers(data) {
  const box = $('#mtgAnswers');
  if (!box) return;
  box.replaceChildren();
  const list = (Array.isArray(data.answers) ? data.answers : []).slice()
    .sort((a, b) => String(a.create_time || '').localeCompare(String(b.create_time || ''))
      || Number(a.id || 0) - Number(b.id || 0));
  if ($('#mtgAnsCount')) $('#mtgAnsCount').textContent = list.length + ' 条';
  if (!list.length) {
    box.append(mk('p', 'hint', '这场会议还没有 AI 提问记录。'));
    return;
  }
  list.forEach((a) => {
    const item = mk('div', 'mtg-ans');
    const head = mk('div', 'mtg-ans-head');
    head.append(mk('b', 'mtg-ans-t', fmtTime(a.create_time)));
    if (a.id != null) head.append(mk('span', 'mtg-ans-id', '#' + a.id));
    item.append(head);

    const ctx = String(a.context_snapshot || '').trim();
    if (ctx) {
      const det = document.createElement('details');
      det.className = 'mtg-ctx';
      const sum = document.createElement('summary');
      sum.textContent = '上下文：' + truncateText(ctx, 70);
      det.append(sum, mk('pre', 'mtg-ctx-full', ctx));
      item.append(det);
    }

    const body = mk('div', 'mtg-ans-body');
    body.append(mk('span', 'mtg-ans-k', 'AI 回答'));
    body.append(mk('p', 'mtg-ans-text', String(a.answer || '（空）')));
    item.append(body);
    box.append(item);
  });
}

function renderMtgOffline(m, data) {
  const box = $('#mtgOffline');
  if (!box) return;
  box.replaceChildren();
  const st = Number(m.offline_diarization_status || 0);
  const info = offlineInfo(st);
  const head = mk('h3', null, '离线归集纪要');
  head.append(mk('span', 'mtg-badge ' + info.cls, info.text));
  box.append(head);

  if (st === 0) {
    box.append(mk('p', 'hint',
      '把这场会议的录音离线再跑一遍分人与要点，生成按说话人分组的纪要。提交后需要等一会儿。'));
    if (Number(m.is_recording)) {
      box.append(mk('p', 'hint', '这场会议还在录音，先结束后再提交更稳妥。'));
    }
    const acts = mk('div', 'mtg-offline-acts');
    const btn = mk('button', 'btn btn-save', '生成离线归集纪要');
    btn.type = 'button';
    btn.onclick = () => submitMtgOffline(btn);
    acts.append(btn);
    box.append(acts);
    return;
  }

  if (st === 1) {
    box.append(mk('p', 'hint', '已提交，后台正在处理。这里会自动刷新，也可以手动刷新。'));
    const acts = mk('div', 'mtg-offline-acts');
    const btn = mk('button', 'btn', '立即刷新');
    btn.type = 'button';
    btn.onclick = () => mtgRefreshDetail(btn, '立即刷新');
    acts.append(btn);
    box.append(acts);
    return;
  }

  if (st === 2) {
    const summary = data.offline_summary;
    const groups = summary && Array.isArray(summary.groups) ? summary.groups : [];
    if (!groups.length) {
      box.append(mk('p', 'hint', '标记为已完成，但还没读到纪要内容；点上方「刷新」再试一次。'));
      return;
    }
    if (summary.generated_at) box.append(mk('p', 'meta', '生成于 ' + fmtTime(summary.generated_at)));
    const wrap = mk('div', 'mtg-groups');
    groups.forEach((g) => {
      const card = mk('div', 'mtg-group');
      const gh = mk('div', 'mtg-group-head');
      gh.append(mk('b', null, g.name || '发言人'));
      gh.append(mk('span', 'mtg-group-sec',
        fmtSecShort(g.seconds) + (g.spk_id ? ' · ' + g.spk_id : '')));
      card.append(gh);
      const ul = mk('ul', 'mtg-points');
      const points = Array.isArray(g.points) ? g.points : [];
      if (!points.length) ul.append(mk('li', 'muted', '（没有要点）'));
      points.forEach((p) => ul.append(mk('li', null, String(p))));
      card.append(ul);
      wrap.append(card);
    });
    box.append(wrap);
    return;
  }

  box.append(mk('p', 'hint', '未知的归集状态：' + st));
}

async function submitMtgOffline(btn) {
  const sid = mtg.current;
  if (!sid || mtg.submitting) return;
  mtg.submitting = true;
  if (btn) {
    btn.disabled = true;
    btn.textContent = '提交中…';
  }
  try {
    const r = await post('/api/meeting/offline', { session: sid });
    if (!r || !r.ok) throw new Error((r && r.error) || '提交失败');
    toast('已提交，稍后刷新');
    const meeting = (mtg.detail && mtg.detail.meeting) || null;
    if (meeting && meeting.session_id === sid) {
      meeting.offline_diarization_status = Number(r.status != null ? r.status : 1);
      renderMtgOffline(meeting, mtg.detail);
      if (Number(meeting.offline_diarization_status) === 2) return;
    }
    startMtgPoll(sid);
  } catch (e) {
    toast('提交失败：' + e.message, true);
    if (btn) {
      btn.disabled = false;
      btn.textContent = '生成离线归集纪要';
    }
  } finally {
    mtg.submitting = false;
  }
}

async function mtgRefreshDetail(btn, label) {
  const sid = mtg.current;
  if (!sid) return;
  const text = label || '刷新';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '刷新中…';
  }
  await loadMeetingDetail(sid);
  if (btn) {
    btn.disabled = false;
    btn.textContent = text;
  }
}

function meetingBindOnce() {
  if (mtg.bound) return;
  mtg.bound = true;
  if ($('#mtgRefresh')) $('#mtgRefresh').onclick = () => loadMeetings({ toast: true });
  if ($('#mtgReload')) $('#mtgReload').onclick = () => loadMeetings({ toast: true });
  if ($('#mtgBack')) $('#mtgBack').onclick = backToMtgList;
  if ($('#mtgDRefresh')) $('#mtgDRefresh').onclick = () => mtgRefreshDetail($('#mtgDRefresh'), '刷新');
  document.addEventListener('keydown', (e) => {
    if (e.isComposing || e.key !== 'Escape') return;
    const box = $('#meetings');
    const detail = $('#mtgDetailView');
    if (box && !box.hidden && detail && !detail.hidden) backToMtgList();
  });
}

/* ---------------- 绑定 ---------------- */

/* ---------------- 模块 02（旧）：单人对话分析 ----------------
   网页端入口已移除（赛题不需要对外暴露这个页面）；后端 /api/converse/* 仍保留，
   供脚本或其他端调用。下面这段 JS 不再被页面调用，留着是为了不破坏旧接口的演示脚本。 */

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
  const onPreset = name === 'preset';
  const onMeeting = name === 'meeting';
  const showPeople = !onPreset && !onMeeting;
  if ($('#presets')) $('#presets').hidden = !onPreset;
  if ($('#meetings')) $('#meetings').hidden = !onMeeting;
  if ($('#contentPeople')) $('#contentPeople').hidden = !showPeople;
  if ($('#listcol')) $('#listcol').hidden = !showPeople;
  [['#modPeople', showPeople], ['#modPreset', onPreset],
    ['#modMeeting', onMeeting]].forEach(([sel, on]) => {
    const el = $(sel);
    if (!el) return;
    el.classList.toggle('is-active', !!on);
    el.setAttribute('aria-current', on ? 'page' : 'false');
  });
  if (!onMeeting) mtgStopPoll();
  if (onPreset) {
    presetBindOnce();
    loadPresets();
  }
  if (onMeeting) {
    meetingBindOnce();
    loadMeetings();
  }
}

/* ---------------- 预设（按钮 + 总提示词 + 时长） ---------------- */
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
    if ($('#systemPrompt')) $('#systemPrompt').value = cfg.system_prompt || '';
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
        system_prompt: (($('#systemPrompt') || {}).value || '').slice(0, 2000),
      }),
    });
    const cfg = await cfgRes.json();
    if (!cfg.ok) throw new Error(cfg.error || '时长保存失败');
    presets.items = data.items || presets.items;
    renderPresets();
    if ($('#presetMsg')) $('#presetMsg').textContent = '已保存 · 按钮与时长下次启动生效，总提示词立即生效';
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
  if ($('#modPreset')) $('#modPreset').onclick = () => setModule('preset');
  if ($('#modMeeting')) $('#modMeeting').onclick = () => setModule('meeting');
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

/* ---------------- 对眼镜说一句（文本指令） ----------------
   文本和语音共用同一个 agent session：这里发 /api/agent/feed，
   眼镜端用现有轮询把新的卡片刷出来。 */
const cmdBar = { session: '', busy: false };

function cmdSetResult(text) {
  const el = $('#cmdResult');
  if (el) el.textContent = text;
}

async function cmdPoll() {
  const bar = $('#cmdBar');
  if (!bar) return;
  try {
    const d = await (await fetch('/api/latest')).json();
    const fresh = d.kind === 'agent' && d.session
      && Date.now() - Number(d.at || 0) < 30 * 60 * 1000;
    cmdBar.session = fresh ? d.session : '';
    const st = $('#cmdSession');
    const btn = $('#cmdSend');
    if (st) st.textContent = fresh ? ('在线 · ' + d.session) : '待机';
    if (btn) btn.disabled = !cmdBar.session || cmdBar.busy;
    const hint = $('#cmdResult');
    if (fresh && hint && (!hint.textContent || hint.textContent.startsWith('先在眼镜上'))) {
      hint.textContent = '会话在线 · 输入指令或点下面快捷按钮';
    }
    if (!fresh && !cmdBar.busy) {
      cmdSetResult('先在眼镜上按确认开始一段对话，再在这里发指令。');
    }
  } catch (e) {
    const st = $('#cmdSession');
    if (st) st.textContent = '后台没连上';
  }
}

async function cmdSend(text) {
  if (!$('#cmdBar')) return;
  const input = $('#cmdText');
  const msg = String(text != null ? text : (input ? input.value : '')).trim();
  if (!msg) { if (input) input.focus(); return; }
  if (!cmdBar.session) {
    cmdSetResult('当前没有正在进行的会话：先在眼镜上按确认。');
    return;
  }
  if (cmdBar.busy) return;
  cmdBar.busy = true;
  const btn = $('#cmdSend');
  if (btn) btn.disabled = true;
  cmdSetResult('已发送 · 眼镜正在更新…');
  try {
    const res = await fetch('/api/agent/feed', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: cmdBar.session, text: msg, mode: 'single', manual: true }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '发送失败');
    const v = data.view || {};
    const rows = (v.rows || []).slice(0, 2).join(' / ');
    const tag = v.degraded ? '（模型兜底）' : '';
    cmdSetResult([v.state || '完成', v.person || '—', rows].filter(Boolean).join(' · ') + tag);
    if (input) input.value = '';
    toast('已发给眼镜');
  } catch (e) {
    cmdSetResult('发送失败：' + e.message);
    toast('发送失败：' + e.message, true);
  } finally {
    cmdBar.busy = false;
    if (btn) btn.disabled = !cmdBar.session;
  }
}

function cmdBind() {
  const bar = $('#cmdBar');
  if (!bar || bar.__bound) return;
  bar.__bound = true;
  if ($('#cmdSend')) $('#cmdSend').onclick = () => cmdSend();
  if ($('#cmdText')) {
    $('#cmdText').onkeydown = (e) => { if (e.key === 'Enter') cmdSend(); };
  }
  if (typeof bar.querySelectorAll === 'function') {
    bar.querySelectorAll('[data-cmd]').forEach((b) => {
      b.onclick = () => cmdSend(b.getAttribute('data-cmd'));
    });
  }
  cmdPoll();
  setInterval(cmdPoll, 2500);
}

/* ---------------- 启动 ---------------- */

const logoutBtn = document.getElementById('logout');
if (logoutBtn) logoutBtn.onclick = () => { localStorage.removeItem('ml_token'); location.replace('login.html'); };

bind();
cmdBind();
loadPeople();

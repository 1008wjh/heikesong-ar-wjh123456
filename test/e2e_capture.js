const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await startCamera();
const live = state.live;
await capture();
await sleep(900);
return JSON.stringify({
  live,
  frozen: !!state.frozen,
  faces: state.faces.length,
  res: document.querySelectorAll('.res').length,
  lensMsg: document.getElementById('lensMsg').textContent,
  latency: document.getElementById('latency').textContent,
  shotHidden: document.getElementById('btnShot').hidden,
  retakeHidden: document.getElementById('btnRetake').hidden,
});

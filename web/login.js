(function () {
  const $ = (s) => document.querySelector(s);
  const msg = $('#loginMsg');
  const token = localStorage.getItem('ml_token');

  if (token) {
    fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + token } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && d.ok) location.replace('/'); })
      .catch(() => {});
  }

  $('#loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    msg.textContent = '';
    const username = ($('#username').value || '').trim();
    const password = $('#password').value || '';
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || '登录失败');
      localStorage.setItem('ml_token', data.token);
      localStorage.setItem('ml_user', JSON.stringify(data.user || {}));
      location.replace('/');
    } catch (err) {
      msg.textContent = err.message || '登录失败';
    }
  });
})();

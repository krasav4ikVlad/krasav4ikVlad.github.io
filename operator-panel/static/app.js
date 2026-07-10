/* Operator panel SPA — vanilla JS, no build step. */
'use strict';

// ================================================================ state & api

const S = {
  token: localStorage.getItem('op_token') || null,
  me: null, // {id, login, name, role}
};

const $view = document.getElementById('view');
const $topbar = document.getElementById('topbar');

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (S.token) headers['Authorization'] = 'Bearer ' + S.token;
  if (opts.body && typeof opts.body !== 'string') {
    headers['Content-Type'] = 'application/json';
    opts = { ...opts, body: JSON.stringify(opts.body) };
  }
  let resp;
  try {
    resp = await fetch(path, { ...opts, headers });
  } catch (e) {
    throw new ApiError('Нет соединения с сервером', 0);
  }
  if (resp.status === 401) {
    logout(false);
    throw new ApiError('Сессия истекла — войдите заново', 401);
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* empty body */ }
  if (!resp.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map(e => e.msg || JSON.stringify(e)).join('\n');
    throw new ApiError(msg || `Ошибка ${resp.status}`, resp.status);
  }
  return data;
}

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

// ================================================================ helpers

const GB = 1024 ** 3;

function fmtBytes(b) {
  if (b == null) return '—';
  return (b / GB).toFixed(2).replace(/\.00$/, '') + ' ГБ';
}

function fmtNum(n) {
  const v = Number(n);
  return isNaN(v) ? esc(n) : v.toLocaleString('ru-RU');
}

function parseTs(v) {
  if (!v) return null;
  if (/^\d{2}\.\d{2}\.\d{4}/.test(v)) {
    const [d, t] = v.split(' ');
    const [dd, mm, yy] = d.split('.');
    return new Date(`${yy}-${mm}-${dd}T${t || '00:00:00'}Z`);
  }
  const dt = new Date(v);
  return isNaN(dt) ? null : dt;
}

function fmtDate(v) {
  const dt = parseTs(v);
  if (!dt) return v ? esc(v) : '—';
  return dt.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function subBadge(expireAt) {
  const dt = parseTs(expireAt);
  if (!dt) return '<span class="badge badge-gray">нет подписки</span>';
  const days = Math.floor((dt - Date.now()) / 86400000);
  if (days < 0) return `<span class="badge badge-red">истекла ${fmtDate(expireAt)}</span>`;
  if (days <= 3) return `<span class="badge badge-yellow">истекает через ${days} дн.</span>`;
  return `<span class="badge badge-green">активна до ${fmtDate(expireAt)} (${days} дн.)</span>`;
}

const PERM_LABELS = {
  balance_change: 'Изменение баланса',
  subscription_expire_change: 'Срок подписки',
  device_limit_change: 'Лимит устройств',
  bypass_update: 'ByPass (трафик)',
  device_reset: 'Отвязка устройств',
  tickets: 'Тикеты (ответы и закрытие)',
};

function can(key) {
  if (!S.me) return false;
  if (S.me.role === 'owner') return true;
  return !!(S.me.permissions && S.me.permissions[key]);
}

function toast(msg, kind = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast toast-' + kind;
  el.textContent = msg;
  document.getElementById('toast-root').appendChild(el);
  setTimeout(() => el.remove(), kind === 'err' ? 8000 : 4000);
}

function spinnerHtml(text = 'Загрузка…') {
  return `<div class="center"><span class="spinner"></span> ${esc(text)}</div>`;
}

// ================================================================ modal framework

function openModal(html) {
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="modal-overlay"><div class="modal">${html}</div></div>`;
  root.querySelector('.modal-overlay').addEventListener('click', e => {
    if (e.target.classList.contains('modal-overlay')) closeModal();
  });
  return root.querySelector('.modal');
}

function closeModal() {
  document.getElementById('modal-root').innerHTML = '';
}

/**
 * Two-step action modal: form -> confirmation (old -> new) -> POST.
 * cfg: { title, fieldsHtml, buildRequest($modal) -> {body, confirmHtml, danger},
 *        url, onDone, supportsForceLocal }
 */
function actionModal(cfg) {
  const $m = openModal(`
    <h2>${esc(cfg.title)}</h2>
    <form id="act-form">${cfg.fieldsHtml}
      <div class="field">
        <label>Причина (обязательно, попадает в аудит-лог)</label>
        <textarea name="reason" rows="2" required minlength="3" maxlength="500"
          placeholder="Например: возврат за ошибочное продление по обращению #123"></textarea>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" id="act-cancel">Отмена</button>
        <button type="submit" class="btn">Далее</button>
      </div>
    </form>`);
  $m.querySelector('#act-cancel').onclick = closeModal;

  $m.querySelector('#act-form').addEventListener('submit', e => {
    e.preventDefault();
    let req;
    try {
      req = cfg.buildRequest($m);
    } catch (err) {
      toast(err.message, 'err');
      return;
    }
    req.body.reason = $m.querySelector('[name=reason]').value.trim();
    showConfirmStep(cfg, req);
  });
}

function showConfirmStep(cfg, req, remnaError = null) {
  const $m = openModal(`
    <h2>Подтверждение</h2>
    <div class="confirm-box">${req.confirmHtml}</div>
    <div><span class="muted">Причина:</span> ${esc(req.body.reason)}</div>
    ${remnaError ? `<div class="error-note">${esc(remnaError)}</div>
      <label style="margin-top:10px; display:flex; gap:8px; align-items:center; color:var(--text)">
        <input type="checkbox" id="force-local" style="width:auto"> Применить только в базе (без синхронизации с нодами)
      </label>` : ''}
    ${req.danger ? '<div class="warn-note">Действие необратимо. Проверьте данные перед подтверждением.</div>' : ''}
    <div class="modal-actions">
      <button class="btn btn-ghost" id="c-back">Назад</button>
      <button class="btn ${req.danger ? 'btn-danger' : ''}" id="c-ok">Подтвердить</button>
    </div>`);
  $m.querySelector('#c-back').onclick = closeModal;
  $m.querySelector('#c-ok').onclick = async () => {
    const btn = $m.querySelector('#c-ok');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
    const body = { ...req.body };
    if (remnaError && $m.querySelector('#force-local')?.checked) body.force_local = true;
    try {
      const res = await api(cfg.url, { method: 'POST', body });
      closeModal();
      if (res && res.remnawave_synced === false) {
        toast('Применено в базе, но НЕ синхронизировано с Remnawave: ' + (res.remnawave_error || ''), 'err');
      } else {
        toast('Готово ✓');
      }
      cfg.onDone && cfg.onDone(res);
    } catch (err) {
      if (err.status === 502 && cfg.supportsForceLocal) {
        showConfirmStep(cfg, req, err.message);
      } else if (err.status === 401) {
        closeModal();
      } else {
        btn.disabled = false;
        btn.textContent = 'Подтвердить';
        toast(err.message, 'err');
      }
    }
  };
}

// ================================================================ auth

function logout(redirect = true) {
  S.token = null;
  S.me = null;
  localStorage.removeItem('op_token');
  if (redirect) location.hash = '#/login';
  render();
}

function currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

function applyTheme(theme) {
  if (theme === 'dark') document.documentElement.dataset.theme = 'dark';
  else delete document.documentElement.dataset.theme;
  localStorage.setItem('op_theme', theme);
  const meta = document.querySelector('meta[name=theme-color]');
  if (meta) meta.content = theme === 'dark' ? '#0f0f0e' : '#f2f1ec';
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема';
}

document.getElementById('theme-btn').onclick = () =>
  applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
applyTheme(currentTheme());

document.getElementById('logout-btn').onclick = () => logout();

async function loadMe() {
  if (!S.token) return false;
  try {
    S.me = await api('/api/auth/me');
    return true;
  } catch (e) {
    return false;
  }
}

function viewLogin() {
  $topbar.classList.add('hidden');
  $view.innerHTML = `
    <div class="login-wrap"><div class="card login-card">
      <h1>Панель оператора</h1>
      <div class="login-sub">поддержка · управление пользователями</div>
      <form id="login-form">
        <div class="field"><label>Логин</label><input name="login" required autocomplete="username"></div>
        <div class="field"><label>Пароль</label><input name="password" type="password" required autocomplete="current-password"></div>
        <button class="btn" style="width:100%" type="submit">Войти</button>
        <div class="error-note hidden" id="login-err"></div>
      </form>
      <div style="text-align:center; margin-top:14px">
        <a href="#" id="login-theme" class="muted" style="font-size:13px"></a>
      </div>
    </div></div>`;
  const themeLink = document.getElementById('login-theme');
  themeLink.textContent = currentTheme() === 'dark' ? 'Светлая тема' : 'Тёмная тема';
  themeLink.onclick = e => {
    e.preventDefault();
    applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    themeLink.textContent = currentTheme() === 'dark' ? 'Светлая тема' : 'Тёмная тема';
  };
  document.getElementById('login-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    const errEl = document.getElementById('login-err');
    errEl.classList.add('hidden');
    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: { login: f.login.value.trim(), password: f.password.value },
      });
      S.token = data.access_token;
      S.me = data.operator;
      localStorage.setItem('op_token', S.token);
      if (S.me.must_change_password) S.pendingPwd = f.password.value;
      location.hash = S.nextHash && !S.nextHash.startsWith('#/login') ? S.nextHash : '#/search';
      S.nextHash = null;
      render();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

// ================================================================ forced password change

function viewForcePassword() {
  $topbar.classList.add('hidden');
  $view.innerHTML = `
    <div class="login-wrap"><div class="card login-card">
      <h1>Новый пароль</h1>
      <div class="login-sub">вы вошли с временным паролем</div>
      <div class="muted" style="margin-bottom:16px; font-size:13px">
        Придумайте свой пароль (минимум 8 символов). Временный пароль после этого перестанет действовать.</div>
      <form id="pwd-form">
        ${S.pendingPwd ? '' : `<div class="field"><label>Временный пароль</label>
          <input name="current" type="password" required autocomplete="current-password"></div>`}
        <div class="field"><label>Новый пароль</label>
          <input name="pwd1" type="password" required minlength="8" autocomplete="new-password"></div>
        <div class="field"><label>Ещё раз</label>
          <input name="pwd2" type="password" required minlength="8" autocomplete="new-password"></div>
        <button class="btn" style="width:100%" type="submit">Сохранить и продолжить</button>
        <div class="error-note hidden" id="pwd-err"></div>
      </form>
      <div style="text-align:center; margin-top:14px">
        <a href="#" id="pwd-logout" class="muted" style="font-size:13px">Выйти</a>
      </div>
    </div></div>`;
  document.getElementById('pwd-logout').onclick = e => { e.preventDefault(); logout(); };
  document.getElementById('pwd-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    const errEl = document.getElementById('pwd-err');
    errEl.classList.add('hidden');
    if (f.pwd1.value !== f.pwd2.value) {
      errEl.textContent = 'Пароли не совпадают';
      errEl.classList.remove('hidden');
      return;
    }
    try {
      const me = await api('/api/auth/change-password', {
        method: 'POST',
        body: {
          current_password: S.pendingPwd || f.current.value,
          new_password: f.pwd1.value,
        },
      });
      S.me = me;
      S.pendingPwd = null;
      toast('Пароль установлен ✓');
      location.hash = S.nextHash && !S.nextHash.startsWith('#/login') ? S.nextHash : '#/search';
      S.nextHash = null;
      render();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

// ================================================================ search view

function viewSearch() {
  $view.innerHTML = `
    <h1>Поиск пользователя</h1>
    <div class="card">
      <form id="search-form" class="row">
        <input name="q" placeholder="user_id, @username или email" required minlength="1" style="flex:3" autofocus>
        <button class="btn" type="submit" style="flex:0 0 auto">Найти</button>
      </form>
      <div id="search-results" style="margin-top:16px"></div>
    </div>`;
  const $res = document.getElementById('search-results');
  document.getElementById('search-form').addEventListener('submit', async e => {
    e.preventDefault();
    const q = e.target.q.value.trim();
    $res.innerHTML = spinnerHtml('Ищем…');
    try {
      const data = await api('/api/users/search?q=' + encodeURIComponent(q));
      if (!data.results.length) {
        $res.innerHTML = '<div class="center">Ничего не найдено. Проверьте user_id / username / email.</div>';
        return;
      }
      $res.innerHTML = `<div class="table-wrap"><table>
        <tr><th>user_id</th><th>Username</th><th>Имя</th><th>Баланс</th><th>Подписка</th><th>Email</th><th>Сегмент</th></tr>
        ${data.results.map(u => `
          <tr style="cursor:pointer" onclick="location.hash='#/user/${u.user_id}'">
            <td class="mono">${esc(u.user_id)}</td>
            <td>${u.username ? '@' + esc(u.username) : '—'}</td>
            <td>${esc(u.first_name || '—')}</td>
            <td>${fmtNum(u.balance)} ₽</td>
            <td>${subBadge(u.expire_at)}</td>
            <td>${esc(u.email || '—')}</td>
            <td>${esc(u.segment || '—')}</td>
          </tr>`).join('')}
      </table></div>`;
    } catch (err) {
      $res.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
    }
  });
}

// ================================================================ user card view

async function viewUser(userId) {
  $view.innerHTML = spinnerHtml('Загружаем карточку…');
  let u;
  try {
    u = await api('/api/users/' + userId);
  } catch (err) {
    $view.innerHTML = `<div class="card"><div class="error-note">${esc(err.message)}</div>
      <div style="margin-top:12px"><a href="#/search" class="btn btn-ghost">← К поиску</a></div></div>`;
    return;
  }
  const ud = u.user_data || {};
  const vpn = u.vpn || {};
  const reload = () => viewUser(userId);

  $view.innerHTML = `
    <div style="margin-bottom:12px"><a href="#/search" class="muted" style="text-decoration:none">← К поиску</a></div>
    <div class="card">
      <div class="user-header">
        <div>
          <h1 style="margin-bottom:4px">${esc(ud.first_name || 'Без имени')} ${ud.username ? '<span class="muted">@' + esc(ud.username) + '</span>' : ''}</h1>
          <div class="muted mono">user_id: ${esc(ud.user_id)} · регистрация: ${fmtDate(ud.date_joined)}
            ${ud.utm ? '· utm: ' + esc(ud.utm) : ''}</div>
        </div>
        <div>${subBadge(vpn.expireAt)}</div>
      </div>

      <div class="stats-grid">
        <div class="stat"><div class="stat-label">Баланс</div><div class="stat-value">${fmtNum(u.balance)} ₽</div></div>
        <div class="stat"><div class="stat-label">Подписка до</div><div class="stat-value">${fmtDate(vpn.expireAt)}</div></div>
        <div class="stat"><div class="stat-label">Лимит устройств</div><div class="stat-value">${esc(vpn.hwidDeviceLimit ?? '—')}</div></div>
        <div class="stat"><div class="stat-label">ByPass трафик</div><div class="stat-value">${fmtBytes(vpn.bypass_trafficLimitBytes)}</div></div>
        <div class="stat"><div class="stat-label">Реф. баланс</div><div class="stat-value">${fmtNum(u.ref_withdrawable)} ₽</div></div>
        <div class="stat"><div class="stat-label">Email</div><div class="stat-value" style="font-size:14px">${esc(u.email || '—')}</div></div>
        <div class="stat"><div class="stat-label">Сегмент</div><div class="stat-value" style="font-size:14px">${esc((u.growth || {}).segment || '—')}</div></div>
      </div>

      <div class="actions-bar">
        ${can('balance_change') ? '<button class="btn" id="act-balance">Баланс</button>' : ''}
        ${can('subscription_expire_change') ? '<button class="btn" id="act-expire">Срок подписки</button>' : ''}
        ${can('device_limit_change') ? '<button class="btn" id="act-devlimit">Лимит устройств</button>' : ''}
        ${can('bypass_update') ? '<button class="btn" id="act-bypass">ByPass</button>' : ''}
        ${can('device_reset') ? '<button class="btn btn-danger" id="act-devreset">Отвязать все устройства</button>' : ''}
      </div>
    </div>

    <div class="tabs" id="tabs">
      <button data-tab="logs" class="active">История действий</button>
      <button data-tab="transactions">Транзакции и баланс</button>
      <button data-tab="bypass">Покупки ByPass</button>
      <button data-tab="referrals">Рефералы</button>
      <button data-tab="devices">Устройства</button>
    </div>
    <div class="card" id="tab-content"></div>`;

  // -------- action buttons
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on('act-balance', () => modalBalance(userId, u, reload));
  on('act-expire', () => modalExpire(userId, vpn, reload));
  on('act-devlimit', () => modalDeviceLimit(userId, vpn, reload));
  on('act-bypass', () => modalBypass(userId, vpn, reload));
  on('act-devreset', () => modalDeviceReset(userId, reload));

  // -------- tabs
  const $tabs = document.getElementById('tabs');
  $tabs.addEventListener('click', e => {
    const btn = e.target.closest('button[data-tab]');
    if (!btn) return;
    $tabs.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
    renderTab(btn.dataset.tab, userId);
  });
  renderTab('logs', userId);
}

// ---------------------------------------------------------------- tabs

async function renderTab(tab, userId) {
  const $c = document.getElementById('tab-content');
  if (tab === 'logs') return tabLogs($c, userId);
  if (tab === 'transactions') return tabTransactions($c, userId);
  if (tab === 'bypass') return tabBypass($c, userId);
  if (tab === 'referrals') return tabReferrals($c, userId);
  if (tab === 'devices') return tabDevices($c, userId);
}

function filterBarHtml(withAction) {
  return `<div class="filter-bar">
    <input name="f-search" placeholder="Поиск по тексту…">
    ${withAction ? '<input name="f-action" placeholder="Тип действия…">' : ''}
    <input name="f-from" type="date" title="С даты">
    <input name="f-to" type="date" title="По дату">
    <button class="btn btn-sm" id="f-apply">Применить</button>
  </div>`;
}

function readFilters($c) {
  const v = n => { const el = $c.querySelector(`[name=${n}]`); return el ? el.value.trim() : ''; };
  return { search: v('f-search'), action: v('f-action'), from: v('f-from'), to: v('f-to') };
}

function pagerHtml(data) {
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  return `<div class="pager">
    <span>${data.total} записей · стр. ${data.page}/${pages}</span>
    <button class="btn btn-ghost btn-sm" id="pg-prev" ${data.page <= 1 ? 'disabled' : ''}>←</button>
    <button class="btn btn-ghost btn-sm" id="pg-next" ${data.page >= pages ? 'disabled' : ''}>→</button>
  </div>`;
}

async function tabLogs($c, userId, page = 1) {
  const prev = $c.dataset.tabInit === 'logs' ? readFilters($c) : { search: '', action: '', from: '', to: '' };
  $c.dataset.tabInit = 'logs';
  const qs = new URLSearchParams({ page, page_size: 50 });
  if (prev.search) qs.set('search', prev.search);
  if (prev.action) qs.set('action_type', prev.action);
  if (prev.from) qs.set('date_from', prev.from);
  if (prev.to) qs.set('date_to', prev.to);

  if (!$c.querySelector('#logs-list')) {
    $c.innerHTML = `<h2>История действий</h2>${filterBarHtml(true)}<div id="logs-list"></div>`;
    ['f-search', 'f-action', 'f-from', 'f-to'].forEach(n => {
      const el = $c.querySelector(`[name=${n}]`);
      if (el && prev[n.slice(2)]) el.value = prev[n.slice(2)];
    });
  }
  $c.querySelector('#f-apply').onclick = () => tabLogs($c, userId, 1);
  const $list = $c.querySelector('#logs-list');
  $list.innerHTML = spinnerHtml();
  try {
    const data = await api(`/api/users/${userId}/logs?` + qs);
    $list.innerHTML = data.items.length ? `<div class="table-wrap"><table>
      <tr><th>Время</th><th>Действие</th><th>Детали</th></tr>
      ${data.items.map(l => `<tr>
        <td class="mono" style="white-space:nowrap">${fmtDate(l.timestamp)}</td>
        <td>${esc(l.action)}</td><td class="mono">${esc(l.details || '')}</td></tr>`).join('')}
    </table></div>` + pagerHtml(data) : '<div class="center">Записей нет</div>';
    const pv = $list.querySelector('#pg-prev'), nx = $list.querySelector('#pg-next');
    if (pv) pv.onclick = () => tabLogs($c, userId, data.page - 1);
    if (nx) nx.onclick = () => tabLogs($c, userId, data.page + 1);
  } catch (err) {
    $list.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
  }
}

async function tabTransactions($c, userId, page = 1) {
  const prev = $c.dataset.tabInit === 'tx' ? readFilters($c) : { search: '', from: '', to: '' };
  $c.dataset.tabInit = 'tx';
  const qs = new URLSearchParams({ page, page_size: 50 });
  if (prev.search) qs.set('search', prev.search);
  if (prev.from) qs.set('date_from', prev.from);
  if (prev.to) qs.set('date_to', prev.to);

  if (!$c.querySelector('#tx-list')) {
    $c.innerHTML = `<h2>Транзакции и движение баланса</h2>${filterBarHtml(false)}<div id="tx-list"></div>`;
    ['f-search', 'f-from', 'f-to'].forEach(n => {
      const el = $c.querySelector(`[name=${n}]`);
      if (el && prev[n.slice(2)]) el.value = prev[n.slice(2)];
    });
  }
  $c.querySelector('#f-apply').onclick = () => tabTransactions($c, userId, 1);
  const $list = $c.querySelector('#tx-list');
  $list.innerHTML = spinnerHtml();
  try {
    const data = await api(`/api/users/${userId}/transactions?` + qs);
    const bl = data.balance_log, tx = data.transactions;
    $list.innerHTML = `
      <h2 style="margin-top:8px">Движение баланса (logs_balance) · ${bl.total}</h2>
      ${bl.items.length ? `<div class="table-wrap"><table>
        <tr><th>Время</th><th>Сумма</th><th>Детали</th></tr>
        ${bl.items.map(l => {
          const amt = Number(l.amount) || 0;
          return `<tr><td class="mono" style="white-space:nowrap">${fmtDate(l.timestamp)}</td>
            <td class="${amt >= 0 ? 'amount-pos' : 'amount-neg'}">${amt >= 0 ? '+' : ''}${esc(l.amount)}</td>
            <td>${esc(l.details || '')}</td></tr>`;
        }).join('')}
      </table></div>` + pagerHtml(bl) : '<div class="center">Записей нет</div>'}
      <h2 style="margin-top:24px">Транзакции · ${tx.total}</h2>
      ${tx.items.length ? `<div class="table-wrap"><table>
        <tr><th>Данные</th></tr>
        ${tx.items.map(t => `<tr><td class="mono" style="white-space:pre-wrap">${esc(JSON.stringify(t, null, 1))}</td></tr>`).join('')}
      </table></div>` : '<div class="center">Записей нет</div>'}`;
    const pv = $list.querySelector('#pg-prev'), nx = $list.querySelector('#pg-next');
    if (pv) pv.onclick = () => tabTransactions($c, userId, bl.page - 1);
    if (nx) nx.onclick = () => tabTransactions($c, userId, bl.page + 1);
  } catch (err) {
    $list.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
  }
}

async function tabBypass($c, userId) {
  $c.dataset.tabInit = 'bypass';
  $c.innerHTML = `<h2>Покупки ByPass</h2>` + spinnerHtml();
  try {
    const data = await api(`/api/users/${userId}/bypass-purchases`);
    $c.innerHTML = `<h2>Покупки ByPass · ${data.total}</h2>` + (data.items.length ? `
      <div class="table-wrap"><table>
        <tr><th>Дата</th><th>Объём</th><th>Цена</th></tr>
        ${data.items.map(p => `<tr>
          <td class="mono">${fmtDate(p.created_at)}</td>
          <td>${esc(p.amount_gb)} ГБ</td>
          <td>${esc(p.price)} ₽</td></tr>`).join('')}
      </table></div>` : '<div class="center">Покупок нет</div>');
  } catch (err) {
    $c.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
  }
}

async function tabReferrals($c, userId) {
  $c.dataset.tabInit = 'ref';
  $c.innerHTML = `<h2>Реферальная статистика</h2>` + spinnerHtml();
  try {
    const d = await api(`/api/users/${userId}/referrals`);
    $c.innerHTML = `<h2>Реферальная статистика</h2>
      <div class="stats-grid" style="margin-bottom:16px">
        <div class="stat"><div class="stat-label">Доступно к выводу</div><div class="stat-value">${fmtNum(d.withdrawable)} ₽</div></div>
        <div class="stat"><div class="stat-label">Заработано всего</div><div class="stat-value">${fmtNum(d.earned_total)} ₽</div></div>
        <div class="stat"><div class="stat-label">Рефералов</div><div class="stat-value">${(d.referrals || []).length}</div></div>
        <div class="stat"><div class="stat-label">Пригласил</div><div class="stat-value" style="font-size:14px">${esc(d.referrer || '—')}</div></div>
      </div>
      ${(d.referrals || []).length ? `<div class="table-wrap"><table><tr><th>Реферал</th></tr>
        ${d.referrals.map(r => `<tr><td class="mono" style="white-space:pre-wrap">${esc(typeof r === 'object' ? JSON.stringify(r) : r)}</td></tr>`).join('')}
      </table></div>` : '<div class="center">Рефералов нет</div>'}`;
  } catch (err) {
    $c.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
  }
}

async function tabDevices($c, userId) {
  $c.dataset.tabInit = 'dev';
  $c.innerHTML = `<h2>Устройства (из Remnawave)</h2>` + spinnerHtml();
  try {
    const d = await api(`/api/users/${userId}/devices`);
    $c.innerHTML = `<h2>Устройства (из Remnawave) · лимит: ${esc(d.limit ?? '—')}</h2>
      ${d.warning ? `<div class="warn-note">${esc(d.warning)}</div>` : ''}
      ${(d.devices || []).length ? `<div class="table-wrap"><table>
        <tr><th>Платформа</th><th>Модель</th><th></th></tr>
        ${d.devices.map(dev => `<tr>
          <td>${esc(dev.platform || '—')}</td>
          <td>${esc(dev.deviceModel || dev.model || '—')}</td>
          <td>${can('device_reset') ? `<button class="btn btn-danger btn-sm" data-hwid="${esc(dev.hwid || '')}">Отвязать</button>` : ''}</td>
        </tr>`).join('')}
      </table></div>` : '<div class="center">Привязанных устройств нет</div>'}
      ${(d.extra_devices || []).length ? `<h2 style="margin-top:16px">Доп. устройства (extraDevices)</h2>
        <div class="mono" style="white-space:pre-wrap">${esc(JSON.stringify(d.extra_devices, null, 1))}</div>` : ''}`;
    $c.querySelectorAll('button[data-hwid]').forEach(btn => {
      btn.onclick = () => modalDeviceReset(userId, () => tabDevices($c, userId), btn.dataset.hwid);
    });
  } catch (err) {
    $c.innerHTML = `<h2>Устройства</h2><div class="error-note">${esc(err.message)}</div>`;
  }
}

// ---------------------------------------------------------------- action modals

function modalBalance(userId, u, onDone) {
  actionModal({
    title: `Изменить баланс (сейчас ${u.balance} ₽)`,
    url: `/api/users/${userId}/balance`,
    onDone,
    fieldsHtml: `
      <div class="field"><label>Операция</label>
        <select name="dir"><option value="+">Начислить</option><option value="-">Списать</option></select></div>
      <div class="field"><label>Сумма, ₽</label>
        <input name="amount" type="number" step="0.01" min="0.01" required placeholder="Например: 199"></div>
      <div class="muted" style="font-size:12.5px">Баланс не может уйти в минус — списание больше остатка будет отклонено.</div>`,
    buildRequest($m) {
      const dir = $m.querySelector('[name=dir]').value;
      const amount = parseFloat($m.querySelector('[name=amount]').value);
      if (!amount || amount <= 0) throw new Error('Введите сумму больше нуля');
      const signed = dir === '-' ? -amount : amount;
      if (dir === '-' && amount > u.balance) throw new Error(`Списание ${amount} ₽ больше баланса ${u.balance} ₽ — в минус нельзя`);
      return {
        body: { amount: signed },
        danger: dir === '-',
        confirmHtml: `Баланс: <b>${esc(u.balance)} ₽</b><span class="arrow">→</span><b>${esc(Math.round((u.balance + signed) * 100) / 100)} ₽</b>
          <div class="muted">(${dir === '-' ? 'списание' : 'начисление'} ${amount} ₽)</div>`,
      };
    },
  });
}

function modalExpire(userId, vpn, onDone) {
  actionModal({
    title: 'Срок подписки',
    url: `/api/users/${userId}/subscription/expire`,
    onDone,
    supportsForceLocal: true,
    fieldsHtml: `
      <div class="muted" style="margin-bottom:12px">Сейчас: <b>${fmtDate(vpn.expireAt)}</b><br>
        Срок ByPass всегда равен сроку подписки и изменится вместе с ней.</div>
      <div class="field"><label>Режим</label>
        <select name="mode">
          <option value="add">Продлить на N дней</option>
          <option value="sub">Сократить на N дней</option>
          <option value="date">Задать точную дату</option>
        </select></div>
      <div class="field" id="days-field"><label>Дней</label>
        <input name="days" type="number" min="1" max="3650" placeholder="30"></div>
      <div class="field hidden" id="date-field"><label>Дата окончания</label>
        <input name="date" type="datetime-local"></div>`,
    buildRequest($m) {
      const mode = $m.querySelector('[name=mode]').value;
      if (mode === 'date') {
        const v = $m.querySelector('[name=date]').value;
        if (!v) throw new Error('Выберите дату');
        const iso = new Date(v).toISOString();
        return {
          body: { expire_at: iso },
          danger: parseTs(vpn.expireAt) && new Date(iso) < parseTs(vpn.expireAt),
          confirmHtml: `Подписка: <b>${fmtDate(vpn.expireAt)}</b><span class="arrow">→</span><b>${fmtDate(iso)}</b>`,
        };
      }
      const n = parseInt($m.querySelector('[name=days]').value, 10);
      if (!n || n < 1) throw new Error('Введите число дней');
      const days = mode === 'sub' ? -n : n;
      return {
        body: { days },
        danger: days < 0,
        confirmHtml: `Подписка: <b>${fmtDate(vpn.expireAt)}</b><span class="arrow">→</span>
          <b>${days > 0 ? '+' : ''}${days} дн.</b>`,
      };
    },
  });
  // toggle days/date fields
  const $m = document.querySelector('.modal');
  $m.querySelector('[name=mode]').addEventListener('change', e => {
    const isDate = e.target.value === 'date';
    $m.querySelector('#days-field').classList.toggle('hidden', isDate);
    $m.querySelector('#date-field').classList.toggle('hidden', !isDate);
  });
}

function modalDeviceLimit(userId, vpn, onDone) {
  const current = vpn.hwidDeviceLimit;
  actionModal({
    title: `Лимит устройств (сейчас ${current ?? '—'})`,
    url: `/api/users/${userId}/subscription/device-limit`,
    onDone,
    supportsForceLocal: true,
    fieldsHtml: `
      <div class="muted" style="margin-bottom:12px">Лимит можно только уменьшать — увеличение покупается в боте.</div>
      <div class="field"><label>Новый лимит (меньше ${esc(current ?? '—')})</label>
      <input name="limit" type="number" min="0" max="${current != null ? current - 1 : 1000}" required placeholder="Например: ${current != null ? current - 1 : 5}"></div>`,
    buildRequest($m) {
      const limit = parseInt($m.querySelector('[name=limit]').value, 10);
      if (isNaN(limit) || limit < 0) throw new Error('Введите корректный лимит');
      if (current == null) throw new Error('У пользователя не задан лимит устройств — менять нечего');
      if (limit >= current) throw new Error(`Лимит можно только уменьшать (сейчас ${current})`);
      return {
        body: { limit },
        danger: true,
        confirmHtml: `Лимит устройств: <b>${esc(current)}</b><span class="arrow">→</span><b>${limit}</b>`,
      };
    },
  });
}

function modalBypass(userId, vpn, onDone) {
  actionModal({
    title: 'ByPass: лимит трафика',
    url: `/api/users/${userId}/bypass`,
    onDone,
    supportsForceLocal: true,
    fieldsHtml: `
      <div class="muted" style="margin-bottom:12px">
        Сейчас: <b>${fmtBytes(vpn.bypass_trafficLimitBytes)}</b><br>
        Срок ByPass равен сроку подписки и меняется через «Срок подписки».</div>
      <div class="field"><label>Добавить трафика, ГБ (±)</label>
        <input name="add_gb" type="number" step="0.1" placeholder="5 или -5"></div>
      <div class="field"><label>ИЛИ задать лимит точно, ГБ</label>
        <input name="abs_gb" type="number" step="0.1" min="0" placeholder="608"></div>`,
    buildRequest($m) {
      const addGb = $m.querySelector('[name=add_gb]').value.trim();
      const absGb = $m.querySelector('[name=abs_gb]').value.trim();
      if (addGb && absGb) throw new Error('Укажите либо прибавку, либо точный лимит — не оба');
      if (!addGb && !absGb) throw new Error('Нет изменений');
      const body = {};
      const parts = [];
      if (addGb) { body.add_traffic_gb = parseFloat(addGb); parts.push(`трафик ${body.add_traffic_gb > 0 ? '+' : ''}${body.add_traffic_gb} ГБ`); }
      if (absGb) { body.traffic_limit_gb = parseFloat(absGb); parts.push(`лимит = ${body.traffic_limit_gb} ГБ`); }
      const danger = (body.add_traffic_gb || 0) < 0 ||
        (body.traffic_limit_gb != null && body.traffic_limit_gb * GB < (vpn.bypass_trafficLimitBytes || 0));
      return {
        body, danger,
        confirmHtml: `ByPass: <b>${esc(parts.join(', '))}</b>
          <div class="muted">Было: ${fmtBytes(vpn.bypass_trafficLimitBytes)}</div>`,
      };
    },
  });
}

function modalDeviceReset(userId, onDone, hwid = null) {
  actionModal({
    title: hwid ? 'Отвязать устройство' : 'Отвязать ВСЕ устройства',
    url: `/api/users/${userId}/devices/reset`,
    onDone,
    fieldsHtml: hwid
      ? `<div class="muted" style="margin-bottom:12px">Будет отвязано выбранное устройство.</div>`
      : `<div class="warn-note" style="margin-bottom:12px">Будут отвязаны все HWID-привязки пользователя в Remnawave.
         Пользователю придётся заново подключить свои устройства.</div>`,
    buildRequest() {
      return {
        body: hwid ? { hwid } : {},
        danger: true,
        confirmHtml: hwid
          ? `Отвязать выбранное устройство пользователя ${esc(userId)}`
          : `Отвязать <b>ВСЕ</b> устройства пользователя ${esc(userId)}`,
      };
    },
  });
}

// ================================================================ tickets

const TICKET_BADGES = {
  pending: '<span class="badge badge-yellow">🟡 ожидает</span>',
  open: '<span class="badge badge-green">🟢 оператор</span>',
  closed: '<span class="badge badge-red">🔴 закрыт</span>',
};
function ticketBadge(st) { return TICKET_BADGES[st] || `<span class="badge badge-gray">${esc(st)}</span>`; }

function viewTickets() {
  if (!S.tk) S.tk = { status: 'open', page: 1, sort: 'pending_at', order: 'desc' };
  const tk = S.tk;
  $view.innerHTML = `
    <h1>Тикеты поддержки</h1>
    <div class="card">
      <div class="filter-bar">
        <select id="tk-filter">
          <option value="" ${tk.status === '' ? 'selected' : ''}>Все статусы</option>
          <option value="pending" ${tk.status === 'pending' ? 'selected' : ''}>🟡 Ожидают</option>
          <option value="open" ${tk.status === 'open' ? 'selected' : ''}>🟢 У оператора</option>
          <option value="closed" ${tk.status === 'closed' ? 'selected' : ''}>🔴 Закрытые</option>
        </select>
        <span class="muted" style="align-self:center; font-size:12px" id="tk-updated"></span>
      </div>
      <div id="tk-list">${spinnerHtml()}</div>
    </div>`;
  document.getElementById('tk-filter').onchange = e => {
    tk.status = e.target.value;
    tk.page = 1;
    loadTickets();
  };
  loadTickets();
  // автообновление списка — можно сидеть и ждать новые тикеты
  S.ticketTimer = setInterval(() => loadTickets(true), 5000);
}

const TK_SORT_LABELS = [
  ['status', 'Статус'],
  ['user', 'Пользователь'],
  ['pending_at', 'Дата обращения'],
];

async function loadTickets(silent = false) {
  const tk = S.tk;
  const $list = document.getElementById('tk-list');
  if (!$list) return;
  if (!silent) $list.innerHTML = spinnerHtml();
  const qs = new URLSearchParams({
    page: tk.page, page_size: 30, sort: tk.sort, order: tk.order,
  });
  if (tk.status) qs.set('status', tk.status);
  let data;
  try {
    data = await api('/api/tickets?' + qs);
  } catch (err) {
    if (!silent) $list.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
    return;
  }
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  if (tk.page > pages) { tk.page = pages; return loadTickets(silent); }

  const arrow = key => tk.sort === key ? (tk.order === 'desc' ? ' ↓' : ' ↑') : '';
  const ths = TK_SORT_LABELS.map(([key, label]) =>
    `<th class="th-sort" data-sort="${key}">${label}${arrow(key)}</th>`).join('');

  $list.innerHTML = data.items.length ? `<div class="table-wrap"><table>
    <tr>${ths}<th>Последнее сообщение</th></tr>
    ${data.items.map(t => `
      <tr style="cursor:pointer" onclick="location.hash='#/ticket/${t.user_id}'">
        <td>${ticketBadge(t.status)}</td>
        <td>${esc(t.first_name || '—')} ${t.username ? '<span class="muted">@' + esc(t.username) + '</span>' : ''}
          <div class="mono muted" style="font-size:11.5px">${esc(t.user_id)}</div></td>
        <td class="mono" style="white-space:nowrap">${fmtDate(t.pending_at)}</td>
        <td class="muted" style="max-width:280px">${t.last_message
          ? `${t.last_message.direction === 'operator' ? '↩' : t.last_message.direction === 'system' ? '·' : '💬'} ${esc(t.last_message.text)}`
          : '—'}</td>
      </tr>`).join('')}
  </table></div>
  <div class="pager">
    <span>${data.total} тикетов</span>
    <button class="btn btn-ghost btn-sm" id="pg-prev" ${tk.page <= 1 ? 'disabled' : ''}>←</button>
    <select id="pg-select" style="width:auto; padding:5px 10px; font-size:13px">
      ${Array.from({ length: pages }, (_, i) =>
        `<option value="${i + 1}" ${i + 1 === tk.page ? 'selected' : ''}>стр. ${i + 1} / ${pages}</option>`).join('')}
    </select>
    <button class="btn btn-ghost btn-sm" id="pg-next" ${tk.page >= pages ? 'disabled' : ''}>→</button>
  </div>` : '<div class="center">Тикетов нет</div>';

  const updated = document.getElementById('tk-updated');
  if (updated) updated.textContent = 'обновлено ' + new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  $list.querySelectorAll('.th-sort').forEach(th => {
    th.style.cursor = 'pointer';
    th.onclick = () => {
      const key = th.dataset.sort;
      if (tk.sort === key) tk.order = tk.order === 'desc' ? 'asc' : 'desc';
      else { tk.sort = key; tk.order = 'desc'; }
      tk.page = 1;
      loadTickets();
    };
  });
  const pv = $list.querySelector('#pg-prev'), nx = $list.querySelector('#pg-next'),
        sel = $list.querySelector('#pg-select');
  if (pv) pv.onclick = () => { tk.page--; loadTickets(); };
  if (nx) nx.onclick = () => { tk.page++; loadTickets(); };
  if (sel) sel.onchange = e => { tk.page = parseInt(e.target.value, 10); loadTickets(); };
}

async function viewTicket(userId) {
  $view.innerHTML = spinnerHtml('Загружаем тикет…');
  let data;
  try {
    data = await api('/api/tickets/' + userId);
  } catch (err) {
    $view.innerHTML = `<div class="card"><div class="error-note">${esc(err.message)}</div>
      <div style="margin-top:12px"><a href="#/tickets" class="btn btn-ghost">← К тикетам</a></div></div>`;
    return;
  }
  const t = data.ticket;
  if (!S.tk) S.tk = { status: 'open', page: 1, sort: 'pending_at', order: 'desc' };
  $view.innerHTML = `
    <div style="margin-bottom:12px"><a href="#/tickets" class="muted" style="text-decoration:none">← К тикетам</a></div>
    <div class="ticket-layout">
    <div class="ticket-main">
    <div class="card">
      <div class="user-header">
        <div>
          <h1 style="margin-bottom:2px">Тикет #${esc(t.user_id)}</h1>
          <div class="muted">${esc(t.first_name || '')} ${t.username ? '@' + esc(t.username) : ''}</div>
        </div>
        <div id="tk-status">${ticketBadge(t.status)}</div>
      </div>
      <div class="actions-bar">
        <a class="btn btn-ghost" href="#/user/${t.user_id}">Карточка пользователя</a>
        ${can('tickets') && t.status !== 'closed'
          ? '<button class="btn btn-danger" id="tk-close">Закрыть тикет</button>' : ''}
        <button class="btn btn-ghost" id="tk-refresh">Обновить</button>
      </div>
    </div>
    <div class="card">
      <h2>Переписка</h2>
      <div class="muted chat-hint" style="font-size:12px; margin-bottom:10px">
        Здесь видны сообщения, прошедшие через сайт и бота с момента подключения интеграции.
        Полная история старых тикетов — в Telegram-треде.</div>
      <div class="chat" id="tk-chat"></div>
      ${can('tickets') ? `
      <form id="tk-reply" style="margin-top:14px">
        <div class="field"><label>Ответ пользователю (уйдёт в ЛС и продублируется в тред)</label>
          <textarea name="text" rows="3" maxlength="3500"
            placeholder="Текст ответа…"></textarea></div>
        <input type="file" id="tk-photo" accept="image/*" class="hidden">
        <div id="tk-photo-preview" class="hidden" style="margin-bottom:10px"></div>
        <div id="tk-ai-status" class="muted hidden" style="font-size:12.5px; margin-bottom:10px"></div>
        <div style="display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap">
          <div style="display:flex; gap:8px; flex-wrap:wrap">
            <button class="btn btn-ghost" type="button" id="tk-attach">Прикрепить фото</button>
            <button class="btn btn-ghost" type="button" id="tk-quick">Быстрые ответы</button>
            <button class="btn btn-ghost" type="button" id="tk-ai">Ещё вариант ИИ</button>
          </div>
          <button class="btn" type="submit">Отправить</button>
        </div>
      </form>` : '<div class="muted" style="margin-top:10px">У вас нет права отвечать в тикеты.</div>'}
    </div>
    </div>
    <aside class="ticket-side">
      <div class="card">
        <div class="side-head">
          <h2 style="margin-bottom:0">Тикеты</h2>
          <select id="side-filter" style="width:auto; padding:5px 10px; font-size:13px">
            <option value="" ${S.tk.status === '' ? 'selected' : ''}>Все</option>
            <option value="pending" ${S.tk.status === 'pending' ? 'selected' : ''}>🟡 Ожидают</option>
            <option value="open" ${S.tk.status === 'open' ? 'selected' : ''}>🟢 У оператора</option>
            <option value="closed" ${S.tk.status === 'closed' ? 'selected' : ''}>🔴 Закрытые</option>
          </select>
        </div>
        <div id="side-list">${spinnerHtml()}</div>
      </div>
    </aside>
    </div>`;

  // на широких экранах чат занимает всё до низа окна и не создаёт скролл страницы
  window.scrollTo(0, 0);
  const layoutEl = $view.querySelector('.ticket-layout');
  const sizeLayout = () => {
    if (!layoutEl) return;
    if (window.matchMedia('(min-width: 1100px)').matches) {
      const h = window.innerHeight - layoutEl.getBoundingClientRect().top - 18;
      layoutEl.style.height = Math.max(420, h) + 'px';
    } else {
      layoutEl.style.height = '';
    }
  };
  sizeLayout();
  S.onResize = sizeLayout;
  window.addEventListener('resize', sizeLayout);

  renderChat(data.messages);

  document.getElementById('side-filter').onchange = e => {
    S.tk.status = e.target.value;
    S.tk.page = 1; // чтобы список тикетов открылся с той же выборки
    const list = document.getElementById('side-list');
    if (list) { list.dataset.key = ''; list.innerHTML = spinnerHtml(); }
    loadSideTickets(userId);
  };
  loadSideTickets(userId);

  let pollSig = data.sig || '';

  const refresh = async () => {
    try {
      const d = await api('/api/tickets/' + userId);
      pollSig = d.sig || pollSig;
      renderChat(d.messages);
      document.getElementById('tk-status').innerHTML = ticketBadge(d.ticket.status);
    } catch (e) { /* тихо: таймер может пережить уход со страницы */ }
    loadSideTickets(userId);
  };
  document.getElementById('tk-refresh').onclick = refresh;
  // список справа — раз в 5 секунд, чат — мгновенно через long-poll ниже
  S.ticketTimer = setInterval(() => loadSideTickets(userId), 5000);

  // Мгновенные обновления чата: держим запрос открытым, сервер отвечает
  // сразу, как только приходит новое сообщение (в т.ч. из Telegram).
  const pollCtl = new AbortController();
  S.chatPoll = pollCtl;
  (async () => {
    while (!pollCtl.signal.aborted) {
      try {
        const d = await api(
          `/api/tickets/${userId}/updates?sig=${encodeURIComponent(pollSig)}&wait=25`,
          { signal: pollCtl.signal });
        if (pollCtl.signal.aborted) return;
        pollSig = d.sig || '';
        if (d.changed) {
          renderChat(d.messages);
          const st = document.getElementById('tk-status');
          if (st) st.innerHTML = ticketBadge(d.ticket.status);
          loadSideTickets(userId);
        }
      } catch (e) {
        if (pollCtl.signal.aborted || e.status === 401) return;
        // сервер недоступен или 5xx — подождём, чтобы не заспамить
        await new Promise(r => setTimeout(r, 3000));
      }
    }
  })();

  const closeBtn = document.getElementById('tk-close');
  if (closeBtn) closeBtn.onclick = () => {
    const $m = openModal(`
      <h2>Закрыть тикет</h2>
      <div class="confirm-box">Тикет #${esc(userId)} будет закрыт. Пользователь получит запрос оценки, тред в Telegram переименуется в 🔴.</div>
      <div class="modal-actions">
        <button class="btn btn-ghost" onclick="closeModal()">Отмена</button>
        <button class="btn btn-danger" id="tk-close-ok">Закрыть тикет</button>
      </div>`);
    $m.querySelector('#tk-close-ok').onclick = async () => {
      try {
        await api(`/api/tickets/${userId}/close`, { method: 'POST' });
        closeModal();
        toast('Тикет закрыт ✓');
        viewTicket(userId);
      } catch (err) {
        toast(err.message, 'err');
      }
    };
  };

  const form = document.getElementById('tk-reply');
  if (form) {
    const photoInput = document.getElementById('tk-photo');
    const preview = document.getElementById('tk-photo-preview');

    const clearPhoto = () => {
      photoInput.value = '';
      preview.innerHTML = '';
      preview.classList.add('hidden');
    };
    document.getElementById('tk-attach').onclick = () => photoInput.click();

    // ---- ИИ-черновик: автогенерация при открытии, результат кладётся в поле ввода ----
    const aiStatus = document.getElementById('tk-ai-status');
    const aiBtn = document.getElementById('tk-ai');
    let lastAiDraft = null;

    const setAiStatus = (html) => {
      if (html) { aiStatus.innerHTML = html; aiStatus.classList.remove('hidden'); }
      else aiStatus.classList.add('hidden');
    };

    const generateAiDraft = async (auto = false) => {
      if (auto && S.aiDisabled) return; // авто не дёргаем, но ручной клик пробует снова
      aiBtn.disabled = true;
      setAiStatus('<span class="spinner"></span> ИИ готовит черновик ответа…');
      try {
        const data = await api(`/api/tickets/${userId}/suggest`, { method: 'POST' });
        const typed = form.text.value.trim();
        if (auto && typed && typed !== lastAiDraft) {
          // оператор уже что-то пишет — не затираем, просто предлагаем
          setAiStatus('Черновик ИИ готов — нажмите «Ещё вариант ИИ», чтобы вставить его вместо вашего текста');
          lastAiDraft = data.suggestion;
          return;
        }
        form.text.value = data.suggestion;
        lastAiDraft = data.suggestion;
        setAiStatus('Черновик ИИ вставлен в поле — проверьте и поправьте текст перед отправкой');
      } catch (err) {
        if (err.status === 503) {
          // не настроено на сервере: кнопку НЕ прячем — показываем причину,
          // авто-генерацию до конца сессии выключаем, ручной клик пробует снова
          S.aiDisabled = err.message || 'ИИ-помощник не настроен на сервере';
          setAiStatus('ИИ-помощник недоступен: ' + esc(S.aiDisabled));
          if (!auto) toast(err.message, 'err');
        } else if (auto) {
          setAiStatus('Не удалось получить черновик ИИ: ' + esc(err.message || '')
            + ' — можно попробовать кнопкой «Ещё вариант ИИ»');
        } else {
          setAiStatus('');
          toast(err.message, 'err');
        }
      } finally {
        aiBtn.disabled = false;
      }
    };

    aiBtn.onclick = () => {
      const typed = form.text.value.trim();
      if (typed && typed !== lastAiDraft
          && !confirm('Заменить текст в поле новым черновиком ИИ?')) return;
      S.aiDisabled = null; // вдруг на сервере уже починили — пробуем
      generateAiDraft(false);
    };

    // ---- быстрые ответы: общие с ботом, вставляются в поле ----
    document.getElementById('tk-quick').onclick = () => openQuickReplies(text => {
      const typed = form.text.value.trim();
      if (typed && typed !== lastAiDraft
          && !confirm('Заменить текст в поле выбранным быстрым ответом?')) return false;
      form.text.value = text;
      lastAiDraft = text; // чтобы следующая вставка/ИИ не спрашивали про этот текст
      setAiStatus('');
      form.text.focus();
      return true;
    });
    // автогенерация при открытии тикета (кроме закрытых);
    // если сервер уже отвечал «не настроено» — не дёргаем, но подсказываем причину
    if (S.aiDisabled) setAiStatus('ИИ-помощник недоступен: ' + esc(S.aiDisabled));
    else if (t.status !== 'closed') generateAiDraft(true);

    photoInput.onchange = () => {
      const f = photoInput.files[0];
      if (!f) return clearPhoto();
      if (!f.type.startsWith('image/')) { toast('Можно прикрепить только изображение', 'err'); return clearPhoto(); }
      if (f.size > 10 * 1024 * 1024) { toast('Фото больше 10 МБ — Telegram не примет', 'err'); return clearPhoto(); }
      preview.classList.remove('hidden');
      preview.innerHTML = `<div class="photo-preview">
        <img src="${URL.createObjectURL(f)}" alt="">
        <div>
          <div style="font-size:13px">${esc(f.name)}</div>
          <button type="button" class="btn btn-ghost btn-sm" id="tk-photo-remove" style="margin-top:6px">Убрать</button>
        </div>
      </div>`;
      preview.querySelector('#tk-photo-remove').onclick = clearPhoto;
    };

    form.addEventListener('submit', async e => {
      e.preventDefault();
      const text = form.text.value.trim();
      const photo = photoInput.files[0];
      if (!text && !photo) { toast('Введите текст или прикрепите фото', 'err'); return; }
      const btn = form.querySelector('button[type=submit]');
      btn.disabled = true;
      // мгновенно показываем своё сообщение в чате, не дожидаясь сервера
      const $chat = document.getElementById('tk-chat');
      const pendingEl = document.createElement('div');
      pendingEl.className = 'msg msg-operator msg-pending';
      pendingEl.innerHTML =
        (text ? `<div class="msg-text">${esc(text)}</div>` : '') +
        (photo ? '<div class="msg-text muted">📷 фото</div>' : '') +
        '<div class="msg-meta">отправляется…</div>';
      if ($chat) { $chat.appendChild(pendingEl); $chat.scrollTop = $chat.scrollHeight; }
      try {
        if (photo) {
          const fd = new FormData();
          fd.append('file', photo);
          fd.append('caption', text);
          const resp = await fetch(`/api/tickets/${userId}/photo`, {
            method: 'POST',
            headers: { Authorization: 'Bearer ' + S.token },
            body: fd,
          });
          const data = await resp.json().catch(() => null);
          if (!resp.ok) throw new Error((data && data.detail) || `Ошибка ${resp.status}`);
          clearPhoto();
        } else {
          await api(`/api/tickets/${userId}/reply`, { method: 'POST', body: { text } });
        }
        form.text.value = '';
        lastAiDraft = null;
        setAiStatus('');
        toast('Отправлено ✓');
        await refresh();
      } catch (err) {
        toast(err.message, 'err');
      } finally {
        pendingEl.remove(); // после refresh сообщение уже в истории; при ошибке — убираем
        btn.disabled = false;
      }
    });
  }
}

// Боковой список тикетов на широких экранах (справа от чата).
// На телефонах блок скрыт стилями, поэтому данные зря не качаем.
async function loadSideTickets(activeUserId) {
  const $list = document.getElementById('side-list');
  if (!$list) return;
  if (!window.matchMedia('(min-width: 1100px)').matches) return;
  const qs = new URLSearchParams({ page: 1, page_size: 30, sort: 'pending_at', order: 'desc' });
  if (S.tk && S.tk.status) qs.set('status', S.tk.status);
  let data;
  try {
    data = await api('/api/tickets?' + qs);
  } catch (e) {
    return; // тихо: следующая попытка через 5 секунд
  }
  const key = qs.toString() + '|' + activeUserId + '|' +
    JSON.stringify(data.items.map(m => [m.user_id, m.status, m.last_message && m.last_message.text]));
  if ($list.dataset.key === key) return; // ничего нового — не перерисовываем
  $list.dataset.key = key;

  const shortDate = v => {
    const dt = parseTs(v);
    return dt ? dt.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
  };
  const dot = st => `<span class="side-dot side-dot-${esc(st)}"></span>`;

  $list.innerHTML = data.items.length ? data.items.map(m => `
    <div class="side-row ${m.user_id === activeUserId ? 'active' : ''}" data-uid="${esc(m.user_id)}">
      <div class="side-row-top">
        ${dot(m.status)}
        <span class="side-name">${esc(m.first_name || '—')}${m.username ? ' <span class="muted">@' + esc(m.username) + '</span>' : ''}</span>
        <span class="side-date muted">${shortDate(m.pending_at)}</span>
      </div>
      <div class="side-last muted">${m.last_message ? esc(m.last_message.text || 'вложение') : '—'}</div>
    </div>`).join('') +
    (data.total > data.items.length
      ? `<a href="#/tickets" class="muted" style="display:block; text-align:center; font-size:12.5px; padding:10px 0 2px">Показаны первые ${data.items.length} из ${data.total} — все тикеты →</a>`
      : '')
    : '<div class="center" style="padding:14px 0">Тикетов нет</div>';

  $list.querySelectorAll('.side-row').forEach(row => {
    row.onclick = () => {
      const uid = parseInt(row.dataset.uid, 10);
      if (uid !== activeUserId) location.hash = '#/ticket/' + uid;
    };
  });
}

// ================================================================ quick replies
// Общая с ботом коллекция support_quick_replies: добавленное здесь появляется
// и в меню бота, и в FAQ ИИ-помощника. insert(text) -> true, если вставлено.

function openQuickReplies(insert) {
  const $m = openModal(`
    <h2>Быстрые ответы</h2>
    <div class="muted" style="font-size:12px; margin-bottom:10px">
      Общие с ботом: изменения тут сразу видны в меню бота и наоборот.
      Клик по названию вставляет текст в поле ответа.</div>
    <input id="qr-search" placeholder="Поиск по названию или тексту…" style="margin-bottom:10px">
    <div id="qr-list" style="max-height:48vh; overflow-y:auto">${spinnerHtml()}</div>
    <div class="modal-actions">
      <button class="btn btn-ghost" id="qr-add">+ Добавить</button>
      <button class="btn" id="qr-close">Закрыть</button>
    </div>`);
  const $list = $m.querySelector('#qr-list');
  let items = [];

  const renderList = () => {
    const q = $m.querySelector('#qr-search').value.trim().toLowerCase();
    const filtered = items.filter(i => !q
      || i.title.toLowerCase().includes(q) || i.text.toLowerCase().includes(q));
    $list.innerHTML = filtered.length ? filtered.map(i => `
      <div class="qr-item">
        <div class="qr-main" data-use="${esc(i.id)}">
          <div class="qr-title">${esc(i.title)}
            ${i.active ? '' : ' <span class="badge badge-gray">выключен</span>'}</div>
          <div class="qr-preview muted">${esc(i.text.slice(0, 110))}${i.text.length > 110 ? '…' : ''}</div>
        </div>
        <div class="qr-btns">
          <button class="btn btn-ghost btn-sm" data-edit="${esc(i.id)}">Изменить</button>
          <button class="btn btn-ghost btn-sm" data-del="${esc(i.id)}">Удалить</button>
        </div>
      </div>`).join('')
      : '<div class="center" style="padding:14px 0">Пока пусто — добавьте первый быстрый ответ</div>';

    $list.querySelectorAll('[data-use]').forEach(el => el.onclick = () => {
      const item = items.find(i => i.id === el.dataset.use);
      if (item && insert(item.text) !== false) closeModal();
    });
    $list.querySelectorAll('[data-edit]').forEach(el => el.onclick = () =>
      qrEditModal(items.find(i => i.id === el.dataset.edit), insert));
    $list.querySelectorAll('[data-del]').forEach(el => el.onclick = async () => {
      const item = items.find(i => i.id === el.dataset.del);
      if (!item) return;
      if (!confirm(`Удалить быстрый ответ «${item.title}»? Он пропадёт и из меню бота.`)) return;
      try {
        await api('/api/quick-replies/' + item.id, { method: 'DELETE' });
        toast('Удалено ✓');
        load();
      } catch (err) { toast(err.message, 'err'); }
    });
  };

  const load = async () => {
    try {
      items = (await api('/api/quick-replies?all=true')).items;
      renderList();
    } catch (err) {
      $list.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
    }
  };
  $m.querySelector('#qr-search').oninput = renderList;
  $m.querySelector('#qr-add').onclick = () => qrEditModal(null, insert);
  $m.querySelector('#qr-close').onclick = closeModal;
  load();
}

function qrEditModal(item, insert) {
  const $m = openModal(`
    <h2>${item ? 'Изменить быстрый ответ' : 'Новый быстрый ответ'}</h2>
    <form id="qr-form">
      <div class="field"><label>Название (это текст кнопки в боте и на сайте)</label>
        <input name="title" required maxlength="64" value="${item ? esc(item.title) : ''}"></div>
      <div class="field"><label>Текст ответа пользователю</label>
        <textarea name="text" rows="8" required maxlength="3500">${item ? esc(item.text) : ''}</textarea></div>
      ${item ? `<label style="display:flex; gap:8px; align-items:center; color:var(--text); margin-bottom:12px">
        <input type="checkbox" name="active" style="width:auto" ${item.active ? 'checked' : ''}>
        Активен (виден в боте и в списке вставки)
      </label>` : ''}
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" id="qr-back">Назад</button>
        <button type="submit" class="btn">Сохранить</button>
      </div>
    </form>`);
  $m.querySelector('#qr-back').onclick = () => openQuickReplies(insert);
  $m.querySelector('#qr-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    const body = { title: f.title.value.trim(), text: f.text.value.trim() };
    try {
      if (item) {
        body.active = f.active.checked;
        await api('/api/quick-replies/' + item.id, { method: 'PATCH', body });
      } else {
        await api('/api/quick-replies', { method: 'POST', body });
      }
      toast('Сохранено ✓');
      openQuickReplies(insert);
    } catch (err) { toast(err.message, 'err'); }
  });
}

const ATT_CACHE = {}; // file_id -> blob URL (чтобы автообновление не перекачивало файлы)

function attachmentHtml(m) {
  const a = m.attachment;
  if (!a || !a.file_id) return '';
  const id = esc(a.file_id);
  if (a.type === 'photo' || a.type === 'sticker') {
    return `<div class="att" data-file="${id}" data-att-type="img"><span class="spinner"></span></div>`;
  }
  if (a.type === 'voice' || a.type === 'audio') {
    return `<div class="att" data-file="${id}" data-att-type="audio"><span class="spinner"></span></div>`;
  }
  if (a.type === 'video' || a.type === 'video_note' || a.type === 'animation') {
    return `<div class="att" data-file="${id}" data-att-type="video"><span class="spinner"></span></div>`;
  }
  return `<div class="att"><button class="att-chip" data-file="${id}" data-att-type="download">📄 ${esc(a.name || 'Файл')} — открыть</button></div>`;
}

async function fetchAttachment(fileId) {
  if (ATT_CACHE[fileId]) return ATT_CACHE[fileId];
  const resp = await fetch('/api/tickets/file/' + encodeURIComponent(fileId), {
    headers: { Authorization: 'Bearer ' + S.token },
  });
  if (!resp.ok) throw new Error('Вложение недоступно');
  const url = URL.createObjectURL(await resp.blob());
  ATT_CACHE[fileId] = url;
  return url;
}

function openLightbox(url, kind) {
  const root = document.getElementById('modal-root');
  root.innerHTML = `
    <div class="lightbox-overlay">
      ${kind === 'video'
        ? `<video controls autoplay src="${url}"></video>`
        : `<img src="${url}" alt="вложение">`}
      <button class="lightbox-close" title="Закрыть">✕</button>
    </div>`;
  const overlay = root.querySelector('.lightbox-overlay');
  overlay.addEventListener('click', e => {
    if (e.target === overlay || e.target.classList.contains('lightbox-close')) closeModal();
  });
  const onKey = e => { if (e.key === 'Escape') { closeModal(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
}

function hydrateAttachments(root) {
  root.querySelectorAll('[data-file]').forEach(async el => {
    if (el.dataset.hydrated) return;
    el.dataset.hydrated = '1';
    const type = el.dataset.attType;
    if (type === 'download') {
      el.onclick = async () => {
        el.disabled = true;
        try {
          const url = await fetchAttachment(el.dataset.file);
          const a = document.createElement('a');
          a.href = url;
          a.download = el.textContent.replace(/^📄 /, '').replace(/ — открыть$/, '') || 'file';
          a.click();
        } catch (e) { toast('Не удалось загрузить вложение', 'err'); }
        el.disabled = false;
      };
      return;
    }
    try {
      const url = await fetchAttachment(el.dataset.file);
      if (type === 'img') {
        el.innerHTML = `<img src="${url}" alt="вложение">`;
        el.querySelector('img').onclick = () => openLightbox(url, 'img');
      } else if (type === 'audio') {
        el.innerHTML = `<audio controls src="${url}"></audio>`;
      } else if (type === 'video') {
        el.innerHTML = `<video controls src="${url}"></video>`;
        el.querySelector('video').addEventListener('dblclick', () => openLightbox(url, 'video'));
      }
    } catch (e) {
      el.innerHTML = '<span class="muted" style="font-size:12px">вложение недоступно</span>';
    }
  });
}

function renderChat(messages) {
  const $chat = document.getElementById('tk-chat');
  if (!$chat) return;
  const key = JSON.stringify(messages.map(m => m.timestamp));
  if ($chat.dataset.key === key) return; // ничего нового — не перерисовываем (не сбрасываем плееры)
  $chat.dataset.key = key;
  if (!messages.length) {
    $chat.innerHTML = '<div class="center">Сообщений пока нет</div>';
    return;
  }
  $chat.innerHTML = messages.map(m => {
    const cls = m.direction === 'operator' ? 'msg-operator' : m.direction === 'system' ? 'msg-system' : 'msg-user';
    const who = m.direction === 'operator'
      ? (m.operator_login ? esc(m.operator_login) + (m.source === 'tg' ? ' (TG)' : ' (сайт)') : 'оператор')
      : m.direction === 'system' ? '' : 'пользователь';
    const text = m.text ? `<div class="msg-text">${esc(m.text)}</div>` : '';
    return `<div class="msg ${cls}">${text}${attachmentHtml(m)}<div class="msg-meta">${who ? who + ' · ' : ''}${fmtDate(m.timestamp)}</div></div>`;
  }).join('');
  hydrateAttachments($chat);
  $chat.scrollTop = $chat.scrollHeight;
}

// ================================================================ operator activity / stats

function fmtDur(sec) {
  if (sec == null) return '—';
  if (sec < 60) return Math.round(sec) + ' сек';
  if (sec < 3600) return Math.round(sec / 60) + ' мин';
  return Math.floor(sec / 3600) + ' ч ' + Math.round((sec % 3600) / 60) + ' мин';
}

function isoDate(d) { return d.toISOString().slice(0, 10); }

function viewStats() {
  const today = new Date();
  if (!S.st) {
    S.st = { from: isoDate(new Date(today - 6 * 86400000)), to: isoDate(today), preset: '7d' };
  }
  const st = S.st;
  $view.innerHTML = `
    <h1>Активность операторов</h1>
    <div class="card">
      <div class="filter-bar" style="align-items:center">
        <button class="btn btn-ghost btn-sm" data-preset="today">Сегодня</button>
        <button class="btn btn-ghost btn-sm" data-preset="7d">7 дней</button>
        <button class="btn btn-ghost btn-sm" data-preset="30d">30 дней</button>
        <button class="btn btn-ghost btn-sm" data-preset="month">Этот месяц</button>
        <input type="date" name="st-from" value="${esc(st.from)}" style="flex:0 1 150px">
        <input type="date" name="st-to" value="${esc(st.to)}" style="flex:0 1 150px">
        <button class="btn btn-sm" id="st-apply">Показать</button>
        ${S.me.role === 'owner' ? `
          <button class="btn btn-ghost btn-sm" id="st-settings">Настройки расчёта</button>
          <button class="btn btn-ghost btn-sm" id="st-csv">Скачать CSV</button>` : ''}
      </div>
      <div id="st-summary" class="stats-grid" style="margin:14px 0 0"></div>
    </div>
    <div class="card">
      <h2>Рейтинг за период</h2>
      <div id="st-table">${spinnerHtml()}</div>
      <div class="muted" id="st-formula" style="font-size:12.5px; margin-top:12px"></div>
    </div>`;

  const load = async () => {
    const $t = document.getElementById('st-table');
    $t.innerHTML = spinnerHtml();
    let data;
    try {
      data = await api(`/api/stats/operators?date_from=${st.from}&date_to=${st.to}`);
    } catch (err) {
      $t.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
      return;
    }
    S.stData = data;
    const rows = data.rows;
    const tot = k => rows.reduce((s, r) => s + (r[k] || 0), 0);
    const allWaits = rows.filter(r => r.avg_wait_sec != null);
    const avgAll = allWaits.length
      ? allWaits.reduce((s, r) => s + r.avg_wait_sec * r.measured, 0) /
        Math.max(1, allWaits.reduce((s, r) => s + r.measured, 0))
      : null;
    document.getElementById('st-summary').innerHTML = `
      <div class="stat"><div class="stat-label">Ответов</div><div class="stat-value">${tot('replies')}</div></div>
      <div class="stat"><div class="stat-label">Тикетов закрыто</div><div class="stat-value">${tot('closes')}</div></div>
      <div class="stat"><div class="stat-label">Средняя скорость ответа</div><div class="stat-value">${fmtDur(avgAll)}</div></div>
      <div class="stat"><div class="stat-label">Оценок получено</div><div class="stat-value">${tot('rating_count')}</div></div>`;

    const showPay = rows.some(r => 'salary_base' in r);
    $t.innerHTML = rows.length ? `<div class="table-wrap"><table>
      <tr><th>#</th><th>Оператор</th><th>Баллы</th><th>Коэфф.</th>${showPay ? '<th>К выплате</th>' : ''}
        <th>Ответы</th><th>Тикетов</th><th>Закрыто</th>
        <th>Скорость (медиана)</th><th>Быстрых ≤${data.points.fast_threshold_min} мин</th><th>Оценка</th><th>График</th></tr>
      ${rows.map((r, i) => `
        <tr class="${r.login === S.me.login ? 'st-me' : ''}">
          <td>${i === 0 ? '🏆' : i + 1}</td>
          <td>${esc(r.name)} <span class="muted mono" style="font-size:11px">${esc(r.login)}</span></td>
          <td><b>${fmtNum(r.score)}</b>${r.norm_points ? `<span class="muted" style="font-size:11px"> / ${fmtNum(r.norm_points)}</span>` : ''}</td>
          <td>${r.coeff != null ? `<b>×${r.coeff.toFixed(2)}</b>` : '<span class="muted" title="Задайте часы в неделю в карточке оператора">—</span>'}</td>
          ${showPay ? `<td>${'payout' in r && r.payout != null ? `<b>${fmtNum(r.payout)} ₽</b>` : ('salary_base' in r ? '<span class="muted">—</span>' : '')}</td>` : ''}
          <td>${r.replies}</td>
          <td>${r.tickets}</td>
          <td>${r.closes}</td>
          <td>${fmtDur(r.median_wait_sec)}${r.avg_wait_sec != null ? ` <span class="muted">(ср. ${fmtDur(r.avg_wait_sec)})</span>` : ''}</td>
          <td>${r.measured ? `${r.fast} из ${r.measured} (${Math.round(r.fast / r.measured * 100)}%)` : '—'}</td>
          <td>${r.rating_avg != null ? `★ ${r.rating_avg} <span class="muted">(${r.rating_count})</span>` : '—'}</td>
          <td${r.schedule ? ` title="${esc(scheduleSummary(r.schedule))}"` : ''}>${r.hours_per_week != null ? esc(r.hours_per_week) + ' ч/нед' : '—'}${r.schedule ? ' <span class="muted" style="cursor:help">ⓘ</span>' : ''}</td>
        </tr>`).join('')}
    </table></div>`
      : '<div class="center">За выбранный период активности нет</div>';

    const p = data.points;
    const a = data.settings || {};
    const workWin = a.work_start === a.work_end ? 'круглосуточно' : `${a.work_start}–${a.work_end} (UTC+${a.tz_offset_hours})`;
    document.getElementById('st-formula').innerHTML =
      `Баллы: ответ +${p.reply} · закрытие тикета +${p.close} · быстрый первый ответ (≤${p.fast_threshold_min} мин) ещё +${p.fast} · ` +
      `оценка пользователя ±${p.rating_step}×(звёзды−3), т.е. 5★ = +${p.rating_step * 2}, 1★ = −${p.rating_step * 2}.<br>` +
      `Коэффициент = баллы ÷ норма, в пределах ×${a.coeff_min}–×${a.coeff_max}. ` +
      (a.norm_mode === 'manual'
        ? `Норма = ${a.norm_points_per_hour} баллов/час × часы оператора за период. `
        : a.norm_mode === 'team_avg'
          ? `Норма — средний темп команды за период` +
            (data.norm_used ? ` (${data.norm_used} баллов/час)` : ' (пока нет данных)') +
            `, × часы оператора. `
          : `Норма — по нагрузке: за период отвечено на ${(data.demand || {}).answered ?? '—'} обращений, ` +
            `закрыто ${(data.demand || {}).closes ?? '—'} тикетов, в очереди без ответа висит ${(data.demand || {}).backlog ?? '—'} — ` +
            `итого ${(data.demand || {}).potential_points ?? '—'} доступных баллов, распределяются по часам операторов` +
            (data.norm_used ? ` (${data.norm_used} баллов/час)` : '') + `. `) +
      `К выплате = оклад × коэффициент (пропорционально периоду).<br>` +
      `Рабочее окно поддержки: ${workWin} — время вне окна не считается ожиданием ответа. ` +
      `Учитываются ответы с сайта и из Telegram (если бот пишет их в общую историю).`;
  };

  const setPreset = (preset) => {
    const now = new Date();
    if (preset === 'today') st.from = st.to = isoDate(now);
    else if (preset === '7d') { st.from = isoDate(new Date(now - 6 * 86400000)); st.to = isoDate(now); }
    else if (preset === '30d') { st.from = isoDate(new Date(now - 29 * 86400000)); st.to = isoDate(now); }
    else if (preset === 'month') {
      st.from = isoDate(new Date(Date.UTC(now.getFullYear(), now.getMonth(), 1)));
      st.to = isoDate(now);
    }
    document.querySelector('[name=st-from]').value = st.from;
    document.querySelector('[name=st-to]').value = st.to;
    load();
  };
  document.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => setPreset(b.dataset.preset));
  document.getElementById('st-apply').onclick = () => {
    st.from = document.querySelector('[name=st-from]').value;
    st.to = document.querySelector('[name=st-to]').value;
    if (!st.from || !st.to) { toast('Выберите обе даты', 'err'); return; }
    load();
  };
  const setBtn = document.getElementById('st-settings');
  if (setBtn) setBtn.onclick = async () => {
    let cfg;
    try { cfg = await api('/api/stats/settings'); }
    catch (err) { toast(err.message, 'err'); return; }
    const $m = openModal(`
      <h2>Настройки расчёта зарплаты</h2>
      <form id="act-set">
        <div class="field"><label>Норма баллов в час</label>
          <select name="nmode">
            <option value="auto" ${cfg.norm_mode !== 'manual' && cfg.norm_mode !== 'team_avg' ? 'selected' : ''}>По нагрузке — от потока обращений (рекомендуется)</option>
            <option value="team_avg" ${cfg.norm_mode === 'team_avg' ? 'selected' : ''}>Средний темп команды</option>
            <option value="manual" ${cfg.norm_mode === 'manual' ? 'selected' : ''}>Вручную</option>
          </select>
          <div class="muted" style="font-size:12px; margin-top:4px">
            <b>По нагрузке</b>: норма — из реальной работы периода: отвеченные обращения (по 5),
            закрытые тикеты (по 10) и висящая без ответа очередь (по 15 — полный вес),
            поделённые между операторами пропорционально их часам. Сообщения, которые
            обработал бот или которые не требовали ответа, в нагрузку не попадают.
            Игнорировать тикеты невыгодно: очередь тянет норму вверх сильнее всего.
            Если нагрузки не было — коэффициент 1.0. Работает даже с двумя операторами.<br>
            <b>Средний темп команды</b> — сравнение с коллегами (осторожно: при общем
            простое норма занижается). <b>Вручную</b> — фиксированное число баллов/час.</div></div>
        <div class="field" id="norm-manual"><label>Норма баллов за час (для ручного режима)</label>
          <input name="norm" type="number" step="0.5" min="0" value="${esc(cfg.norm_points_per_hour)}">
          <div class="muted" style="font-size:12px; margin-top:4px">
            Норма оператора за период = это число × его часы. Пример: 10 баллов/час ≈ 4 ответа или 1 закрытие в час.</div></div>
        <div class="row">
          <div class="field"><label>Коэфф. минимум</label>
            <input name="cmin" type="number" step="0.05" min="0" value="${esc(cfg.coeff_min)}"></div>
          <div class="field"><label>Коэфф. максимум</label>
            <input name="cmax" type="number" step="0.05" min="0" value="${esc(cfg.coeff_max)}"></div>
        </div>
        <div class="row">
          <div class="field"><label>Поддержка работает с</label>
            <input name="wstart" value="${esc(cfg.work_start)}" placeholder="09:00"></div>
          <div class="field"><label>до</label>
            <input name="wend" value="${esc(cfg.work_end)}" placeholder="24:00"></div>
          <div class="field"><label>Часовой пояс, UTC+</label>
            <input name="tz" type="number" min="-12" max="14" value="${esc(cfg.tz_offset_hours)}"></div>
        </div>
        <div class="muted" style="font-size:12px; margin-bottom:12px">
          Время вне окна не считается ожиданием ответа (ночь никого не штрафует).
          Одинаковые «с» и «до» = круглосуточно. Окно может переходить через полночь (18:00–02:00).
          Оклад и часы в неделю задаются в карточке каждого оператора (страница «Операторы»).</div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" onclick="closeModal()">Отмена</button>
          <button type="submit" class="btn">Сохранить</button>
        </div>
      </form>`);
    const nmodeSel = $m.querySelector('[name=nmode]');
    const toggleNorm = () => $m.querySelector('#norm-manual')
      .classList.toggle('hidden', nmodeSel.value !== 'manual');
    nmodeSel.addEventListener('change', toggleNorm);
    toggleNorm();
    $m.querySelector('#act-set').addEventListener('submit', async e => {
      e.preventDefault();
      const f = e.target;
      try {
        await api('/api/stats/settings', { method: 'PUT', body: {
          norm_mode: f.nmode.value,
          norm_points_per_hour: parseFloat(f.norm.value) || 0,
          coeff_min: parseFloat(f.cmin.value) || 0,
          coeff_max: parseFloat(f.cmax.value) || 0,
          work_start: f.wstart.value.trim(),
          work_end: f.wend.value.trim(),
          tz_offset_hours: parseInt(f.tz.value, 10) || 0,
        }});
        closeModal();
        toast('Сохранено ✓');
        load();
      } catch (err) { toast(err.message, 'err'); }
    });
  };

  const csvBtn = document.getElementById('st-csv');
  if (csvBtn) csvBtn.onclick = () => {
    const d = S.stData;
    if (!d || !d.rows.length) { toast('Нет данных для выгрузки', 'err'); return; }
    const head = ['Логин', 'Имя', 'Баллы', 'Норма', 'Коэффициент', 'Оклад, ₽/мес', 'К выплате, ₽',
      'Часов/нед', 'Ответы', 'Тикетов', 'Закрыто',
      'Медиана ответа, сек', 'Среднее, сек', 'Быстрых', 'Замерено', 'Оценка', 'Кол-во оценок'];
    const lines = [head.join(';')].concat(d.rows.map(r => [
      r.login, r.name, r.score, r.norm_points ?? '', r.coeff ?? '', r.salary_base ?? '', r.payout ?? '',
      r.hours_per_week ?? '', r.replies, r.tickets, r.closes,
      r.median_wait_sec ?? '', r.avg_wait_sec ?? '', r.fast, r.measured,
      r.rating_avg ?? '', r.rating_count,
    ].map(v => String(v).replace(/;/g, ',')).join(';')));
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `operators_${d.date_from}_${d.date_to}.csv`;
    a.click();
  };

  load();
}

// ================================================================ audit view (owner)

async function viewAudit(page = 1) {
  if (!S.me || S.me.role !== 'owner') { location.hash = '#/search'; return; }
  const keepFilters = document.getElementById('audit-filters') != null;
  const f = keepFilters ? {
    op: document.querySelector('[name=a-op]').value.trim(),
    uid: document.querySelector('[name=a-uid]').value.trim(),
    action: document.querySelector('[name=a-action]').value,
    from: document.querySelector('[name=a-from]').value,
    to: document.querySelector('[name=a-to]').value,
  } : { op: '', uid: '', action: '', from: '', to: '' };

  if (!keepFilters) {
    $view.innerHTML = `
      <h1>Аудит-лог операторов</h1>
      <div class="card">
        <div class="filter-bar" id="audit-filters">
          <input name="a-op" placeholder="Логин оператора">
          <input name="a-uid" type="number" placeholder="user_id">
          <select name="a-action"><option value="">Все действия</option></select>
          <input name="a-from" type="date">
          <input name="a-to" type="date">
          <button class="btn btn-sm" id="a-apply">Применить</button>
        </div>
        <div id="audit-list"></div>
      </div>`;
    try {
      const acts = await api('/api/audit/actions');
      const sel = document.querySelector('[name=a-action]');
      acts.actions.forEach(a => {
        const o = document.createElement('option');
        o.value = a; o.textContent = actionLabel(a);
        sel.appendChild(o);
      });
    } catch (e) { /* non-fatal */ }
  }
  document.getElementById('a-apply').onclick = () => viewAudit(1);

  const $list = document.getElementById('audit-list');
  $list.innerHTML = spinnerHtml();
  const qs = new URLSearchParams({ page, page_size: 50 });
  if (f.op) qs.set('operator_login', f.op);
  if (f.uid) qs.set('target_user_id', f.uid);
  if (f.action) qs.set('action', f.action);
  if (f.from) qs.set('date_from', f.from);
  if (f.to) qs.set('date_to', f.to);
  try {
    const data = await api('/api/audit?' + qs);
    $list.innerHTML = data.items.length ? `<div class="table-wrap"><table>
      <tr><th>Время</th><th>Оператор</th><th>Действие</th><th>Пользователь</th><th>Было → стало</th><th>Причина</th><th>Sync</th><th>IP</th></tr>
      ${data.items.map(r => `<tr>
        <td class="mono" style="white-space:nowrap">${fmtDate(r.timestamp)}</td>
        <td>${esc(r.operator_login)}${r.operator_name ? '<br><span class="muted">' + esc(r.operator_name) + '</span>' : ''}</td>
        <td><span class="badge badge-blue">${esc(actionLabel(r.action))}</span></td>
        <td class="mono">${r.target_user_id != null ? `<a href="#/user/${r.target_user_id}" style="color:var(--accent)">${r.target_user_id}</a>` : '—'}</td>
        <td class="mono" style="max-width:260px; white-space:pre-wrap">${fmtChange(r.old_value, r.new_value)}</td>
        <td style="max-width:200px">${esc(r.reason || '—')}</td>
        <td>${fmtSync(r)}</td>
        <td class="mono">${esc(r.ip || '')}</td>
      </tr>`).join('')}
    </table></div>` + pagerHtml(data) : '<div class="center">Записей нет</div>';
    const pv = $list.querySelector('#pg-prev'), nx = $list.querySelector('#pg-next');
    if (pv) pv.onclick = () => viewAudit(data.page - 1);
    if (nx) nx.onclick = () => viewAudit(data.page + 1);
  } catch (err) {
    $list.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
  }
}

const ACTION_LABELS = {
  login: 'Вход', balance_change: 'Баланс', subscription_expire_change: 'Срок подписки',
  device_limit_change: 'Лимит устройств', bypass_update: 'ByPass', device_reset: 'Отвязка устройств',
  email_change: 'Email', gift: 'Подарок', operator_create: 'Создание оператора',
  operator_update: 'Изменение оператора',
  operator_password_reset: 'Сброс пароля оператора',
  password_change: 'Смена своего пароля',
  ticket_reply: 'Ответ в тикете',
  ticket_close: 'Закрытие тикета',
  quick_reply_create: 'Быстрый ответ: создан',
  quick_reply_update: 'Быстрый ответ: изменён',
  quick_reply_delete: 'Быстрый ответ: удалён',
};
function actionLabel(a) { return ACTION_LABELS[a] || a; }

function fmtChange(oldV, newV) {
  if (oldV == null && newV == null) return '—';
  const short = v => v == null ? '—' : esc(typeof v === 'object' ? JSON.stringify(v) : v);
  return `${short(oldV)}\n→ ${short(newV)}`;
}

function fmtSync(r) {
  if (r.remnawave_synced === true) return '<span class="badge badge-green">✓</span>';
  if (r.remnawave_synced === false) return `<span class="badge badge-red" title="${esc(r.remnawave_error || '')}">✗</span>`;
  return '<span class="muted">—</span>';
}

// ================================================================ operators view (owner)

async function viewOperators() {
  if (!S.me || S.me.role !== 'owner') { location.hash = '#/search'; return; }
  $view.innerHTML = `
    <h1>Операторы</h1>
    <div class="card">
      <div style="display:flex; justify-content:flex-end; margin-bottom:12px">
        <button class="btn" id="op-add">+ Новый оператор</button>
      </div>
      <div id="op-list">${spinnerHtml()}</div>
    </div>`;
  document.getElementById('op-add').onclick = () => modalOperatorCreate();
  await loadOperatorList();
}

async function loadOperatorList() {
  const $list = document.getElementById('op-list');
  try {
    const ops = await api('/api/operators');
    $list.innerHTML = `<div class="table-wrap"><table>
      <tr><th>Логин</th><th>Имя</th><th>Роль</th><th>Статус</th><th>Последний вход</th><th></th></tr>
      ${ops.map(o => `<tr>
        <td class="mono">${esc(o.login)}</td>
        <td>${esc(o.name)}</td>
        <td><span class="badge ${o.role === 'owner' ? 'badge-yellow' : 'badge-blue'}">${esc(o.role)}</span>
          ${o.role === 'operator' ? `<div class="muted" style="font-size:11.5px;margin-top:3px">прав: ${Object.values(o.permissions || {}).filter(Boolean).length}/${Object.keys(PERM_LABELS).length}</div>` : ''}</td>
        <td>${o.active ? '<span class="badge badge-green">активен</span>' : '<span class="badge badge-red">отключён</span>'}
          ${o.must_change_password ? '<div style="margin-top:3px"><span class="badge badge-yellow">врем. пароль</span></div>' : ''}</td>
        <td class="mono">${fmtDate(o.last_login_at)}</td>
        <td><button class="btn btn-ghost btn-sm" data-edit="${esc(o.id)}">Изменить</button></td>
      </tr>`).join('')}
    </table></div>`;
    $list.querySelectorAll('button[data-edit]').forEach(btn => {
      const op = ops.find(o => o.id === btn.dataset.edit);
      btn.onclick = () => modalOperatorEdit(op);
    });
  } catch (err) {
    $list.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
  }
}

const DAY_LIST = [
  ['mon', 'Понедельник'], ['tue', 'Вторник'], ['wed', 'Среда'], ['thu', 'Четверг'],
  ['fri', 'Пятница'], ['sat', 'Суббота'], ['sun', 'Воскресенье'],
];

// разбор значения дня: "09:00-18:00" или "10:00-14:00~8" (плавающий)
function parseDay(iv) {
  const fm = /^(\d{2}:\d{2})-(\d{2}:\d{2})~(\d{1,2}(?:\.\d)?)$/.exec(iv || '');
  if (fm) return { a: fm[1], b: fm[2], float: true, dur: parseFloat(fm[3]) };
  const m = /^(\d{2}:\d{2})-(\d{2}:\d{2})$/.exec(iv || '');
  if (m) return { a: m[1], b: m[2], float: false, dur: null };
  return { a: '', b: '', float: false, dur: null };
}

// часы дня; для фиксированного конец <= начала = через полночь;
// для плавающего — гарантированный минимум: длина дня − окно 1-го ответа
function intervalHours(iv) {
  const d = parseDay(iv);
  if (!d.a || !d.b) return 0;
  const toMin = t => +t.slice(0, 2) * 60 + +t.slice(3);
  const a = toMin(d.a), b = toMin(d.b) || 1440;
  if (d.float) return Math.max(0, (b - a) / 60 - (d.dur || 0));
  return ((b > a ? b - a : 1440 - a + b)) / 60;
}

function scheduleSummary(schedule) {
  if (!schedule) return '';
  const short = { mon: 'Пн', tue: 'Вт', wed: 'Ср', thu: 'Чт', fri: 'Пт', sat: 'Сб', sun: 'Вс' };
  return DAY_LIST.map(([k]) => {
    const v = schedule[k];
    if (!v) return `${short[k]} вых.`;
    const d = parseDay(v);
    return d.float
      ? `${short[k]} ${d.a}–${d.b}, старт по 1-му ответу в первые ${d.dur} ч`
      : `${short[k]} ${d.a}–${d.b}`;
  }).join(' · ');
}

const DAY_SHORT = { mon: 'Пн', tue: 'Вт', wed: 'Ср', thu: 'Чт', fri: 'Пт', sat: 'Сб', sun: 'Вс' };

function schedRowHtml(k, label, iv) {
  const d = parseDay(iv);
  return `<tr>
    <td class="sched-day" title="${label}">${DAY_SHORT[k]}</td>
    <td><input type="time" name="sch_${k}_a" value="${esc(d.a)}"></td>
    <td class="sched-dash">—</td>
    <td><input type="time" name="sch_${k}_b" value="${esc(d.b)}"></td>
    <td><label class="sched-float" title="Рабочий день начнётся с первого ответа оператора, но не позже, чем через указанное число часов от начала интервала. До старта ожидание не портит рейтинг">
      <input type="checkbox" name="sch_${k}_f" ${d.float ? 'checked' : ''}> 1-й ответ</label></td>
    <td><input type="number" name="sch_${k}_d" class="sched-dur ${d.float ? '' : 'hidden'}"
      min="0.5" max="12" step="0.5" value="${d.dur ?? ''}" placeholder="1" title="Окно первого ответа, часов"></td>
    <td class="sched-hrs muted" data-day="${k}"></td>
    <td class="sched-btns">
      <button type="button" class="btn btn-ghost btn-sm" data-copy="${k}" title="Скопировать время этого дня">Коп.</button>
      <button type="button" class="btn btn-ghost btn-sm" data-paste="${k}" title="Вставить скопированное время" disabled>Вст.</button>
    </td>
  </tr>`;
}

function bindScheduleTable($m) {
  let clip = null; // скопированный день {a, b, float, dur}

  const dayHours = (k) => {
    const a = $m.querySelector(`[name=sch_${k}_a]`).value;
    const b = $m.querySelector(`[name=sch_${k}_b]`).value;
    const f = $m.querySelector(`[name=sch_${k}_f]`).checked;
    const d = parseFloat($m.querySelector(`[name=sch_${k}_d]`).value);
    if (!a || !b) return 0;
    if (f) return d ? intervalHours(`${a}-${b}~${d}`) : 0;
    return intervalHours(`${a}-${b}`);
  };

  const recalc = () => {
    let total = 0;
    DAY_LIST.forEach(([k]) => {
      const f = $m.querySelector(`[name=sch_${k}_f]`).checked;
      $m.querySelector(`[name=sch_${k}_d]`).classList.toggle('hidden', !f);
      const h = dayHours(k);
      total += h;
      const cell = $m.querySelector(`.sched-hrs[data-day=${k}]`);
      if (cell) cell.textContent = h ? h.toFixed(1).replace(/\.0$/, '') + ' ч' : 'вых.';
    });
    const el = $m.querySelector('#sched-total');
    if (el) el.textContent = total ? total.toFixed(1).replace(/\.0$/, '') + ' ч/нед' : 'график не задан';
  };
  $m.querySelectorAll('.sched-table input').forEach(i => {
    i.addEventListener('input', recalc);
    i.addEventListener('change', recalc);
  });

  const setDay = (k, v) => {
    $m.querySelector(`[name=sch_${k}_a]`).value = v.a;
    $m.querySelector(`[name=sch_${k}_b]`).value = v.b;
    $m.querySelector(`[name=sch_${k}_f]`).checked = v.float;
    $m.querySelector(`[name=sch_${k}_d]`).value = v.dur ?? '';
  };
  $m.querySelectorAll('[data-copy]').forEach(btn => btn.onclick = () => {
    const k = btn.dataset.copy;
    clip = {
      a: $m.querySelector(`[name=sch_${k}_a]`).value,
      b: $m.querySelector(`[name=sch_${k}_b]`).value,
      float: $m.querySelector(`[name=sch_${k}_f]`).checked,
      dur: $m.querySelector(`[name=sch_${k}_d]`).value,
    };
    $m.querySelectorAll('[data-paste], #sched-paste-all').forEach(b => b.disabled = false);
    toast('Время скопировано — жмите «Вст.» у нужных дней');
  });
  $m.querySelectorAll('[data-paste]').forEach(btn => btn.onclick = () => {
    if (clip) { setDay(btn.dataset.paste, clip); recalc(); }
  });
  const pasteAll = $m.querySelector('#sched-paste-all');
  if (pasteAll) pasteAll.onclick = () => {
    if (!clip) return;
    DAY_LIST.forEach(([k]) => setDay(k, clip));
    recalc();
  };

  recalc();
}

// собирает график из формы; бросает Error при неполном дне
function readScheduleTable($m) {
  const out = {};
  for (const [k, label] of DAY_LIST) {
    const a = $m.querySelector(`[name=sch_${k}_a]`).value;
    const b = $m.querySelector(`[name=sch_${k}_b]`).value;
    const f = $m.querySelector(`[name=sch_${k}_f]`).checked;
    const d = $m.querySelector(`[name=sch_${k}_d]`).value;
    if (!a && !b && !d) { out[k] = ''; continue; }
    if (!!a !== !!b) throw new Error(`${label}: заполните обе границы интервала или очистите день`);
    if (f) {
      if (!a || !b) throw new Error(`${label}: задайте рабочий интервал дня`);
      if (!d || parseFloat(d) <= 0) throw new Error(`${label}: укажите окно первого ответа (в часах) для дня «по 1-му ответу»`);
      out[k] = `${a}-${b}~${parseFloat(d)}`;
    } else {
      out[k] = a && b ? `${a}-${b}` : '';
    }
  }
  return out;
}

function permCheckboxesHtml(perms) {
  return `<div class="field" id="perm-block">
    <label>Права оператора</label>
    <div class="perm-grid">
      ${Object.entries(PERM_LABELS).map(([k, l]) => `
        <label class="perm-item"><input type="checkbox" name="perm_${k}"
          ${!perms || perms[k] ? 'checked' : ''}> ${l}</label>`).join('')}
    </div>
  </div>`;
}

function readPermCheckboxes($m) {
  const out = {};
  Object.keys(PERM_LABELS).forEach(k => {
    out[k] = $m.querySelector(`[name=perm_${k}]`).checked;
  });
  return out;
}

function bindRolePermToggle($m) {
  const roleSel = $m.querySelector('[name=role]');
  const toggle = () => $m.querySelector('#perm-block').classList.toggle('hidden', roleSel.value === 'owner');
  roleSel.addEventListener('change', toggle);
  toggle();
}

function modalOperatorCreate() {
  const $m = openModal(`
    <h2>Новый оператор</h2>
    <form id="op-form">
      <div class="field"><label>Логин (a-z, 0-9, _.-)</label><input name="login" required minlength="3" pattern="[a-zA-Z0-9_.\\-]+"></div>
      <div class="field"><label>Имя</label><input name="name" required></div>
      <div class="muted" style="font-size:12.5px; margin-bottom:12px">
        Пароль будет сгенерирован автоматически (временный). Оператор сменит его при первом входе.</div>
      <div class="field"><label>Роль</label>
        <select name="role"><option value="operator">operator</option><option value="owner">owner</option></select></div>
      ${permCheckboxesHtml(null)}
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" onclick="closeModal()">Отмена</button>
        <button type="submit" class="btn">Создать</button>
      </div>
    </form>`);
  bindRolePermToggle($m);
  $m.querySelector('#op-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    try {
      const body = { login: f.login.value.trim(), name: f.name.value.trim(), role: f.role.value };
      if (body.role === 'operator') body.permissions = readPermCheckboxes($m);
      const res = await api('/api/operators', { method: 'POST', body });
      showTempPasswordModal(res.operator.login, res.temp_password, 'Оператор создан');
      loadOperatorList();
    } catch (err) {
      toast(err.message, 'err');
    }
  });
}

function modalOperatorEdit(op) {
  const $m = openModal(`
    <h2>Оператор ${esc(op.login)}</h2>
    <form id="op-form">
      <div class="field"><label>Имя</label><input name="name" value="${esc(op.name)}"></div>
      <div class="field"><label>Роль</label>
        <select name="role">
          <option value="operator" ${op.role === 'operator' ? 'selected' : ''}>operator</option>
          <option value="owner" ${op.role === 'owner' ? 'selected' : ''}>owner</option>
        </select></div>
      ${permCheckboxesHtml(op.permissions)}
      <div class="row">
        <div class="field"><label>Оклад, ₽/мес (для «Активности»)</label>
          <input name="salary" type="number" min="0" step="500" value="${op.salary_base ?? ''}" placeholder="30000"></div>
        <div class="field"><label>Username в Telegram (без @)</label>
          <input name="tg" value="${esc(op.tg_username ?? '')}" placeholder="ivan_support"></div>
      </div>
      <div class="muted" style="font-size:12px; margin:-8px 0 12px">
        Укажите TG username — и ответы этого оператора из Telegram-треда будут
        засчитываться ему же в «Активности», а не отдельной строкой.</div>
      <div class="field">
        <label>График работы (пусто = выходной; конец 00:00 = до конца суток; конец меньше начала = смена через полночь)</label>
        <table class="sched-table">
          ${DAY_LIST.map(([k, label]) => schedRowHtml(k, label, (op.schedule || {})[k] || '')).join('')}
        </table>
        <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; margin-top:6px">
          <div class="muted" style="font-size:12.5px">
            Итого: <b id="sched-total">—</b> — из этих часов считаются норма и коэффициент.</div>
          <button type="button" class="btn btn-ghost btn-sm" id="sched-paste-all" disabled>Вставить во все дни</button>
        </div>
        <div class="muted" style="font-size:12px; margin-top:4px">
          «1-й ответ»: рабочий день начинается с первого ответа оператора, но не позже,
          чем через указанные часы от начала интервала (интервал 09:00–16:00 и окно 1 ч —
          старт где-то с 9 до 10). До старта ожидание клиентов не портит его рейтинг;
          в «Итого» идёт гарантированный минимум часов.</div>
      </div>
      <label style="display:flex;gap:8px;align-items:center;color:var(--text)">
        <input type="checkbox" name="active" style="width:auto" ${op.active ? 'checked' : ''}> Учётка активна
      </label>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" onclick="closeModal()">Отмена</button>
        <button type="submit" class="btn">Сохранить</button>
      </div>
      <div style="border-top:1px solid var(--border); margin-top:16px; padding-top:14px">
        <button type="button" class="btn btn-danger btn-sm" id="op-reset-pwd">Сбросить пароль</button>
        <div class="muted" style="font-size:12px; margin-top:6px">
          Будет сгенерирован новый временный пароль, оператор сменит его при входе.</div>
      </div>
    </form>`);
  $m.classList.add('modal-wide'); // таблица графика не влезает в обычную ширину
  bindRolePermToggle($m);
  bindScheduleTable($m);
  $m.querySelector('#op-reset-pwd').onclick = async () => {
    if (!confirm(`Сбросить пароль для «${op.login}»? Текущий пароль перестанет действовать.`)) return;
    try {
      const res = await api(`/api/operators/${op.id}/reset-password`, { method: 'POST' });
      showTempPasswordModal(res.login, res.temp_password, 'Пароль сброшен');
      loadOperatorList();
    } catch (err) {
      toast(err.message, 'err');
    }
  };
  $m.querySelector('#op-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    const body = { name: f.name.value.trim(), role: f.role.value, active: f.active.checked };
    if (f.role.value === 'operator') body.permissions = readPermCheckboxes($m);
    if (f.salary.value !== '') body.salary_base = parseInt(f.salary.value, 10);
    body.tg_username = f.tg.value.trim();
    try {
      body.schedule = readScheduleTable($m);
    } catch (err) {
      toast(err.message, 'err');
      return;
    }
    try {
      await api('/api/operators/' + op.id, { method: 'PATCH', body });
      closeModal();
      toast('Сохранено ✓');
      loadOperatorList();
    } catch (err) {
      toast(err.message, 'err');
    }
  });
}

function showTempPasswordModal(login, tempPassword, title) {
  const $m = openModal(`
    <h2>${esc(title)}</h2>
    <div class="muted" style="margin-bottom:12px; font-size:13px">
      Передайте оператору логин и временный пароль. Пароль показывается
      <b>только один раз</b> — при первом входе оператор установит свой.</div>
    <div class="confirm-box">
      Логин: <b class="mono">${esc(login)}</b><br>
      Временный пароль: <b class="mono" id="tmp-pwd">${esc(tempPassword)}</b>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" id="tmp-copy">Скопировать</button>
      <button class="btn" onclick="closeModal()">Готово</button>
    </div>`);
  $m.querySelector('#tmp-copy').onclick = async () => {
    try {
      await navigator.clipboard.writeText(`Логин: ${login}\nВременный пароль: ${tempPassword}`);
      toast('Скопировано ✓');
    } catch (e) {
      toast('Не удалось скопировать — выделите вручную', 'err');
    }
  };
}

// ================================================================ router

function setNav(active) {
  $topbar.classList.remove('hidden');
  document.getElementById('me-label').textContent =
    S.me ? `${S.me.name} (${S.me.role})` : '';
  document.querySelectorAll('.owner-only').forEach(el =>
    el.classList.toggle('hidden', !S.me || S.me.role !== 'owner'));
  document.querySelectorAll('#topbar nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.nav === active));
}

async function render() {
  const hash = location.hash || '#/search';
  if (S.ticketTimer) { clearInterval(S.ticketTimer); S.ticketTimer = null; }
  if (S.chatPoll) { S.chatPoll.abort(); S.chatPoll = null; }
  if (S.onResize) { window.removeEventListener('resize', S.onResize); S.onResize = null; }

  if (!S.token) {
    if (!hash.startsWith('#/login')) S.nextHash = hash;
    viewLogin(); return;
  }
  if (!S.me) {
    const ok = await loadMe();
    if (!ok) {
      if (!hash.startsWith('#/login')) S.nextHash = hash;
      viewLogin(); return;
    }
  }

  if (S.me && S.me.must_change_password) { viewForcePassword(); return; }

  const userMatch = hash.match(/^#\/user\/(\d+)$/);
  if (userMatch) { setNav('search'); viewUser(parseInt(userMatch[1], 10)); return; }
  const ticketMatch = hash.match(/^#\/ticket\/(\d+)$/);
  if (ticketMatch) { setNav('tickets'); viewTicket(parseInt(ticketMatch[1], 10)); return; }
  if (hash.startsWith('#/tickets')) { setNav('tickets'); viewTickets(); return; }
  if (hash.startsWith('#/stats')) { setNav('stats'); viewStats(); return; }
  if (hash.startsWith('#/audit')) { setNav('audit'); viewAudit(); return; }
  if (hash.startsWith('#/operators')) { setNav('operators'); viewOperators(); return; }
  if (hash.startsWith('#/login')) {
    if (S.me) { location.hash = '#/search'; return; }
    viewLogin(); return;
  }
  setNav('search');
  viewSearch();
}

window.addEventListener('hashchange', render);
render();

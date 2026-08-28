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

// выбранный тариф подписки (vpn.period основного бота, в днях)
function periodLabel(p) {
  const map = { 1: 'Ежедневный (1 день)', 30: '1 месяц', 90: '3 месяца', 1095: '3 года' };
  if (p == null || p === '') return '—';
  return map[p] || `${p} дн.`;
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
  S.mistakes = undefined;
  S.mistakesPending = undefined;
  S.qaOps = null;
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

// откуда пришли в карточку: из чата тикета «Назад» ведёт обратно в чат
function userBackHref() {
  return S.prevHash && S.prevHash.startsWith('#/ticket/') ? S.prevHash : '#/search';
}
function userBackLabel() {
  return S.prevHash && S.prevHash.startsWith('#/ticket/') ? '← К чату тикета' : '← К поиску';
}

async function viewUser(userId) {
  $view.innerHTML = spinnerHtml('Загружаем карточку…');
  let u;
  try {
    u = await api('/api/users/' + userId);
  } catch (err) {
    $view.innerHTML = `<div class="card"><div class="error-note">${esc(err.message)}</div>
      <div style="margin-top:12px"><a href="${userBackHref()}" class="btn btn-ghost">${userBackLabel()}</a></div></div>`;
    return;
  }
  const ud = u.user_data || {};
  const vpn = u.vpn || {};
  const reload = () => viewUser(userId);

  $view.innerHTML = `
    <div style="margin-bottom:12px"><a href="${userBackHref()}" class="muted" style="text-decoration:none">${userBackLabel()}</a></div>
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
        <div class="stat"><div class="stat-label">Тариф</div><div class="stat-value" style="font-size:16px" title="Выбранный период — по нему проходит автопродление">${esc(periodLabel(vpn.period))}</div></div>
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
      <div id="tk-stats" class="stats-grid" style="margin-bottom:14px"></div>
      <div class="filter-bar">
        <select id="tk-filter">
          <option value="" ${tk.status === '' ? 'selected' : ''}>Все статусы</option>
          <option value="pending" ${tk.status === 'pending' ? 'selected' : ''}>🟡 Ожидают</option>
          <option value="open" ${tk.status === 'open' ? 'selected' : ''}>🟢 У оператора</option>
          <option value="closed" ${tk.status === 'closed' ? 'selected' : ''}>🔴 Закрытые</option>
        </select>
        <form id="tk-search" style="display:flex; gap:8px; flex:1 1 230px; max-width:300px">
          <input name="uid" type="number" min="1" placeholder="ID пользователя"
            inputmode="numeric" style="flex:1; min-width:0">
          <button class="btn btn-sm" type="submit" style="flex:0 0 auto">Найти</button>
        </form>
        <button class="btn btn-ghost btn-sm" id="tk-sound" type="button"
          style="flex:0 0 auto" title="Звуковой сигнал при новом обращении"></button>
        <span class="muted" style="align-self:center; font-size:12px; margin-left:auto" id="tk-updated"></span>
      </div>
      <div id="tk-list">${spinnerHtml()}</div>
    </div>`;
  const soundBtn = document.getElementById('tk-sound');
  const drawSound = () => soundBtn.textContent = soundEnabled() ? 'Звук: вкл' : 'Звук: выкл';
  drawSound();
  soundBtn.onclick = () => {
    const on = !soundEnabled();
    localStorage.setItem('op_sound', on ? '1' : '0');
    drawSound();
    if (on) beep(); // заодно проверка, что звук работает
  };
  document.getElementById('tk-search').onsubmit = async e => {
    e.preventDefault();
    const uid = parseInt(e.target.uid.value, 10);
    if (!uid) { toast('Введите ID пользователя', 'err'); return; }
    try {
      await api('/api/tickets/' + uid + '?limit=1');
      location.hash = '#/ticket/' + uid;
    } catch (err) {
      toast(err.status === 404 ? `Тикет пользователя ${uid} не найден` : err.message, 'err');
    }
  };
  document.getElementById('tk-filter').onchange = e => {
    tk.status = e.target.value;
    tk.page = 1;
    loadTickets();
  };
  loadTickets();
  loadTicketSummary();
  // автообновление списка — можно сидеть и ждать новые тикеты
  S.ticketTimer = setInterval(() => { loadTickets(true); loadTicketSummary(); }, 5000);
}

async function loadTicketSummary() {
  const el = document.getElementById('tk-stats');
  if (!el) return;
  let s;
  try { s = await api('/api/ticket-stats/summary'); }
  catch (e) { return; } // сводка не критична — список работает и без неё
  el.innerHTML = `
    <div class="stat"><div class="stat-label">🟡 Ожидают</div>
      <div class="stat-value">${fmtNum(s.pending)}</div></div>
    <div class="stat"><div class="stat-label">🟢 У оператора</div>
      <div class="stat-value">${fmtNum(s.open)}</div></div>
    <div class="stat"><div class="stat-label">Обращений сегодня</div>
      <div class="stat-value">${fmtNum(s.today.appeals)}</div>
      <div class="muted" style="font-size:11.5px; margin-top:2px">за последний час: ${fmtNum(s.today.last_hour)}</div></div>
    <div class="stat"><div class="stat-label">Отвечено сегодня</div>
      <div class="stat-value">${fmtNum(s.today.answered)}</div></div>
    <div class="stat"><div class="stat-label">Без ответа сегодня</div>
      <div class="stat-value"${s.today.unanswered ? ' style="color:var(--red)"' : ''}>${fmtNum(s.today.unanswered)}</div></div>`;
}

// ================================================================ оповещения: звук + бейдж на вкладке

const FAVICON_BASE = (document.querySelector('link[rel=icon]') || {}).href || '';
let alertPrevWaiting = null;

function soundEnabled() { return localStorage.getItem('op_sound') !== '0'; }

function beep() {
  if (!soundEnabled()) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type = 'sine';
    o.frequency.setValueAtTime(880, ctx.currentTime);
    o.frequency.setValueAtTime(660, ctx.currentTime + 0.18);
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.5);
    o.start(); o.stop(ctx.currentTime + 0.55);
    o.onended = () => ctx.close();
  } catch (e) { /* звук заблокирован браузером до первого клика — не страшно */ }
}

function setAlertBadge(n) {
  document.title = (n > 0 ? `(${n}) ` : '') + 'Панель оператора';
  const link = document.querySelector('link[rel=icon]');
  if (!link) return;
  if (!n) { if (FAVICON_BASE) link.href = FAVICON_BASE; return; }
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const x = c.getContext('2d');
  x.fillStyle = '#141414';
  x.beginPath(); x.roundRect(0, 0, 64, 64, 16); x.fill();
  x.fillStyle = '#ffffff'; x.font = '800 26px Arial'; x.textAlign = 'center';
  x.fillText('RS', 30, 46);
  x.fillStyle = '#dc2626';
  x.beginPath(); x.arc(46, 18, 17, 0, 7); x.fill();
  x.fillStyle = '#ffffff'; x.font = '800 20px Arial';
  x.fillText(n > 9 ? '9+' : String(n), 46, 25);
  link.href = c.toDataURL('image/png');
}

async function pollAlerts() {
  if (!S.token || !S.me) return;
  let s;
  try { s = await api('/api/ticket-stats/summary'); } catch (e) { return; }
  const n = s.waiting_now;
  if (alertPrevWaiting !== null && n > alertPrevWaiting) beep();
  alertPrevWaiting = n;
  setAlertBadge(n);
  pollRatings();
  pollMistakes();
}
setInterval(pollAlerts, 15000);
setTimeout(pollAlerts, 3000);

// ---- уведомления об оценках: колокольчик в шапке ----
let ratingItems = [];

function ratingSeenTs() { return parseInt(localStorage.getItem('op_rate_seen') || '0', 10); }

async function pollRatings() {
  try { ratingItems = (await api('/api/notifications/ratings?days=7&limit=30')).items; }
  catch (e) { return; }
  const badge = document.getElementById('notif-badge');
  if (!badge) return;
  const unseen = ratingItems.filter(i => new Date(i.timestamp).getTime() > ratingSeenTs()).length;
  badge.textContent = unseen > 9 ? '9+' : String(unseen);
  badge.classList.toggle('hidden', unseen === 0);
}

function toggleNotifPanel() {
  const old = document.getElementById('notif-panel');
  if (old) { old.remove(); return; }
  const btn = document.getElementById('notif-btn');
  const rect = btn.getBoundingClientRect();
  const panel = document.createElement('div');
  panel.id = 'notif-panel';
  panel.style.top = (rect.bottom + 8) + 'px';
  panel.style.right = Math.max(8, window.innerWidth - rect.right) + 'px';
  const seen = ratingSeenTs();
  panel.innerHTML = ratingItems.length ? ratingItems.map(i => {
    const fresh = new Date(i.timestamp).getTime() > seen;
    const who = `${esc(i.first_name || '')}${i.username ? ' @' + esc(i.username) : ''}`.trim() || '#' + i.user_id;
    return `<div class="notif-item" data-uid="${esc(i.user_id)}" ${fresh ? 'style="background:var(--card-2)"' : ''}>
      <span class="notif-stars ${i.stars <= 2 ? 'low' : ''}">${'★'.repeat(i.stars)}${'☆'.repeat(5 - i.stars)}</span>
      <span style="font-size:13px"> ${who}</span>
      <div class="muted" style="font-size:11.5px; margin-top:2px">
        ${i.operator_login ? 'оператор ' + esc(i.operator_login) + ' · ' : ''}${fmtDate(i.timestamp)}</div>
    </div>`;
  }).join('')
    : '<div class="center" style="padding:14px 0">Оценок за неделю нет</div>';
  document.body.appendChild(panel);
  panel.querySelectorAll('.notif-item').forEach(el => el.onclick = () => {
    panel.remove();
    location.hash = '#/ticket/' + el.dataset.uid;
  });
  localStorage.setItem('op_rate_seen', String(Date.now()));
  const badge = document.getElementById('notif-badge');
  if (badge) badge.classList.add('hidden');
  const closer = (e) => {
    if (!panel.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
      panel.remove();
      document.removeEventListener('click', closer);
    }
  };
  setTimeout(() => document.addEventListener('click', closer), 0);
}
document.getElementById('notif-btn').onclick = toggleNotifPanel;

// безопасный рендер телеграм-разметки: экранируем всё, затем возвращаем
// только белый список тегов, которыми бот форматирует сообщения
function tgHtml(text) {
  let s = esc(text || '');
  s = s.replace(/&lt;(\/?)(b|strong|i|em|u|s|code|pre|blockquote)&gt;/gi, '<$1$2>');
  s = s.replace(/&lt;br\s*\/?&gt;/gi, '<br>');
  s = s.replace(/&lt;a href=&quot;(https?:\/\/[^"&<>\s]+)&quot;&gt;/gi,
    '<a href="$1" target="_blank" rel="noopener">');
  s = s.replace(/&lt;\/a&gt;/gi, '</a>');
  // прочие теги (tg-emoji и т.п.) прячем, чтобы не пугали разметкой
  s = s.replace(/&lt;\/?tg-emoji[^&]*&gt;/gi, '');
  return s;
}

// тикет ждёт ответа: последнее слово за пользователем, тикет не закрыт
function ticketAwaiting(t) {
  return t.status !== 'closed' && t.last_message && t.last_message.direction === 'user';
}

const TK_SORT_LABELS = [
  ['status', 'Статус'],
  ['user', 'Пользователь'],
  ['pending_at', 'Дата обращения'],
  ['last_message', 'Последнее сообщение'],
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
    <tr>${ths}</tr>
    ${data.items.map(t => `
      <tr style="cursor:pointer" class="${ticketAwaiting(t) ? 'tk-unanswered' : ''}"
          onclick="location.hash='#/ticket/${t.user_id}'">
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
        ${t.tg_thread_url
          ? `<a class="btn btn-ghost" href="${esc(t.tg_thread_url)}" target="_blank" rel="noopener"
               title="Открыть тред этого тикета в саппорт-чате Telegram">Тред в Telegram</a>` : ''}
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
            placeholder="Текст ответа… (Enter — отправить, Shift+Enter — новая строка)"></textarea></div>
        <input type="file" id="tk-photo" accept="image/*" class="hidden">
        <div id="tk-photo-preview" class="hidden" style="margin-bottom:10px"></div>
        <div id="tk-ai-status" class="muted hidden" style="font-size:12.5px; margin-bottom:10px"></div>
        <div style="display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap">
          <div style="display:flex; gap:8px; flex-wrap:wrap">
            <button class="btn btn-ghost" type="button" id="tk-attach">Прикрепить фото</button>
            <button class="btn btn-ghost" type="button" id="tk-quick">Быстрые ответы</button>
            <button class="btn btn-ghost" type="button" id="tk-ai">Ответ ИИ</button>
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
    S.sidePage = null; // фильтр сменился — заново ищем страницу тикета
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
    loadSideTickets(userId, true);
  };
  document.getElementById('tk-refresh').onclick = refresh;
  // список справа — раз в 5 секунд, чат — мгновенно через long-poll ниже
  S.ticketTimer = setInterval(() => loadSideTickets(userId, true), 5000);

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
          loadSideTickets(userId, true);
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
    // поле ответа растёт под текст, но не выше max-height (дальше — свой скролл)
    const ta = form.text;
    const growTa = () => {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight + 2, 220) + 'px';
    };
    ta.addEventListener('input', growTa);
    growTa();
    // черновик живёт в localStorage: уход со страницы/обновление его не теряют
    const draftKey = 'op_draft_' + userId;
    const savedDraft = localStorage.getItem(draftKey);
    if (!ta.value && savedDraft) {
      ta.value = savedDraft;
      growTa();
    }
    ta.addEventListener('input', () => {
      if (ta.value.trim()) localStorage.setItem(draftKey, ta.value);
      else localStorage.removeItem(draftKey); // отправили/стёрли — черновик не нужен
    });
    // на ПК Enter отправляет, Shift+Enter — перенос строки;
    // на сенсорных устройствах Enter остаётся переносом (кнопка «Отправить»)
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing
          && !window.matchMedia('(pointer: coarse)').matches) {
        e.preventDefault();
        if (ta.value.trim() || photoInput.files.length) form.requestSubmit();
      }
    });
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

    // черновик ИИ готовится ТОЛЬКО по кнопке — при открытии тикета ничего
    // не генерируется само (не тратим токены и не дёргаем поле ввода)
    const generateAiDraft = async () => {
      aiBtn.disabled = true;
      setAiStatus('<span class="spinner"></span> ИИ готовит черновик ответа…');
      try {
        const data = await api(`/api/tickets/${userId}/suggest`, { method: 'POST' });
        form.text.value = data.suggestion;
        form.text.dispatchEvent(new Event('input'));
        lastAiDraft = data.suggestion;
        setAiStatus('Черновик ИИ вставлен в поле — проверьте и поправьте текст перед отправкой');
      } catch (err) {
        if (err.status === 503) {
          // не настроено на сервере: кнопку НЕ прячем — показываем причину
          S.aiDisabled = err.message || 'ИИ-помощник не настроен на сервере';
          setAiStatus('ИИ-помощник недоступен: ' + esc(S.aiDisabled));
          toast(err.message, 'err');
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
      generateAiDraft();
    };

    // ---- быстрые ответы: общие с ботом, вставляются в поле ----
    document.getElementById('tk-quick').onclick = () => openQuickReplies(text => {
      const typed = form.text.value.trim();
      if (typed && typed !== lastAiDraft
          && !confirm('Заменить текст в поле выбранным быстрым ответом?')) return false;
      form.text.value = text;
      form.text.dispatchEvent(new Event('input'));
      lastAiDraft = text; // чтобы следующая вставка/ИИ не спрашивали про этот текст
      setAiStatus('');
      form.text.focus();
      return true;
    });
    // если сервер уже отвечал «ИИ не настроен» — сразу показываем причину
    if (S.aiDisabled) setAiStatus('ИИ-помощник недоступен: ' + esc(S.aiDisabled));

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
        form.text.dispatchEvent(new Event('input'));
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
async function loadSideTickets(activeUserId, silent = false) {
  const $list = document.getElementById('side-list');
  if (!$list) return;
  if (!window.matchMedia('(min-width: 1100px)').matches) return;
  // сортировка живая (по последнему сообщению): фоновое обновление на
  // страницах дальше первой не дёргает список — тикеты не «уезжают»
  // из-под курсора, пока оператор листает. Вернулся на стр. 1 — обновления идут.
  if (silent && (S.sidePage || 1) > 1) return;
  // список живёт по последнему сообщению; при смене тикета открываем
  // страницу, на которой он находится (locate на бэке)
  if (S.sideFor !== activeUserId) { S.sideFor = activeUserId; S.sidePage = null; }
  const qs = new URLSearchParams({ page: S.sidePage || 1, page_size: 30,
    sort: 'last_message', order: 'desc' });
  if (S.tk && S.tk.status) qs.set('status', S.tk.status);
  if (!S.sidePage) qs.set('locate', activeUserId);
  let data;
  try {
    data = await api('/api/tickets?' + qs);
  } catch (e) {
    return; // тихо: следующая попытка через 5 секунд
  }
  S.sidePage = data.page;
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
    <div class="side-row ${m.user_id === activeUserId ? 'active' : ''} ${ticketAwaiting(m) ? 'tk-unanswered' : ''}"
         data-uid="${esc(m.user_id)}">
      <div class="side-row-top">
        ${dot(m.status)}
        <span class="side-name">${esc(m.first_name || '—')}${m.username ? ' <span class="muted">@' + esc(m.username) + '</span>' : ''}</span>
        <span class="side-date muted">${shortDate(m.pending_at)}</span>
      </div>
      <div class="side-last muted">${m.last_message ? esc(m.last_message.text || 'вложение') : '—'}</div>
    </div>`).join('') +
    (Math.ceil(data.total / data.page_size) > 1
      ? `<div class="side-pager">
          <button class="btn btn-ghost btn-sm" id="side-prev" ${data.page <= 1 ? 'disabled' : ''}>←</button>
          <span class="muted">стр. ${data.page} / ${Math.ceil(data.total / data.page_size)}</span>
          <button class="btn btn-ghost btn-sm" id="side-next" ${data.page >= Math.ceil(data.total / data.page_size) ? 'disabled' : ''}>→</button>
        </div>`
      : '')
    : '<div class="center" style="padding:14px 0">Тикетов нет</div>';
  const flip = d => {
    S.sidePage = data.page + d;
    $list.dataset.key = '';
    loadSideTickets(activeUserId);
  };
  const pv = $list.querySelector('#side-prev'), nx = $list.querySelector('#side-next');
  if (pv) pv.onclick = e => { e.stopPropagation(); flip(-1); };
  if (nx) nx.onclick = e => { e.stopPropagation(); flip(1); };

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
      «Мои» видны только вам. «Общие» — единая база с ботом: это же пункты
      меню самопомощи, их видят все пользователи. Клик по названию вставляет
      текст в поле ответа.</div>
    <input id="qr-search" placeholder="Поиск по названию или тексту…" style="margin-bottom:10px">
    <div id="qr-list" style="max-height:52vh; overflow-y:auto">${spinnerHtml()}</div>
    <div class="modal-actions">
      <button class="btn btn-ghost" id="qr-add-my">+ Мой ответ</button>
      <button class="btn btn-ghost" id="qr-add">+ Общий (в бота)</button>
      <button class="btn" id="qr-close">Закрыть</button>
    </div>`);
  const $list = $m.querySelector('#qr-list');
  let mine = [];
  let shared = [];

  const itemHtml = (i, personal) => `
      <div class="qr-item">
        <div class="qr-main" data-use="${personal ? 'm' : 's'}:${esc(i.id)}">
          <div class="qr-title">${esc(i.title)}
            ${!personal && !i.active ? ' <span class="badge badge-gray">выключен</span>' : ''}</div>
          <div class="qr-preview muted">${tgHtml(i.text.slice(0, 160))}${i.text.length > 160 ? '…' : ''}</div>
        </div>
        <div class="qr-btns">
          <button class="btn btn-ghost btn-sm" data-edit="${personal ? 'm' : 's'}:${esc(i.id)}">Изменить</button>
          <button class="btn btn-ghost btn-sm" data-del="${personal ? 'm' : 's'}:${esc(i.id)}">Удалить</button>
        </div>
      </div>`;

  const findItem = (ref) => {
    const personal = ref.startsWith('m:');
    const id = ref.slice(2);
    const item = (personal ? mine : shared).find(i => i.id === id);
    return item ? { item, personal } : null;
  };

  const renderList = () => {
    const q = $m.querySelector('#qr-search').value.trim().toLowerCase();
    const match = i => !q || i.title.toLowerCase().includes(q) || i.text.toLowerCase().includes(q);
    const mineF = mine.filter(match);
    const sharedF = shared.filter(match);
    $list.innerHTML =
      `<div class="qr-sec">Мои — видны только вам</div>`
      + (mineF.length ? mineF.map(i => itemHtml(i, true)).join('')
        : '<div class="center muted" style="padding:8px 0; font-size:12.5px">пусто — добавьте кнопкой «+ Мой ответ»</div>')
      + `<div class="qr-sec">Общие — меню бота, видят все операторы и пользователи</div>`
      + (sharedF.length ? sharedF.map(i => itemHtml(i, false)).join('')
        : '<div class="center muted" style="padding:8px 0; font-size:12.5px">пусто</div>');

    $list.querySelectorAll('[data-use]').forEach(el => el.onclick = () => {
      const found = findItem(el.dataset.use);
      if (found && insert(found.item.text) !== false) closeModal();
    });
    $list.querySelectorAll('[data-edit]').forEach(el => el.onclick = () => {
      const found = findItem(el.dataset.edit);
      if (found) qrEditModal(found.item, insert, found.personal);
    });
    $list.querySelectorAll('[data-del]').forEach(el => el.onclick = async () => {
      const found = findItem(el.dataset.del);
      if (!found) return;
      const warn = found.personal
        ? `Удалить ваш быстрый ответ «${found.item.title}»?`
        : `Удалить общий быстрый ответ «${found.item.title}»? Он пропадёт и из меню бота.`;
      if (!confirm(warn)) return;
      try {
        await api((found.personal ? '/api/my-quick-replies/' : '/api/quick-replies/') + found.item.id,
          { method: 'DELETE' });
        toast('Удалено ✓');
        load();
      } catch (err) { toast(err.message, 'err'); }
    });
  };

  const load = async () => {
    try {
      const [m, s] = await Promise.all([
        api('/api/my-quick-replies'), api('/api/quick-replies?all=true')]);
      mine = m.items;
      shared = s.items;
      renderList();
    } catch (err) {
      $list.innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
    }
  };
  $m.querySelector('#qr-search').oninput = renderList;
  $m.querySelector('#qr-add-my').onclick = () => qrEditModal(null, insert, true);
  $m.querySelector('#qr-add').onclick = () => qrEditModal(null, insert, false);
  $m.querySelector('#qr-close').onclick = closeModal;
  load();
}

function qrEditModal(item, insert, personal = false) {
  const kind = personal ? 'личный' : 'общий';
  const $m = openModal(`
    <h2>${item ? `Изменить ${kind} ответ` : `Новый ${kind} ответ`}</h2>
    ${personal ? '<div class="muted" style="font-size:12px; margin-bottom:10px">Личный ответ видите только вы — в меню бота он не попадает.</div>' : ''}
    <form id="qr-form">
      <div class="field"><label>${personal ? 'Название (для себя)' : 'Название (это текст кнопки в боте и на сайте)'}</label>
        <input name="title" required maxlength="64" value="${item ? esc(item.title) : ''}"></div>
      <div class="field"><label>Текст ответа пользователю</label>
        <textarea name="text" rows="8" required maxlength="3500">${item ? esc(item.text) : ''}</textarea>
        <div class="muted" style="font-size:11.5px; margin-top:4px">Можно использовать теги Telegram:
          &lt;b&gt; &lt;i&gt; &lt;u&gt; &lt;s&gt; &lt;code&gt; &lt;blockquote&gt; &lt;a href="…"&gt;</div></div>
      <div class="field"><label>Как увидит пользователь</label>
        <div id="qr-preview" class="qr-live-preview"></div></div>
      ${item && !personal ? `<label style="display:flex; gap:8px; align-items:center; color:var(--text); margin-bottom:12px">
        <input type="checkbox" name="active" style="width:auto" ${item.active ? 'checked' : ''}>
        Активен (виден в боте и в списке вставки)
      </label>` : ''}
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" id="qr-back">Назад</button>
        <button type="submit" class="btn">Сохранить</button>
      </div>
    </form>`);
  const pv = $m.querySelector('#qr-preview');
  const drawPv = () => pv.innerHTML = tgHtml($m.querySelector('[name=text]').value) || '<span class="muted">пусто</span>';
  $m.querySelector('[name=text]').addEventListener('input', drawPv);
  drawPv();
  $m.querySelector('#qr-back').onclick = () => openQuickReplies(insert);
  $m.querySelector('#qr-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    const body = { title: f.title.value.trim(), text: f.text.value.trim() };
    const base = personal ? '/api/my-quick-replies' : '/api/quick-replies';
    try {
      if (item) {
        if (!personal) body.active = f.active.checked;
        await api(base + '/' + item.id, { method: 'PATCH', body });
      } else {
        await api(base, { method: 'POST', body });
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
  // «прилипание» к низу: пока оператор внизу — держим его на последних
  // сообщениях (и при перерисовке, и когда подгружаются картинки);
  // если он листает историю выше — позицию не трогаем
  if (!$chat.dataset.hook) {
    $chat.dataset.hook = '1';
    $chat.dataset.stick = '1';
    $chat.addEventListener('scroll', () => {
      $chat.dataset.stick =
        $chat.scrollTop + $chat.clientHeight >= $chat.scrollHeight - 80 ? '1' : '0';
    });
    // вложение загрузилось и раздвинуло чат — доскролливаем, если липнем к низу
    $chat.addEventListener('load', () => {
      if ($chat.dataset.stick !== '0') $chat.scrollTop = $chat.scrollHeight;
    }, true);
  }
  const key = JSON.stringify(messages.map(m => m.timestamp));
  if ($chat.dataset.key === key) return; // ничего нового — не перерисовываем (не сбрасываем плееры)
  $chat.dataset.key = key;
  if (!messages.length) {
    $chat.innerHTML = '<div class="center">Сообщений пока нет</div>';
    return;
  }
  const stick = $chat.dataset.stick !== '0';
  const keepPos = $chat.scrollTop;
  $chat.innerHTML = messages.map(m => {
    const cls = m.direction === 'operator' ? 'msg-operator' : m.direction === 'system' ? 'msg-system' : 'msg-user';
    const who = m.direction === 'operator'
      ? (m.operator_login ? esc(m.operator_login) + (m.source === 'tg' ? ' (TG)' : ' (сайт)') : 'оператор')
      : m.direction === 'system' ? '' : 'пользователь';
    const text = m.text ? `<div class="msg-text">${tgHtml(m.text)}</div>` : '';
    return `<div class="msg ${cls}">${text}${attachmentHtml(m)}<div class="msg-meta">${who ? who + ' · ' : ''}${fmtDate(m.timestamp)}</div></div>`;
  }).join('');
  hydrateAttachments($chat);
  $chat.scrollTop = stick ? $chat.scrollHeight : keepPos;
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

  // расшифровка расчёта для владельца: за что баллы и как вышли норма/коэфф/выплата
  const breakdownHtml = (r, data) => {
    const p = data.points;
    const a = data.settings || {};
    const replyPts = r.replies * p.reply;
    const fastPts = r.fast * p.fast;
    const ratingPts = r.score - replyPts - fastPts; // остаток — ровно вклад оценок
    const sign = v => (v > 0 ? '+' : '') + fmtNum(v);
    const modeTxt = a.norm_mode === 'manual' ? 'задана вручную'
      : a.norm_mode === 'team_avg' ? 'средний темп команды' : 'по нагрузке периода';

    const lines = [];
    lines.push(`<b>Баллы — ${fmtNum(r.score)}</b>`);
    lines.push(`ответы: ${r.replies} × ${p.reply} = ${fmtNum(replyPts)}`);
    lines.push(`быстрые первые ответы (≤${p.fast_threshold_min} мин): ${r.fast} × ${p.fast} = ${fmtNum(fastPts)}`);
    lines.push(r.rating_count
      ? `оценки: ${r.rating_count} шт., средняя ★${r.rating_avg} → ${sign(ratingPts)} (каждая даёт ±${p.rating_step}×(звёзды−3))`
      : 'оценки: не было → 0');
    lines.push(`закрыто тикетов: ${r.closes} — справочно, баллов не даёт`);

    if (r.norm_points) {
      lines.push(`<b>Норма — ${fmtNum(r.norm_points)}</b>: ${data.norm_used} баллов/час (${modeTxt}) × ${r.hours_period ?? '?'} ч оператора за период`);
    } else if (r.coeff != null) {
      lines.push('<b>Норма</b>: нагрузки в периоде не было — коэффициент 1.0, простой не в минус');
    } else {
      lines.push('<b>Норма</b>: не считается — у оператора нет часов в графике за этот период');
    }

    if (r.coeff != null && r.norm_points) {
      const raw = r.score / r.norm_points;
      lines.push(`<b>Коэффициент — ×${r.coeff.toFixed(2)}</b>: ${fmtNum(r.score)} ÷ ${fmtNum(r.norm_points)} = ${raw.toFixed(2)}`
        + (r.coeff.toFixed(2) !== raw.toFixed(2) ? `, зажат в пределы ×${a.coeff_min}–×${a.coeff_max}` : ''));
    } else if (r.coeff != null) {
      lines.push(`<b>Коэффициент — ×${r.coeff.toFixed(2)}</b>`);
    }

    if ('salary_base' in r) {
      if (r.payout != null) {
        lines.push(`<b>К выплате — ${fmtNum(r.payout)} ₽</b>: оклад ${fmtNum(r.salary_base)} ₽/мес × ${r.coeff.toFixed(2)} × ${data.days} дн ÷ 30.44 (доля месяца)`);
      } else {
        lines.push(`<b>К выплате</b>: ${r.salary_base ? 'коэффициент не посчитан' : 'оклад не задан в карточке оператора'}`);
      }
    }
    if ('work_rhythm' in r) {
      lines.push(r.work_rhythm
        ? `<b>Рабочий ритм</b>: активных дней ${r.work_rhythm.days}; в среднем начинает в ${r.work_rhythm.avg_start}, заканчивает в ${r.work_rhythm.avg_end} (по первому и последнему ответу за день)`
        : '<b>Рабочий ритм</b>: ответов за период не было');
    }
    if (r.schedule) lines.push(`<span class="muted">График: ${esc(scheduleSummary(r.schedule))}</span>`);
    return lines.join('<br>');
  };

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
        <th>Скорость (медиана)</th><th>Быстрых ≤${data.points.fast_threshold_min} мин</th><th>Оценка</th>
        ${showPay ? '<th>Раб. день (ср.)</th>' : ''}<th>График</th></tr>
      ${rows.map((r, i) => `
        <tr class="${r.login === S.me.login ? 'st-me' : ''}" data-i="${i}">
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
          ${showPay ? `<td style="white-space:nowrap"${r.work_rhythm ? ` title="Среднее время первого и последнего ответа за день (активных дней: ${r.work_rhythm.days})"` : ''}>${r.work_rhythm ? `${r.work_rhythm.avg_start}–${r.work_rhythm.avg_end}` : '—'}</td>` : ''}
          <td${r.schedule ? ` title="${esc(scheduleSummary(r.schedule))}"` : ''}>${r.hours_per_week != null ? esc(r.hours_per_week) + ' ч/нед' : '—'}${r.schedule ? ' <span class="muted" style="cursor:help">ⓘ</span>' : ''}</td>
        </tr>`).join('')}
    </table></div>`
      : '<div class="center">За выбранный период активности нет</div>';

    // владелец может раскрыть оператора и увидеть, за что и как посчиталось
    if (S.me.role === 'owner') {
      $t.querySelectorAll('tr[data-i]').forEach(tr => {
        tr.classList.add('st-click');
        tr.title = 'Нажмите, чтобы увидеть расчёт';
        tr.onclick = () => {
          const next = tr.nextElementSibling;
          const wasOpen = next && next.classList.contains('st-detail');
          $t.querySelectorAll('.st-detail').forEach(x => x.remove());
          if (wasOpen) return;
          const r = rows[parseInt(tr.dataset.i, 10)];
          tr.insertAdjacentHTML('afterend',
            `<tr class="st-detail"><td colspan="${tr.children.length}">${breakdownHtml(r, data)}</td></tr>`);
        };
      });
    }

    // раздутые часы у одного оператора съедают норму всей команды — подсветим
    if (S.me.role === 'owner') {
      const fat = rows.filter(r => (r.hours_per_week || 0) > 84);
      if (fat.length) {
        $t.insertAdjacentHTML('beforeend', `<div class="error-note" style="margin-top:10px">
          Похоже на ошибку в графике: ${fat.map(r => `${esc(r.name)} — ${esc(r.hours_per_week)} ч/нед`).join(', ')}.
          Такие часы раздувают часы команды: норма остальных занижается, а их коэффициенты
          взлетают к максимуму. Исправьте график в карточке оператора (страница «Операторы»).</div>`);
      }
    }

    const p = data.points;
    const a = data.settings || {};
    const workWin = a.work_start === a.work_end ? 'круглосуточно' : `${a.work_start}–${a.work_end} (UTC+${a.tz_offset_hours})`;
    document.getElementById('st-formula').innerHTML =
      `Баллы: ответ +${p.reply} · быстрый первый ответ (≤${p.fast_threshold_min} мин) ещё +${p.fast} · ` +
      `оценка пользователя ±${p.rating_step}×(звёзды−3), т.е. 5★ = +${p.rating_step * 2}, 1★ = −${p.rating_step * 2}.<br>` +
      `Коэффициент = баллы ÷ норма, в пределах ×${a.coeff_min}–×${a.coeff_max}. ` +
      (a.norm_mode === 'manual'
        ? `Норма = ${a.norm_points_per_hour} баллов/час × часы оператора за период. `
        : a.norm_mode === 'team_avg'
          ? `Норма — средний темп команды за период` +
            (data.norm_used ? ` (${data.norm_used} баллов/час)` : ' (пока нет данных)') +
            `, × часы оператора. `
          : `Норма — по нагрузке: за период отвечено на ${(data.demand || {}).answered ?? '—'} обращений, ` +
            `реально брошено без ответа ${(data.demand || {}).backlog ?? '—'} — ` +
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
    let cfg, ac, esc2;
    try {
      [cfg, ac, esc2] = await Promise.all([
        api('/api/stats/settings'), api('/api/stats/autoclose'), api('/api/stats/escalation')]);
    } catch (err) { toast(err.message, 'err'); return; }
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
            <b>По нагрузке</b>: норма — из реальной работы периода: отвеченные обращения
            и висящая без ответа очередь (по 5 баллов), поделённые между операторами
            пропорционально их часам. Закрытия тикетов в норму не входят — тикеты
            закрываются автоматически. Сообщения, которые обработал бот или которые
            не требовали ответа, в нагрузку не попадают. Игнорировать тикеты невыгодно:
            брошенный тикет тянет норму вверх так же, как отвеченный.
            Если нагрузки не было — коэффициент 1.0. Работает даже с двумя операторами.<br>
            <b>Средний темп команды</b> — сравнение с коллегами (осторожно: при общем
            простое норма занижается). <b>Вручную</b> — фиксированное число баллов/час.</div></div>
        <div class="field" id="norm-manual"><label>Норма баллов за час (для ручного режима)</label>
          <input name="norm" type="number" step="0.5" min="0" value="${esc(cfg.norm_points_per_hour)}">
          <div class="muted" style="font-size:12px; margin-top:4px">
            Норма оператора за период = это число × его часы. Пример: 10 баллов/час ≈ 5 ответов (или 2 быстрых) в час.</div></div>
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
        <h3 style="margin:16px 0 8px; font-size:15px">Автозакрытие тикетов</h3>
        <div class="row" style="align-items:center">
          <div class="field" style="flex:0 0 auto"><label style="display:flex; gap:8px; align-items:center; font-weight:400">
            <input type="checkbox" name="ac_on" ${ac.enabled ? 'checked' : ''} style="width:auto"> Включено</label></div>
          <div class="field"><label>Закрывать через, часов</label>
            <input name="ac_hours" type="number" step="1" min="1" max="720" value="${esc(ac.hours)}"></div>
        </div>
        <div class="muted" style="font-size:12px; margin-bottom:12px">
          Если после ответа оператора пользователь молчит дольше указанного времени, тикет
          закрывается сам: пользователю в Telegram приходят кнопки оценки и кнопка
          «Вопрос не решён» (она открывает тикет заново). Неотвеченные тикеты автозакрытие
          не трогает — они остаются в очереди. Закрытия тикетов на баллы и норму не влияют:
          активность считается по ответам, их скорости и оценкам пользователей.</div>
        <h3 style="margin:16px 0 8px; font-size:15px">Эскалация неотвеченных</h3>
        <div class="row" style="align-items:center">
          <div class="field" style="flex:0 0 auto"><label style="display:flex; gap:8px; align-items:center; font-weight:400">
            <input type="checkbox" name="esc_on" ${esc2.enabled ? 'checked' : ''} style="width:auto"> Включено</label></div>
          <div class="field"><label>Алерт через, минут</label>
            <input name="esc_min" type="number" step="5" min="5" max="1440" value="${esc(esc2.minutes)}"></div>
          <div class="field"><label>Повторять каждые, минут</label>
            <input name="esc_rep" type="number" step="5" min="10" max="1440" value="${esc(esc2.repeat_minutes)}"></div>
        </div>
        <div class="muted" style="font-size:12px; margin-bottom:12px">
          Если обращение ждёт ответа дольше указанного времени (считается только рабочее окно
          поддержки — ночь не в счёт), в общий раздел саппорт-чата уходит алерт со списком
          тикетов. Пока не ответят — алерт повторяется. Ответ оператора сбрасывает отсчёт.</div>
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
        await api('/api/stats/autoclose', { method: 'PUT', body: {
          enabled: f.ac_on.checked,
          hours: parseFloat(f.ac_hours.value) || 24,
        }});
        await api('/api/stats/escalation', { method: 'PUT', body: {
          enabled: f.esc_on.checked,
          minutes: parseFloat(f.esc_min.value) || 15,
          repeat_minutes: parseFloat(f.esc_rep.value) || 30,
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
      'Медиана ответа, сек', 'Среднее, сек', 'Быстрых', 'Замерено', 'Оценка', 'Кол-во оценок',
      'Начинает (ср.)', 'Заканчивает (ср.)', 'Активных дней'];
    const lines = [head.join(';')].concat(d.rows.map(r => [
      r.login, r.name, r.score, r.norm_points ?? '', r.coeff ?? '', r.salary_base ?? '', r.payout ?? '',
      r.hours_per_week ?? '', r.replies, r.tickets, r.closes,
      r.median_wait_sec ?? '', r.avg_wait_sec ?? '', r.fast, r.measured,
      r.rating_avg ?? '', r.rating_count,
      r.work_rhythm?.avg_start ?? '', r.work_rhythm?.avg_end ?? '', r.work_rhythm?.days ?? '',
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
          <select name="a-op"><option value="">Все операторы</option></select>
          <input name="a-uid" type="number" placeholder="user_id">
          <select name="a-action"><option value="">Все действия</option></select>
          <input name="a-from" type="date">
          <input name="a-to" type="date">
          <button class="btn btn-sm" id="a-apply">Применить</button>
        </div>
        <div id="audit-list"></div>
      </div>`;
    try {
      const [acts, ops] = await Promise.all([api('/api/audit/actions'), api('/api/operators')]);
      const sel = document.querySelector('[name=a-action]');
      acts.actions.forEach(a => {
        const o = document.createElement('option');
        o.value = a; o.textContent = actionLabel(a);
        sel.appendChild(o);
      });
      // зарегистрированные операторы (включая отключённых — их история важна)
      const selOp = document.querySelector('[name=a-op]');
      ops.forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.login;
        opt.textContent = (o.name && o.name !== o.login ? `${o.name} (${o.login})` : o.login)
          + (o.active ? '' : ' — отключён');
        selOp.appendChild(opt);
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
          <input name="tg" value="${esc((op.tg_usernames && op.tg_usernames.length ? op.tg_usernames : [op.tg_username]).filter(Boolean).join(', '))}"
            placeholder="ivan_support, ivan_backup">
          <div class="muted" style="font-size:11.5px; margin-top:4px">Можно несколько через запятую —
            операторы иногда меняют тег, старые оставляйте здесь же, чтобы прошлые ответы из TG
            не потерялись в статистике.</div></div>
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
    body.tg_usernames = f.tg.value.split(',').map(s => s.trim()).filter(Boolean);
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

// ================================================================ ticket analytics view (owner)

const DOW_RU = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

function vizTip() {
  let t = document.getElementById('viz-tip');
  if (!t) {
    t = document.createElement('div');
    t.id = 'viz-tip';
    document.body.appendChild(t);
  }
  return t;
}

function bindVizTips(root) {
  const tip = vizTip();
  root.querySelectorAll('[data-tip]').forEach(el => {
    el.addEventListener('mouseenter', () => { tip.textContent = el.dataset.tip; tip.style.display = 'block'; });
    el.addEventListener('mousemove', e => {
      tip.style.left = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8) + 'px';
      tip.style.top = (e.clientY + 16) + 'px';
    });
    el.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  });
}

function viewTicketStats() {
  if (!S.me || S.me.role !== 'owner') { location.hash = '#/search'; return; }
  const st = S.tst || (S.tst = {
    from: isoDate(new Date(Date.now() - 6 * 86400000)),
    to: isoDate(new Date()),
  });

  $view.innerHTML = `
    <h1>Аналитика тикетов</h1>
    <div class="card">
      <div class="filter-bar" style="align-items:center">
        <button class="btn btn-ghost btn-sm" data-preset="7d">7 дней</button>
        <button class="btn btn-ghost btn-sm" data-preset="30d">30 дней</button>
        <button class="btn btn-ghost btn-sm" data-preset="month">Этот месяц</button>
        <input type="date" name="ts-from" value="${esc(st.from)}" style="flex:0 1 150px">
        <input type="date" name="ts-to" value="${esc(st.to)}" style="flex:0 1 150px">
        <button class="btn btn-sm" id="ts-apply">Показать</button>
      </div>
      <div id="ts-summary" class="stats-grid" style="margin:14px 0 0"></div>
    </div>
    <div class="card">
      <h2>Когда приходят обращения</h2>
      <div class="muted" style="font-size:12.5px; margin-bottom:10px" id="ts-heat-sub"></div>
      <div id="ts-heat">${spinnerHtml()}</div>
    </div>
    <div class="card">
      <h2>По дням</h2>
      <div id="ts-days"></div>
    </div>
    <div class="card">
      <h2>Кто пишет</h2>
      <div class="muted" style="font-size:12.5px; margin-bottom:6px">
        Тип пользователя — по сегментации основного бота (подписка, платежи, возраст аккаунта).</div>
      <div id="ts-types"></div>
    </div>
    <div class="card">
      <h2>Темы обращений (ИИ)</h2>
      <div class="muted" style="font-size:12.5px; margin-bottom:10px">
        Модель читает первые сообщения обращений периода, группирует их по темам и
        коротко описывает, в чём проблема. Результат сохраняется — повторный просмотр бесплатный.</div>
      <div id="ts-topics"></div>
    </div>`;

  const setPreset = (preset) => {
    const now = new Date();
    if (preset === '7d') { st.from = isoDate(new Date(now - 6 * 86400000)); st.to = isoDate(now); }
    else if (preset === '30d') { st.from = isoDate(new Date(now - 29 * 86400000)); st.to = isoDate(now); }
    else if (preset === 'month') {
      st.from = isoDate(new Date(now.getFullYear(), now.getMonth(), 1)); st.to = isoDate(now);
    }
    document.querySelector('[name=ts-from]').value = st.from;
    document.querySelector('[name=ts-to]').value = st.to;
    load();
  };
  document.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => setPreset(b.dataset.preset));
  document.getElementById('ts-apply').onclick = () => {
    st.from = document.querySelector('[name=ts-from]').value;
    st.to = document.querySelector('[name=ts-to]').value;
    if (!st.from || !st.to) { toast('Выберите обе даты', 'err'); return; }
    load();
  };

  const seqVar = bin => `var(--seq-${bin})`;
  const heatBin = (v, max) => v === 0 ? 0 : Math.max(1, Math.ceil(Math.sqrt(v / max) * 6));

  const renderHeat = (data) => {
    const heat = data.heatmap;
    const max = Math.max(1, ...heat.flat());
    document.getElementById('ts-heat-sub').textContent =
      `Начала обращений по дням недели и часам, локальное время UTC+${data.tz_offset_hours}. Зелёный — мало, красный — много.`;
    let html = '<div class="heat-wrap"><div class="heat-grid"><div></div>';
    for (let h = 0; h < 24; h++) html += `<div class="hl">${h}</div>`;
    for (let d = 0; d < 7; d++) {
      html += `<div class="hl">${DOW_RU[d]}</div>`;
      for (let h = 0; h < 24; h++) {
        const v = heat[d][h];
        const bin = heatBin(v, max);
        html += `<div class="heat-cell" ${bin ? `style="background:${seqVar(bin)}; border-color:transparent"` : ''}
          data-tip="${DOW_RU[d]}, ${h}:00–${h + 1}:00 — ${v} обращ."></div>`;
      }
    }
    html += '</div></div>';
    html += `<div class="heat-legend">0 <i style="background:var(--card-2); border:1px solid var(--border)"></i>`;
    for (let b = 1; b <= 6; b++) html += `<i style="background:${seqVar(b)}"></i>`;
    html += ` ${max} обращ./час</div>`;
    document.getElementById('ts-heat').innerHTML = html;
    bindVizTips(document.getElementById('ts-heat'));
  };

  const renderDays = (data) => {
    const days = data.by_date;
    const max = Math.max(1, ...days.map(d => d.count));
    document.getElementById('ts-days').innerHTML =
      `<div class="day-bars">${days.map(d => {
        const dt = new Date(d.date + 'T00:00:00');
        return `<div class="db" style="height:${Math.max(3, d.count / max * 100)}%"
          data-tip="${DOW_RU[(dt.getDay() + 6) % 7]} ${dt.toLocaleDateString('ru-RU')} — ${d.count} обращ."></div>`;
      }).join('')}</div>
      <div class="muted" style="font-size:11.5px; margin-top:6px">
        ${new Date(days[0].date + 'T00:00:00').toLocaleDateString('ru-RU')} —
        ${new Date(days[days.length - 1].date + 'T00:00:00').toLocaleDateString('ru-RU')},
        пик ${max} обращ./день</div>`;
    bindVizTips(document.getElementById('ts-days'));
  };

  const renderTypes = (data) => {
    const types = [...data.user_types].sort((a, b) => b.appeals - a.appeals);
    const max = Math.max(1, ...types.map(t => t.appeals));
    const total = types.reduce((s, t) => s + t.appeals, 0) || 1;
    document.getElementById('ts-types').innerHTML = types.length ? types.map(t => `
      <div class="tbar-row">
        <div class="tb-label">${esc(t.type)}</div>
        <div class="tbar-track">
          <div class="tbar" style="width:${Math.max(1, t.appeals / max * 100)}%"></div>
          <div class="tbar-val">${fmtNum(t.appeals)} обращ. (${Math.round(t.appeals / total * 100)}%) · ${fmtNum(t.users)} чел.</div>
        </div>
      </div>`).join('')
      : '<div class="center">Нет данных</div>';
  };

  const renderTopics = (report, cached) => {
    const $t = document.getElementById('ts-topics');
    if (!report) {
      $t.innerHTML = `<button class="btn" id="ts-topics-run">Проанализировать темы</button>
        <span class="muted" style="font-size:12.5px; margin-left:10px">займёт до минуты</span>`;
    } else {
      const created = report.created_at ? new Date(report.created_at).toLocaleString('ru-RU') : '';
      $t.innerHTML = `
        <div class="muted" style="font-size:12px; margin-bottom:4px">
          Анализ от ${esc(created)} · выборка ${report.analyzed} из ${report.total} обращений
          ${cached ? '· из кэша' : ''}</div>
        ${report.topics.map(t => `
          <div class="topic-item">
            <div class="topic-head"><b>${esc(t.name)}</b>
              <span class="muted" style="font-size:12.5px">${t.count} обращ. · ${t.share}%</span></div>
            <div style="font-size:13.5px; margin-top:4px">${esc(t.summary)}</div>
            ${t.examples && t.examples.length
              ? `<div class="topic-ex">${t.examples.map(e => '«' + esc(e) + '»').join(' · ')}</div>` : ''}
          </div>`).join('')}
        <button class="btn btn-ghost btn-sm" id="ts-topics-run" style="margin-top:10px">Обновить анализ</button>`;
    }
    const btn = document.getElementById('ts-topics-run');
    if (btn) btn.onclick = async () => {
      btn.disabled = true;
      $t.insertAdjacentHTML('beforeend',
        `<div id="ts-topics-wait" class="muted" style="margin-top:8px; font-size:12.5px">
          <span class="spinner"></span> ИИ читает обращения…</div>`);
      try {
        const res = await api(`/api/ticket-stats/topics?date_from=${st.from}&date_to=${st.to}&force=true`,
          { method: 'POST' });
        renderTopics(res.report, false);
      } catch (err) {
        document.getElementById('ts-topics-wait')?.remove();
        btn.disabled = false;
        toast(err.message, 'err');
      }
    };
  };

  const load = async () => {
    ['ts-heat', 'ts-days', 'ts-types', 'ts-topics'].forEach(id =>
      document.getElementById(id).innerHTML = spinnerHtml());
    let data;
    try {
      data = await api(`/api/ticket-stats?date_from=${st.from}&date_to=${st.to}`);
    } catch (err) {
      document.getElementById('ts-heat').innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
      ['ts-days', 'ts-types', 'ts-topics'].forEach(id => document.getElementById(id).innerHTML = '');
      return;
    }
    const t = data.totals;
    const peak = t.peak
      ? `${DOW_RU[t.peak.dow]} ${t.peak.hour}:00 (${t.peak.count})` : '—';
    document.getElementById('ts-summary').innerHTML = `
      <div class="stat"><div class="stat-label">Обращений</div><div class="stat-value">${fmtNum(t.appeals)}</div></div>
      <div class="stat"><div class="stat-label">Пользователей</div><div class="stat-value">${fmtNum(t.users)}</div></div>
      <div class="stat"><div class="stat-label">Сообщений</div><div class="stat-value">${fmtNum(t.messages)}</div></div>
      <div class="stat"><div class="stat-label">Пик</div><div class="stat-value" style="font-size:20px">${peak}</div></div>`;
    renderHeat(data);
    renderDays(data);
    renderTypes(data);
    try {
      const cached = await api(`/api/ticket-stats/topics?date_from=${st.from}&date_to=${st.to}`);
      renderTopics(cached.report, cached.cached);
    } catch (err) {
      document.getElementById('ts-topics').innerHTML = `<div class="error-note">${esc(err.message)}</div>`;
    }
  };

  load();
}

// ================================================================ help view (мануал для операторов)

function viewHelp() {
  const isOwner = S.me && S.me.role === 'owner';
  $view.innerHTML = `
    <h1>Справка оператора</h1>

    <div class="card">
      <h2>Как всё устроено</h2>
      <p>Поддержка RS VPN состоит из трёх частей, которые работают с одной базой:</p>
      <ul class="help-list">
        <li><b>Бот в Telegram</b> — с ним переписывается пользователь. Бот сам показывает меню
          самопомощи и отвечает инструкциями; оператор нужен, только если пользователь выбрал
          пункт «Другое (оператор)».</li>
        <li><b>Чат поддержки в Telegram</b> (форум с темами) — на каждого пользователя создаётся
          отдельная тема-тред «Тикет #ID». Всё, что пользователь пишет боту, копируется в тред;
          всё, что оператор пишет в треде, уходит пользователю в личку.</li>
        <li><b>Эта панель</b> — те же тикеты в браузере: список, переписка, быстрые ответы,
          черновики ИИ, карточка пользователя и статистика активности.</li>
      </ul>
      <p>Отвечать можно <b>откуда удобно</b> — с сайта или из треда в Telegram. Сообщения и
      закрытия из обоих мест попадают в общую историю и в вашу статистику.
      Как устроен основной бот со стороны пользователя (тарифы, автопродление, устройства,
      выводы) — на странице <a href="#/bot">«Бот»</a>.
      <b>Важно:</b> чтобы ответы из Telegram засчитывались именно вам, ваш Telegram-username
      должен быть указан в вашей карточке оператора (задаёт владелец на странице «Операторы»).</p>
    </div>

    <div class="card">
      <h2>Статусы тикета</h2>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>Статус</th><th>В панели</th><th>Что это значит</th></tr></thead>
        <tbody>
          <tr><td style="white-space:nowrap">🟡 pending</td><td>«ожидает»</td>
            <td>Пользователь написал боту, тикет создан или переоткрыт. Бот показал меню
            категорий и ждёт выбора. Оператор ещё НЕ подключён. Если пользователь за 5 минут
            не выберет пункт меню — бот закроет тикет сам.</td></tr>
          <tr><td style="white-space:nowrap">🟢 open</td><td>«у оператора»</td>
            <td>Пользователь выбрал «Другое (оператор)» или нажал «Вопрос не решён» после
            автозакрытия. Это ваша работа: тикет ждёт ответа человека.</td></tr>
          <tr><td style="white-space:nowrap">🔴 closed</td><td>«закрыт»</td>
            <td>Закрыт оператором (с сайта или из треда), самим пользователем (кнопка
            «Закрыть тикет» в боте) или автоматикой (см. ниже). Если пользователь напишет
            снова — тикет переоткроется как 🟡 и бот снова покажет меню.</td></tr>
        </tbody>
      </table></div>
      <p class="muted" style="font-size:12.5px">Заголовок темы в Telegram всегда показывает
      текущий статус: «🟡 Тикет #123», «🟢 Тикет #123», «🔴 Тикет #123» — бот и панель
      переименовывают тему при каждой смене статуса.</p>
    </div>

    <div class="card">
      <h2>Что видит пользователь в боте</h2>
      <p>Полезно знать этот путь наизусть, чтобы не советовать пользователю то, что бот ему уже показал:</p>
      <ol class="help-list">
        <li>Пользователь пишет боту первое сообщение → создаётся тред, тикет становится 🟡.
          Бот отвечает: «Тикет создан (ожидается выбор категории)» и показывает <b>меню
          категорий</b>. Пункты меню — это те же «Быстрые ответы», которые редактируются
          в панели (кнопка «Быстрые ответы» в чате тикета → «Управлять»).</li>
        <li>Выбрал категорию (например «RS VPN не работает») → бот присылает инструкцию из
          быстрого ответа + кнопки «Назад» и «Закрыть тикет» и предупреждает, что без
          закрытия вручную тикет закроется автоматически.</li>
        <li>Выбрал <b>«Другое (оператор)»</b> → тикет становится 🟢, в тред падает
          «Тикет переведён в статус OPEN (оператор)», а пользователя бот просит прислать
          скрин из Happ (к какому серверу подключается) и описание проблемы.</li>
        <li>Пока тикет 🟢, на каждое сообщение пользователя бот отвечает «Сообщение отправлено
          оператору» и напоминает прислать: 1) что именно не работает, 2) скрин из Happ,
          3) активны ли белые списки в его городе. Меню больше не показывается.</li>
        <li>Если пользователь так и не выбрал пункт меню — через 5 минут бот закрывает тикет
          («Тикет закрыт автоматически: не выбран пункт меню»).</li>
        <li>Сообщение в закрытый тикет → тикет снова 🟡, бот снова показывает меню.
          Ждать оператора пользователь начнёт только после «Другое (оператор)».</li>
      </ol>
      <p class="muted" style="font-size:12.5px">Если пользователь не зарегистрирован в основном
      боте RS VPN, поддержка попросит его сначала зарегистрироваться и даст ссылку.</p>
    </div>

    <div class="card">
      <h2>Работа с тикетами на сайте</h2>
      <p><b>Страница «Тикеты»</b> — список всех тикетов с фильтром по статусу.
      Очередь — это фильтр «🟡 Ожидают» и «🟢 У оператора»: смотрите в первую очередь тикеты,
      где последнее сообщение от пользователя.</p>
      <p><b>Чат тикета</b> обновляется мгновенно, как мессенджер — держите его открытым,
      новые сообщения появятся сами. Справа — список живых тикетов для быстрого перехода.</p>
      <ul class="help-list">
        <li><b>Ответ</b> уходит пользователю в личку от имени бота и дублируется в
          Telegram-тред с пометкой «Ответ с сайта». Лимит 3500 символов.</li>
        <li><b>Прикрепить фото</b> — картинка уйдёт пользователю и в тред. Вложения
          пользователя (фото, файлы, голосовые) видны в чате и открываются кликом.</li>
        <li><b>Быстрые ответы</b> — общая база заготовок (одна на сайт и бота). Выбранный
          текст вставляется в поле ответа: его можно поправить перед отправкой. Там же
          «Управлять» — добавить/изменить/выключить заготовку. Помните: активные заготовки —
          это ещё и пункты меню бота, которые видят все пользователи.</li>
        <li><b>Ответ ИИ</b> — по кнопке помощник готовит черновик ответа по переписке и
          базе быстрых ответов и вставляет его в поле; повторное нажатие даёт другой
          вариант. <b>Всегда читайте черновик перед отправкой</b> — ИИ может
          ошибаться; вы отвечаете за то, что уходит пользователю.</li>
        <li><b>Закрыть тикет</b> — статус станет 🔴, пользователь получит просьбу оценить
          работу поддержки (кнопки 1–5), тред будет переименован. Закрывайте тикет, когда
          вопрос решён — оценка придёт, пока пользователь ещё «тёплый».</li>
        <li><b>Карточка пользователя</b> (ссылка в шапке тикета) — баланс, подписка, устройства
          и действия с ними. Набор доступных действий зависит от ваших прав.</li>
      </ul>
    </div>

    <div class="card">
      <h2>Ответы из Telegram-треда</h2>
      <ul class="help-list">
        <li>Просто напишите сообщение в тред — бот перешлёт его пользователю в личку.
          Реакция 👍 на вашем сообщении = доставлено, 👎 = НЕ доставлено (обычно пользователь
          заблокировал бота — тогда до него не достучаться ни с сайта, ни из треда).</li>
        <li>Сообщения, начинающиеся с «/», пользователю не пересылаются — это команды.</li>
        <li><b>/check</b> — бот пришлёт в тред карточку пользователя с кнопками:
          «Обновить» (перечитать данные), «Профиль на сайте» (открывает карточку в панели),
          «Очистить подключения» (сбросить устройства VPN), «Быстрые ответы» (отправить
          заготовку в один клик — бот покажет список и подтвердит отправку в треде),
          «Закрыть тикет». Такая же карточка закрепляется в треде при создании тикета.</li>
        <li><b>«Закрыть тикет»</b> в карточке — то же, что закрытие с сайта: пользователь
          получит запрос оценки 1–5, тред станет 🔴.</li>
        <li>Фото, файлы и голосовые тоже пересылаются в обе стороны.</li>
      </ul>
      <p class="muted" style="font-size:12.5px">Ответы и закрытия из треда попадают в вашу
      статистику по вашему Telegram-username — проверьте, что он указан в вашей карточке
      оператора, иначе в рейтинге появится отдельная строка с ником вместо вашего логина.</p>
    </div>

    <div class="card">
      <h2>Что закрывается автоматически</h2>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>Кто закрывает</th><th>Когда</th><th>Что получает пользователь</th></tr></thead>
        <tbody>
          <tr><td>Бот</td><td>Тикет 🟡 и пользователь 5 минут не выбирает пункт меню</td>
            <td>Уведомление «Тикет закрыт автоматически»</td></tr>
          <tr><td>Панель</td><td>Тикет 🟢, вы ответили, а пользователь молчит дольше
            установленного срока${isOwner ? ' (настраивается: «Активность» → «Настройки расчёта»)' : ''}</td>
            <td>Кнопки оценки 1–5 и кнопка «Вопрос не решён — открыть заново». Нажмёт её —
            тикет снова станет 🟢 и в тред придёт уведомление</td></tr>
          <tr><td>Пользователь</td><td>Кнопка «Закрыть тикет» в боте</td>
            <td>Подтверждение закрытия (без запроса оценки)</td></tr>
        </tbody>
      </table></div>
      <p><b>Неотвеченные тикеты автоматика не закрывает.</b> Если последнее слово за
      пользователем — тикет будет висеть в очереди и тянуть норму вверх, пока кто-то
      не ответит. Бросать тикеты невыгодно всей команде.</p>
    </div>

    <div class="card">
      <h2>Оценки пользователей</h2>
      <p>После закрытия тикета (вручную или автозакрытием) пользователь может поставить
      оценку 1–5. Оценка появляется в треде («Оценка пользователя: N/5») и в статистике —
      она приписывается оператору, который <b>последним вёл этот тикет</b>.</p>
      <p>Оценка 5 даёт +4 балла, 4 — +2, 3 — ничего, 2 — −2, 1 — −4. Несколько добрых слов
      в конце диалога заметно влияют на то, какую кнопку нажмёт пользователь.</p>
    </div>

    <div class="card">
      <h2>Баллы, норма и коэффициент</h2>
      <p>Страница «Активность» считает работу каждого оператора за период. Баллы:</p>
      <ul class="help-list">
        <li><b>+2</b> — за каждый ответ пользователю (с сайта или из треда);</li>
        <li><b>+3 сверху</b> — если это первый ответ на обращение и он дан за ≤10 минут
          (ожидание считается только внутри рабочего окна поддержки — ночь никому
          ничего не портит);</li>
        <li><b>±2×(звёзды−3)</b> — за оценку пользователя;</li>
        <li>закрытия тикетов баллов НЕ дают (они автоматические) — колонка «Закрыто»
          в таблице справочная.</li>
      </ul>
      <p><b>Норма</b> считается от реальной нагрузки периода: каждое отвеченное обращение и
      каждый реально брошенный без ответа тикет добавляют по 5 баллов в «общий котёл», который
      делится между операторами пропорционально их часам по графику. <b>Коэффициент</b> =
      ваши баллы ÷ ваша норма${isOwner ? ' — он умножается на оклад при расчёте выплаты' : ''}.
      Если обращений в период не было — коэффициент 1.0, простой не по вашей вине не штрафуется.</p>
      <p>Что из этого следует практически:</p>
      <ul class="help-list">
        <li>быстрый первый ответ — самый «дорогой» балл, старайтесь отвечать в первые 10 минут;</li>
        <li>игнорировать сложный тикет невыгодно: брошенным он поднимет норму так же,
          как отвеченный, но баллов не принесёт никому;</li>
        <li>вежливое завершение диалога → хорошая оценка → до +4 баллов.</li>
      </ul>
    </div>

    <div class="card">
      <h2>График работы</h2>
      <p>Часы задаются в вашей карточке оператора по дням недели (задаёт владелец).
      День бывает двух видов:</p>
      <ul class="help-list">
        <li><b>Фиксированный</b> (например 09:00–16:00) — рабочее время считается по
          интервалу, можно через полночь (22:00–06:00).</li>
        <li><b>«По 1-му ответу»</b> (например 09:00–16:00, окно 1 ч) — день начинается с
          вашего первого ответа в интервале 09:00–10:00, но не позже 10:00. Скорость ответа
          до вашего фактического начала дня не измеряется — рейтинг не страдает, если вы
          начали в 9:40. Длительность дня при этом — до конца интервала.</li>
        <li>Пустой день — выходной: ожидания в этот день вам не считаются.</li>
      </ul>
      <p>Норма распределяется по этим часам, поэтому чем больше часов в графике,
      тем больше личная норма.</p>
    </div>

    <div class="card">
      <h2>Частые вопросы</h2>
      <ul class="help-list">
        <li><b>Пользователь не получает ответы.</b> Скорее всего заблокировал бота — в треде
          на вашем сообщении будет 👎, а сайт покажет ошибку отправки. Связаться с ним
          через поддержку нельзя, пока он не разблокирует бота.</li>
        <li><b>В переписке на сайте нет старых сообщений.</b> История пишется с момента
          подключения интеграции — старые диалоги смотрите в Telegram-треде.</li>
        <li><b>Тикет «сам» переоткрылся.</b> Пользователь написал в закрытый тикет (стало 🟡,
          бот показал меню) или нажал «Вопрос не решён» после автозакрытия (стало 🟢 —
          и это уже ваша очередь).</li>
        <li><b>Кнопка «Ответ ИИ» пишет, что помощник недоступен.</b> Это настройка сервера —
          сообщите владельцу; отвечать можно и без ИИ.</li>
        <li><b>Зачем закрывать тикет вручную, если есть автозакрытие?</b> Пользователь получает
          запрос оценки сразу после решения вопроса, а не через сутки — оценки заметно лучше.
          Баллов закрытие не даёт, это просто хорошая практика.</li>
        <li><b>Что видно в «Аудит-логе»?</b> Все действия операторов с балансом, подпиской и
          тикетами записываются с логином и временем${isOwner ? '' : ' — журнал видит владелец'}.</li>
      </ul>
    </div>`;
}

// ================================================================ bot help view (как устроен основной бот)

function diagram(name, alt) {
  return `<div class="diagram">
    <img class="dia-light" src="/static/help/${name}_light.svg" alt="${alt}">
    <img class="dia-dark" src="/static/help/${name}_dark.svg" alt="${alt}">
  </div>`;
}

function viewBotHelp() {
  $view.innerHTML = `
    <h1>Как устроен основной бот</h1>
    <p class="muted" style="margin:-6px 0 16px; font-size:13.5px">
      Что видит пользователь в боте RS VPN и как бот реагирует на его действия.
      Читайте вместе со «Справкой» — там про работу с тикетами.</p>

    <div class="card">
      <h2>Главное, что нужно понимать</h2>
      <ul class="help-list">
        <li>У каждого пользователя есть <b>внутренний баланс</b>. Пользователь пополняет его
          картой/СБП/криптой, а все покупки — подписка, доп. устройства, гигабайты, подарки —
          списываются с этого баланса. Прямых оплат «подписка картой» нет.</li>
        <li>Всё меню бота — <b>одно сообщение с картинкой и кнопками</b>, которое перерисовывается
          при навигации. Снизу закреплены постоянные кнопки: Профиль, Подключить RS VPN,
          Пополнить баланс, О сервисе, RS VPN Web (личный кабинет в браузере).</li>
        <li>При первом запуске пользователь получает <b>стартовый баланс</b>, которого хватает
          на несколько дней дешёвого дневного тарифа — поэтому первая покупка выглядит
          как «Бесплатно».</li>
        <li>Подписка <b>продлевается автоматически</b>, пока на балансе хватает денег (см. схему ниже).</li>
      </ul>
    </div>

    <div class="card">
      <h2>Карта меню</h2>
      ${diagram('menu', 'Карта меню бота')}
      <p class="muted" style="font-size:12.5px">Кнопки «Поддержка» и «RS VPN» (канал) есть почти
      на каждом экране. Поддержка ведёт в отдельный саппорт-бот — оттуда тикеты попадают к вам.</p>
    </div>

    <div class="card">
      <h2>Подписка</h2>
      <p>Тарифы: <b>1 день — 4₽</b>, <b>1 месяц — 100₽</b>, <b>3 месяца — 250₽</b>,
      <b>3 года — 2000₽</b>. К месячным и длиннее прилагается подарочная подписка
      (значок 🎁), которую пользователь может подарить другу.</p>
      <ul class="help-list">
        <li>Покупка списывает цену с баланса; если денег не хватает — бот сразу показывает
          экран пополнения с недостающей суммой.</li>
        <li>После покупки на экране подписки появляются: дата окончания, лимит устройств,
          ссылка подключения и кнопка «Настроить VPN» (открывает страницу с инструкцией).</li>
        <li>«Изменить длительность» меняет тариф <b>со следующего продления</b> — текущий
          срок не пересчитывается. Частый вопрос «поменял на месяц, а дата не изменилась» —
          это норма.</li>
        <li>«Продлить подписку» — ручное продление: списывает цену тарифа и добавляет срок.</li>
      </ul>
    </div>

    <div class="card">
      <h2>Автопродление и напоминания</h2>
      ${diagram('renew', 'Схема автопродления и напоминаний')}
      <ul class="help-list">
        <li>Пользователи на дневном тарифе (4₽) при достаточном балансе продлеваются
          <b>молча</b> — их не спамят каждый день.</li>
        <li>Если срок вышел, отдельное сообщение «подписка истекла» не приходит — но через
          несколько дней бот может прислать возвратное предложение (иногда с бонусом
          к пополнению — это плановые акции, пользователь ничего не «выигрывал»).</li>
        <li>Частый кейс: «VPN отключился» = кончился срок, а на балансе не хватило на
          автопродление. Решение — пополнить баланс и нажать «Продлить подписку».</li>
      </ul>
    </div>

    <div class="card">
      <h2>Пополнение баланса</h2>
      <ul class="help-list">
        <li>Способы: <b>СБП</b>, <b>Карта РФ</b>, <b>Карта иностранная</b>, <b>Криптовалюта</b>.</li>
        <li>Минимальная сумма — <b>75₽</b> (для СБП и иностранной карты — <b>100₽</b>).
          Есть кнопки готовых сумм и «Указать сумму» (пользователь отправляет число сообщением).</li>
        <li>Бот выставляет счёт и даёт кнопку «Перейти к оплате». После оплаты деньги
          зачисляются <b>автоматически</b>, обычно за минуты.</li>
        <li>Кейс «оплатил, а баланс не пришёл»: проверьте историю баланса в карточке
          пользователя на сайте. Если зачисления нет спустя заметное время — эскалируйте
          владельцу с ID пользователя, суммой и способом оплаты.</li>
        <li>Некоторым пользователям бот показывает персональные акции вида «+15…50% к
          пополнению». Это автоматические предложения; если пользователь говорит, что бонус
          не пришёл — уточните сумму и время пополнения и эскалируйте.</li>
      </ul>
    </div>

    <div class="card">
      <h2>Устройства</h2>
      <ul class="help-list">
        <li>В тариф входят <b>2 устройства</b>. Дополнительные покупаются в «Менеджере
          устройств» пакетами +1/+2/+3/+5 по <b>75₽ в месяц за штуку</b> — списывается
          с баланса ежемесячно, отдельно от подписки.</li>
        <li>Если в день списания денег не хватает — пакет отключается, лимит снижается,
          пользователь получает уведомление. Кейс «у меня пропали слоты устройств» —
          почти всегда это оно.</li>
        <li>«Мои устройства» показывает привязанные устройства (по обоим конфигам).
          Пользователь может <b>отвязать</b> устройство — слот освобождается, лимит не
          меняется. Полезно, когда «лимит устройств исчерпан» после переустановки приложения.</li>
      </ul>
    </div>

    <div class="card">
      <h2>ByPass — белые списки</h2>
      <ul class="help-list">
        <li>Отдельный конфиг для обхода белых списков. Создаётся <b>бесплатно</b> кнопкой
          «Белые списки», срок действия — как у основной подписки.</li>
        <li>У ByPass ограничен трафик; гигабайты докупаются с баланса:
          <b>5 ГБ — 50₽</b>, <b>15 ГБ — 90₽</b>, <b>30 ГБ — 170₽</b>, максимальный пакет — 500₽.
          Кейс «закончились гигабайты» решается именно здесь.</li>
        <li>Бот выдаёт ссылку подключения под выбранное приложение — Happ или INCY —
          с пошаговой инструкцией. Это <b>второй, отдельный</b> конфиг: устройство может быть
          подключено к основному, к ByPass или к обоим.</li>
        <li>ByPass позиционируется как тестовый: полная работа не гарантируется из-за
          внешних факторов — об этом написано прямо в боте.</li>
      </ul>
    </div>

    <div class="card">
      <h2>Реферальная программа и вывод средств</h2>
      <p>«Пригласить» даёт личную ссылку. С <b>каждого пополнения</b> приглашённого друга
      рефереру начисляется <b>30%</b> на отдельный реферальный баланс (не тот, с которого
      покупают подписку).</p>
      ${diagram('payout', 'Путь заявки на вывод')}
      <ul class="help-list">
        <li>Вывод доступен от <b>500₽</b>. Способы: на баланс бота, СБП, карта МИР,
          USDT (TRC-20). Реквизиты пользователь заполняет по полям прямо в боте.</li>
        <li>Заявки обрабатываются <b>вручную</b>, поэтому «мгновенно» не бывает — обещанный
          срок «в течение пары часов» после одобрения.</li>
        <li>Минимумы по способам: карта — от 1000₽, СБП — от 500₽, USDT — от 50$.
          Заявка меньше минимума будет отклонена с этой причиной.</li>
        <li>При отказе деньги <b>не сгорают</b> — остаются на реферальном балансе, пользователь
          может исправить реквизиты и подать снова (не чаще раза в сутки).</li>
      </ul>
    </div>

    <div class="card">
      <h2>Подарки</h2>
      <ul class="help-list">
        <li>Дарят через инлайн-режим: пользователь пишет <b>@rsconnect_bot</b> прямо в чате
          с другом и выбирает подписку (1 день — 4₽, 7 дней — 28₽, 1 месяц — 100₽,
          3 месяца — 250₽, 3 года — 2000₽).</li>
        <li>Списание происходит у <b>дарителя</b> в момент, когда получатель принял подарок.
          Если у дарителя есть запас подарочных подписок (🎁 за покупку тарифов) — используется
          он, бесплатно; иначе — его баланс. Не хватает денег — обе стороны получат отказ.</li>
        <li>Получателю подарок <b>создаёт или продлевает</b> подписку. Подарок одноразовый,
          свой собственный принять нельзя.</li>
      </ul>
    </div>

    <div class="card">
      <h2>Промокоды и почта</h2>
      <ul class="help-list">
        <li><b>Промокод</b> пользователь просто отправляет сообщением в бот. Каждый код
          активируется один раз на пользователя; награда — деньги на баланс или гигабайты
          ByPass. «Промокод не работает» = чаще всего уже активирован, истёк или исчерпан лимит.</li>
        <li>Сообщение, похожее на <b>e-mail</b>, бот сохраняет как почту профиля — она нужна
          для чеков при оплате картой и связи вне Telegram. Если пользователь «случайно
          отправил почту и что-то произошло» — ничего страшного, просто привязалась почта.</li>
        <li>Любое другое сообщение, не похожее на команду/почту/промокод, бот вежливо
          игнорирует — для вопросов есть кнопка «Поддержка».</li>
      </ul>
    </div>

    <div class="card">
      <h2>Шпаргалка: частые обращения</h2>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>Пользователь говорит</th><th>Что это обычно значит</th></tr></thead>
        <tbody>
          <tr><td>«VPN перестал работать»</td><td>Кончился срок: не хватило баланса на
            автопродление. Проверить дату окончания и баланс в карточке.</td></tr>
          <tr><td>«Оплатил, деньги не пришли»</td><td>Смотрим историю баланса в карточке;
            зачисление автоматическое. Нет зачисления — эскалация с ID, суммой, способом.</td></tr>
          <tr><td>«Поменял тариф, а дата не изменилась»</td><td>Новая длительность применяется
            со следующего продления — это штатно.</td></tr>
          <tr><td>«Пропали слоты устройств»</td><td>Не хватило денег на ежемесячные 75₽/устройство —
            пакет отключился. Пополнить и купить пакет заново.</td></tr>
          <tr><td>«Лимит устройств исчерпан»</td><td>Отвязать старое устройство в «Мои устройства»
            (или «Очистить подключения» из карточки).</td></tr>
          <tr><td>«Кончились гигабайты»</td><td>Это ByPass: докупить ГБ в «Белые списки».</td></tr>
          <tr><td>«Где мой вывод средств»</td><td>Заявки обрабатываются вручную; после одобрения —
            пара часов. Отказ приходит сообщением с причиной, деньги остаются на реф-балансе.</td></tr>
          <tr><td>«Подарок не активируется»</td><td>Уже активирован, отправитель = получатель,
            или у дарителя не хватило баланса.</td></tr>
        </tbody>
      </table></div>
    </div>`;
}

// ================================================================ проверка качества («Проверка» у владельца, «Разборы» у оператора)

function qaNormTag(s) { return String(s || '').trim().replace(/^@/, '').toLowerCase(); }

function qaUserLabel(i) {
  const who = `${esc(i.first_name || '')}${i.username ? ' @' + esc(i.username) : ''}`.trim();
  return who ? `${who} <span class="muted">#${i.user_id}</span>` : '#' + i.user_id;
}

function qaVerdictBadge(r) {
  if (!r) return '<span class="badge badge-gray">не проверен</span>';
  if (r.verdict === 'ok') return '<span class="badge badge-green">✓ правильно</span>';
  return `<span class="badge badge-red">✗ ошибки</span> <span class="muted" style="font-size:11.5px">${r.acked ? 'отработано' : 'ждёт оператора'}</span>`;
}

function qaChatHtml(messages, targetSet) {
  if (!messages.length) return '<div class="center">Сообщений нет</div>';
  return messages.map(m => {
    const cls = m.direction === 'operator' ? 'msg-operator' : m.direction === 'system' ? 'msg-system' : 'msg-user';
    const mine = m.direction === 'operator' && targetSet && targetSet.has(qaNormTag(m.operator_login));
    const who = m.direction === 'operator'
      ? (m.operator_login ? esc(m.operator_login) + (m.source === 'tg' ? ' (TG)' : ' (сайт)') : 'оператор')
      : m.direction === 'system' ? '' : 'пользователь';
    const text = m.text ? `<div class="msg-text">${tgHtml(m.text)}</div>` : '';
    return `<div class="msg ${cls}${mine ? ' msg-qa-target' : ''}">${text}${attachmentHtml(m)}<div class="msg-meta">${who ? who + ' · ' : ''}${fmtDate(m.timestamp)}</div></div>`;
  }).join('');
}

async function viewQA() {
  if (!S.me || S.me.role !== 'owner') { location.hash = '#/search'; return; }
  if (!S.qaOps) {
    try { S.qaOps = await api('/api/operators'); } catch (e) { toast(e.message, 'err'); return; }
  }
  if (!S.qaOpLogin && S.qaOps.length) {
    const firstOp = S.qaOps.find(o => o.role !== 'owner') || S.qaOps[0];
    S.qaOpLogin = firstOp.login;
  }
  S.qaFilter = S.qaFilter || 'all';
  S.qaPage = S.qaPage || 1;

  $view.innerHTML = `
    <div class="card">
      <h2>Проверка качества</h2>
      <div class="muted" style="margin:4px 0 12px; font-size:13px">Выберите оператора — ниже все тикеты,
        где он отвечал. Откройте тикет, прочитайте переписку и отметьте: решено правильно или есть ошибки.
        Ошибки оператор обязан разобрать при следующем входе в панель.</div>
      <div class="filters" style="display:flex; gap:10px; flex-wrap:wrap; align-items:center">
        <select id="qa-op">${S.qaOps.map(o =>
          `<option value="${esc(o.login)}" ${S.qaOpLogin === o.login ? 'selected' : ''}>${esc(o.name || o.login)} (${esc(o.login)})</option>`).join('')}</select>
        <select id="qa-filter">
          <option value="all" ${S.qaFilter === 'all' ? 'selected' : ''}>Все тикеты</option>
          <option value="unreviewed" ${S.qaFilter === 'unreviewed' ? 'selected' : ''}>Непроверенные</option>
          <option value="reviewed" ${S.qaFilter === 'reviewed' ? 'selected' : ''}>Проверенные</option>
        </select>
      </div>
    </div>
    <div class="card" id="qa-list"><div class="center"><span class="spinner"></span></div></div>`;

  const $op = document.getElementById('qa-op');
  const $f = document.getElementById('qa-filter');
  $op.onchange = () => { S.qaOpLogin = $op.value; S.qaPage = 1; loadList(); };
  $f.onchange = () => { S.qaFilter = $f.value; S.qaPage = 1; loadList(); };

  async function loadList() {
    const $list = document.getElementById('qa-list');
    if (!$list) return;
    if (!S.qaOpLogin) { $list.innerHTML = '<div class="center">Операторов нет</div>'; return; }
    let data;
    try {
      data = await api(`/api/reviews/tickets?operator=${encodeURIComponent(S.qaOpLogin)}` +
        `&filter=${S.qaFilter}&page=${S.qaPage}&page_size=30`);
    } catch (e) { $list.innerHTML = `<div class="error-note">${esc(e.message)}</div>`; return; }
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    if (data.page > pages) { S.qaPage = pages; return loadList(); }
    $list.innerHTML = (data.items.length ? `<div class="table-wrap"><table>
      <tr><th>Пользователь</th><th>Ответов</th><th>Последний ответ</th><th>Тикет</th><th>Вердикт</th><th></th></tr>
      ${data.items.map(i => `
        <tr class="qa-row" data-uid="${i.user_id}" style="cursor:pointer">
          <td>${qaUserLabel(i)}</td>
          <td>${i.replies}</td>
          <td>${fmtDate(i.last_reply_at)}</td>
          <td>${i.ticket_status === 'closed'
            ? '<span class="badge badge-gray">закрыт</span>'
            : i.ticket_status ? `<span class="badge badge-yellow">${esc(i.ticket_status)}</span>` : '—'}</td>
          <td>${qaVerdictBadge(i.review)}</td>
          <td><button class="btn btn-ghost btn-sm" data-uid="${i.user_id}">Разобрать</button></td>
        </tr>`).join('')}
    </table></div>` : '<div class="center">Тикетов с ответами этого оператора нет</div>') + `
    <div class="pager" style="display:flex; gap:8px; align-items:center; margin-top:10px">
      <button class="btn btn-ghost btn-sm" id="qa-prev" ${data.page <= 1 ? 'disabled' : ''}>←</button>
      <span class="muted" style="font-size:13px">стр. ${data.page} из ${pages} · всего ${data.total}</span>
      <button class="btn btn-ghost btn-sm" id="qa-next" ${data.page >= pages ? 'disabled' : ''}>→</button>
    </div>`;
    $list.querySelectorAll('[data-uid]').forEach(el => el.onclick = (e) => {
      e.stopPropagation();
      location.hash = `#/qa/${encodeURIComponent(S.qaOpLogin)}/${el.dataset.uid}`;
    });
    const prev = document.getElementById('qa-prev'), next = document.getElementById('qa-next');
    if (prev) prev.onclick = () => { S.qaPage = Math.max(1, S.qaPage - 1); loadList(); };
    if (next) next.onclick = () => { S.qaPage = S.qaPage + 1; loadList(); };
  }
  loadList();
}

async function viewQAReview(login, userId) {
  if (!S.me || S.me.role !== 'owner') { location.hash = '#/search'; return; }
  $view.innerHTML = '<div class="center" style="padding:40px 0"><span class="spinner"></span></div>';
  let t, ops;
  try {
    [t, ops] = await Promise.all([
      api(`/api/tickets/${userId}`),
      S.qaOps ? Promise.resolve(S.qaOps) : api('/api/operators'),
    ]);
  } catch (e) {
    $view.innerHTML = `<div class="card"><div class="error-note">${esc(e.message)}</div>
      <div style="margin-top:12px"><a href="#/qa" class="btn btn-ghost">← К проверке</a></div></div>`;
    return;
  }
  S.qaOps = ops;
  const op = ops.find(o => o.login === login);
  const targetSet = new Set([login.toLowerCase()]);
  if (op) {
    (op.tg_usernames || []).concat(op.tg_username ? [op.tg_username] : [])
      .forEach(tag => { const n = qaNormTag(tag); if (n) targetSet.add(n); });
  }

  let review = null;
  try {
    const rd = await api(`/api/reviews/tickets?operator=${encodeURIComponent(login)}&filter=reviewed&page=1&page_size=100`);
    const hit = rd.items.find(i => i.user_id === userId);
    if (hit) review = hit.review;
  } catch (e) { /* не критично */ }

  const u = t.ticket || {};
  $view.innerHTML = `
    <div class="card">
      <div class="ticket-head" style="display:flex; gap:10px; flex-wrap:wrap; align-items:center">
        <h2 style="margin:0">Разбор: ${esc(op ? (op.name || op.login) : login)}</h2>
        <span class="muted">тикет #${userId}${u.username ? ' · @' + esc(u.username) : ''}${u.first_name ? ' · ' + esc(u.first_name) : ''}</span>
        <span style="flex:1"></span>
        <a class="btn btn-ghost btn-sm" href="#/ticket/${userId}">Открыть как тикет</a>
        <a class="btn btn-ghost btn-sm" href="#/qa">← К проверке</a>
      </div>
      <div class="muted" style="font-size:12.5px; margin-top:6px">Сообщения проверяемого оператора подсвечены рамкой.</div>
    </div>
    <div class="card">
      <div class="chat" id="qa-chat" style="max-height:52vh; overflow-y:auto; display:flex; flex-direction:column; gap:8px"></div>
    </div>
    <div class="card" id="qa-verdict">
      <h3 style="margin-top:0">Вердикт</h3>
      ${review ? `<div class="muted" style="font-size:13px; margin-bottom:8px">Текущий: ${qaVerdictBadge(review)}
        · проверил ${esc(review.reviewed_by || '')} ${fmtDate(review.created_at)}</div>` : ''}
      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px">
        <button class="btn" id="qa-ok">✓ Решено правильно</button>
        <button class="btn btn-danger" id="qa-bad">✗ Есть ошибки</button>
      </div>
      <div id="qa-bad-form" class="${review && review.verdict === 'bad' ? '' : 'hidden'}">
        <label style="font-size:13px">Конкретные ошибки — оператор увидит этот текст и обязан его отработать</label>
        <textarea id="qa-mistakes" rows="4" style="width:100%; margin-top:6px"
          placeholder="Например: не поздоровался; дал неверную инструкцию по продлению; закрыл тикет без ответа на второй вопрос">${esc(review ? review.mistakes : '')}</textarea>
        <div style="margin-top:8px"><button class="btn btn-danger" id="qa-bad-save">Сохранить разбор ошибок</button></div>
      </div>
    </div>`;

  const $chat = document.getElementById('qa-chat');
  $chat.innerHTML = qaChatHtml(t.messages || [], targetSet);
  hydrateAttachments($chat);
  $chat.scrollTop = $chat.scrollHeight;

  async function save(verdict) {
    const mistakes = verdict === 'bad' ? document.getElementById('qa-mistakes').value.trim() : '';
    if (verdict === 'bad' && mistakes.length < 3) {
      toast('Опишите конкретные ошибки', 'err');
      document.getElementById('qa-mistakes').focus();
      return;
    }
    try {
      await api('/api/reviews', { method: 'POST',
        body: { operator_login: login, user_id: userId, verdict, mistakes } });
    } catch (e) { toast(e.message, 'err'); return; }
    toast(verdict === 'ok' ? 'Отмечено: решено правильно' : 'Разбор ошибок отправлен оператору');
    location.hash = '#/qa';
  }
  document.getElementById('qa-ok').onclick = () => save('ok');
  document.getElementById('qa-bad').onclick = () => {
    document.getElementById('qa-bad-form').classList.remove('hidden');
    document.getElementById('qa-mistakes').focus();
  };
  document.getElementById('qa-bad-save').onclick = () => save('bad');
}

// ---- «Разборы» у оператора + обязательная работа над ошибками ----

function updateMistakesBadge() {
  const b = document.getElementById('mistakes-badge');
  if (!b) return;
  const n = S.mistakesPending || 0;
  b.textContent = n > 9 ? '9+' : String(n);
  b.classList.toggle('hidden', n === 0);
}

async function pollMistakes() {
  if (!S.me || S.me.role === 'owner') return;
  let r;
  try { r = await api('/api/reviews/my'); } catch (e) { return; }
  const prev = S.mistakesPending;
  S.mistakes = r.items;
  S.mistakesPending = r.pending;
  updateMistakesBadge();
  if (r.pending > 0 && prev !== undefined && r.pending > prev
      && !location.hash.startsWith('#/mistakes')) {
    toast('Владелец назначил вам работу над ошибками', 'err');
    location.hash = '#/mistakes';
  }
}

async function viewMistakes() {
  let r;
  try { r = await api('/api/reviews/my'); } catch (e) {
    $view.innerHTML = `<div class="card"><div class="error-note">${esc(e.message)}</div></div>`;
    return;
  }
  S.mistakes = r.items;
  S.mistakesPending = r.pending;
  updateMistakesBadge();

  const pending = r.items.filter(i => i.verdict === 'bad' && !i.acked);
  const history = r.items.filter(i => !(i.verdict === 'bad' && !i.acked));

  const cardHtml = (i, isPending) => `
    <div class="qa-mistake card" data-id="${esc(i.id)}" style="border:1px solid ${isPending ? 'var(--red, #c0392b)' : 'var(--border)'}">
      <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center">
        ${qaVerdictBadge(i)}
        <b>${qaUserLabel(i)}</b>
        <span class="muted" style="font-size:12.5px">проверил ${esc(i.reviewed_by || '')} · ${fmtDate(i.created_at)}</span>
      </div>
      ${i.mistakes ? `<div class="qa-mistake-text" style="white-space:pre-wrap; margin-top:8px; padding:10px 12px; background:var(--card-2); border-radius:8px">${esc(i.mistakes)}</div>` : ''}
      <div style="display:flex; gap:8px; margin-top:10px; flex-wrap:wrap">
        <button class="btn btn-ghost btn-sm" data-show="${i.user_id}">Показать переписку</button>
        ${isPending ? `<button class="btn btn-sm" data-ack="${esc(i.id)}">Ознакомился, ошибки учту</button>` : ''}
      </div>
      <div class="chat qa-mistake-chat hidden" data-chat="${i.user_id}"
        style="max-height:40vh; overflow-y:auto; display:flex; flex-direction:column; gap:8px; margin-top:10px"></div>
    </div>`;

  $view.innerHTML = `
    ${pending.length ? `<div class="card" style="border:1px solid var(--red, #c0392b)">
      <h2 style="margin:0">Работа над ошибками</h2>
      <div style="margin-top:6px; font-size:13.5px">Владелец разобрал ваши тикеты и нашёл ошибки —
        <b>пока вы не подтвердите каждый разбор, остальные разделы панели недоступны</b>.
        Прочитайте замечания, при необходимости откройте переписку и нажмите «Ознакомился».</div>
    </div>` : `<div class="card"><h2 style="margin:0">Разборы</h2>
      <div class="muted" style="margin-top:6px; font-size:13px">Здесь видны итоги проверки ваших тикетов владельцем.</div>
    </div>`}
    ${pending.map(i => cardHtml(i, true)).join('')}
    ${history.length ? `<h3 style="margin:18px 0 8px">История проверок</h3>
      ${history.map(i => cardHtml(i, false)).join('')}` : ''}
    ${!r.items.length ? '<div class="card"><div class="center">Проверок пока не было</div></div>' : ''}`;

  $view.querySelectorAll('[data-show]').forEach(btn => btn.onclick = async () => {
    const uid = btn.dataset.show;
    const $chat = btn.closest('.qa-mistake').querySelector(`[data-chat="${uid}"]`);
    if (!$chat.classList.contains('hidden')) { $chat.classList.add('hidden'); btn.textContent = 'Показать переписку'; return; }
    $chat.classList.remove('hidden');
    btn.textContent = 'Скрыть переписку';
    if (!$chat.dataset.loaded) {
      $chat.innerHTML = '<div class="center"><span class="spinner"></span></div>';
      try {
        const t = await api(`/api/tickets/${uid}`);
        $chat.innerHTML = qaChatHtml(t.messages || [], new Set([S.me.login.toLowerCase()]
          .concat((S.me.tg_usernames || []).map(qaNormTag))));
        hydrateAttachments($chat);
        $chat.scrollTop = $chat.scrollHeight;
        $chat.dataset.loaded = '1';
      } catch (e) { $chat.innerHTML = `<div class="error-note">${esc(e.message)}</div>`; }
    }
  });
  $view.querySelectorAll('[data-ack]').forEach(btn => btn.onclick = async () => {
    btn.disabled = true;
    try {
      const res = await api(`/api/reviews/${btn.dataset.ack}/ack`, { method: 'POST' });
      S.mistakesPending = res.pending;
      if (res.pending === 0) {
        toast('Все ошибки отработаны — панель разблокирована');
      } else {
        toast(`Принято. Осталось разборов: ${res.pending}`);
      }
      viewMistakes();
    } catch (e) { btn.disabled = false; toast(e.message, 'err'); }
  });
}

// ================================================================ router

function setNav(active) {
  $topbar.classList.remove('hidden');
  document.getElementById('me-label').textContent =
    S.me ? `${S.me.name} (${S.me.role})` : '';
  document.querySelectorAll('.owner-only').forEach(el =>
    el.classList.toggle('hidden', !S.me || S.me.role !== 'owner'));
  document.querySelectorAll('.op-only').forEach(el =>
    el.classList.toggle('hidden', !S.me || S.me.role === 'owner'));
  updateMistakesBadge();
  document.querySelectorAll('#topbar nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.nav === active));
}

async function render() {
  const hash = location.hash || '#/search';
  if (S.curHash !== hash) { S.prevHash = S.curHash; S.curHash = hash; }
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

  // обязательная работа над ошибками: оператор с неотработанными разборами
  // не попадает никуда, кроме страницы «Разборы»
  if (S.me && S.me.role !== 'owner') {
    if (S.mistakesPending === undefined) {
      try {
        const r = await api('/api/reviews/my');
        S.mistakes = r.items;
        S.mistakesPending = r.pending;
      } catch (e) { S.mistakesPending = 0; }
    }
    if (S.mistakesPending > 0 && !hash.startsWith('#/mistakes')) {
      location.hash = '#/mistakes';
      return;
    }
  }

  const userMatch = hash.match(/^#\/user\/(\d+)$/);
  if (userMatch) { setNav('search'); viewUser(parseInt(userMatch[1], 10)); return; }
  const ticketMatch = hash.match(/^#\/ticket\/(\d+)$/);
  if (ticketMatch) { setNav('tickets'); viewTicket(parseInt(ticketMatch[1], 10)); return; }
  if (hash.startsWith('#/tickets')) { setNav('tickets'); viewTickets(); return; }
  if (hash.startsWith('#/stats')) { setNav('stats'); viewStats(); return; }
  if (hash.startsWith('#/tstats')) { setNav('tstats'); viewTicketStats(); return; }
  const qaMatch = hash.match(/^#\/qa\/([A-Za-z0-9_.-]+)\/(\d+)$/);
  if (qaMatch) { setNav('qa'); viewQAReview(decodeURIComponent(qaMatch[1]), parseInt(qaMatch[2], 10)); return; }
  if (hash.startsWith('#/qa')) { setNav('qa'); viewQA(); return; }
  if (hash.startsWith('#/mistakes')) { setNav('mistakes'); viewMistakes(); return; }
  if (hash.startsWith('#/help')) { setNav('help'); viewHelp(); return; }
  if (hash.startsWith('#/bot')) { setNav('bot'); viewBotHelp(); return; }
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

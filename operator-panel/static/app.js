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
    ${req.danger ? '<div class="warn-note">⚠ Действие необратимо. Проверьте данные перед подтверждением.</div>' : ''}
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
      <div class="login-logo">⚡</div>
      <h1>Панель оператора</h1>
      <form id="login-form">
        <div class="field"><label>Логин</label><input name="login" required autocomplete="username"></div>
        <div class="field"><label>Пароль</label><input name="password" type="password" required autocomplete="current-password"></div>
        <button class="btn" style="width:100%" type="submit">Войти</button>
        <div class="error-note hidden" id="login-err"></div>
      </form>
    </div></div>`;
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
      location.hash = '#/search';
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
        <div class="stat"><div class="stat-label">ByPass до</div><div class="stat-value">${fmtDate(vpn.bypass_expireAt)}</div></div>
        <div class="stat"><div class="stat-label">ByPass трафик</div><div class="stat-value">${fmtBytes(vpn.bypass_trafficLimitBytes)}</div></div>
        <div class="stat"><div class="stat-label">Реф. баланс</div><div class="stat-value">${fmtNum(u.ref_withdrawable)} ₽</div></div>
        <div class="stat"><div class="stat-label">Email</div><div class="stat-value" style="font-size:14px">${esc(u.email || '—')}</div></div>
        <div class="stat"><div class="stat-label">Сегмент</div><div class="stat-value" style="font-size:14px">${esc((u.growth || {}).segment || '—')}</div></div>
      </div>

      <div class="actions-bar">
        <button class="btn" id="act-balance">💰 Баланс</button>
        <button class="btn" id="act-expire">📅 Срок подписки</button>
        <button class="btn" id="act-devlimit">📱 Лимит устройств</button>
        <button class="btn" id="act-bypass">🔓 ByPass</button>
        <button class="btn" id="act-gift">🎁 Подарить</button>
        <button class="btn btn-ghost" id="act-email">✉ Email</button>
        <button class="btn btn-danger" id="act-devreset">🔌 Отвязать устройства</button>
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
  document.getElementById('act-balance').onclick = () => modalBalance(userId, u, reload);
  document.getElementById('act-expire').onclick = () => modalExpire(userId, vpn, reload);
  document.getElementById('act-devlimit').onclick = () => modalDeviceLimit(userId, vpn, reload);
  document.getElementById('act-bypass').onclick = () => modalBypass(userId, vpn, reload);
  document.getElementById('act-gift').onclick = () => modalGift(userId, vpn, reload);
  document.getElementById('act-email').onclick = () => modalEmail(userId, u, reload);
  document.getElementById('act-devreset').onclick = () => modalDeviceReset(userId, reload);

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
        <tr><th>HWID</th><th>Платформа</th><th>Модель</th><th></th></tr>
        ${d.devices.map(dev => `<tr>
          <td class="mono">${esc(dev.hwid || '')}</td>
          <td>${esc(dev.platform || '—')}</td>
          <td>${esc(dev.deviceModel || dev.model || '—')}</td>
          <td><button class="btn btn-danger btn-sm" data-hwid="${esc(dev.hwid || '')}">Отвязать</button></td>
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
      <label style="display:flex;gap:8px;align-items:center;color:var(--text)">
        <input type="checkbox" name="allow_negative" style="width:auto"> Разрешить уход баланса в минус
      </label>`,
    buildRequest($m) {
      const dir = $m.querySelector('[name=dir]').value;
      const amount = parseFloat($m.querySelector('[name=amount]').value);
      if (!amount || amount <= 0) throw new Error('Введите сумму больше нуля');
      const signed = dir === '-' ? -amount : amount;
      return {
        body: { amount: signed, allow_negative: $m.querySelector('[name=allow_negative]').checked },
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
      <div class="muted" style="margin-bottom:12px">Сейчас: <b>${fmtDate(vpn.expireAt)}</b></div>
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
  actionModal({
    title: `Лимит устройств (сейчас ${vpn.hwidDeviceLimit ?? '—'})`,
    url: `/api/users/${userId}/subscription/device-limit`,
    onDone,
    supportsForceLocal: true,
    fieldsHtml: `<div class="field"><label>Новый лимит</label>
      <input name="limit" type="number" min="0" max="1000" required placeholder="7"></div>`,
    buildRequest($m) {
      const limit = parseInt($m.querySelector('[name=limit]').value, 10);
      if (isNaN(limit) || limit < 0) throw new Error('Введите корректный лимит');
      return {
        body: { limit },
        danger: vpn.hwidDeviceLimit != null && limit < vpn.hwidDeviceLimit,
        confirmHtml: `Лимит устройств: <b>${esc(vpn.hwidDeviceLimit ?? '—')}</b><span class="arrow">→</span><b>${limit}</b>`,
      };
    },
  });
}

function modalBypass(userId, vpn, onDone) {
  actionModal({
    title: 'ByPass: срок и трафик',
    url: `/api/users/${userId}/bypass`,
    onDone,
    supportsForceLocal: true,
    fieldsHtml: `
      <div class="muted" style="margin-bottom:12px">
        Сейчас: до <b>${fmtDate(vpn.bypass_expireAt)}</b>, лимит <b>${fmtBytes(vpn.bypass_trafficLimitBytes)}</b></div>
      <div class="field"><label>Сдвиг срока, дней (± , пусто = не менять)</label>
        <input name="days" type="number" min="-3650" max="3650" placeholder="30 или -10"></div>
      <div class="field"><label>Добавить трафика, ГБ (±, пусто = не менять)</label>
        <input name="add_gb" type="number" step="0.1" placeholder="5"></div>
      <div class="field"><label>ИЛИ задать лимит трафика точно, ГБ</label>
        <input name="abs_gb" type="number" step="0.1" min="0" placeholder="608"></div>`,
    buildRequest($m) {
      const days = $m.querySelector('[name=days]').value.trim();
      const addGb = $m.querySelector('[name=add_gb]').value.trim();
      const absGb = $m.querySelector('[name=abs_gb]').value.trim();
      if (addGb && absGb) throw new Error('Укажите либо прибавку, либо точный лимит — не оба');
      if (!days && !addGb && !absGb) throw new Error('Нет изменений');
      const body = {};
      const parts = [];
      if (days) { body.days = parseInt(days, 10); parts.push(`срок ${body.days > 0 ? '+' : ''}${body.days} дн.`); }
      if (addGb) { body.add_traffic_gb = parseFloat(addGb); parts.push(`трафик ${body.add_traffic_gb > 0 ? '+' : ''}${body.add_traffic_gb} ГБ`); }
      if (absGb) { body.traffic_limit_gb = parseFloat(absGb); parts.push(`лимит = ${body.traffic_limit_gb} ГБ`); }
      const danger = (body.days || 0) < 0 || (body.add_traffic_gb || 0) < 0 ||
        (body.traffic_limit_gb != null && body.traffic_limit_gb * GB < (vpn.bypass_trafficLimitBytes || 0));
      return {
        body, danger,
        confirmHtml: `ByPass: <b>${esc(parts.join(', '))}</b>
          <div class="muted">Было: до ${fmtDate(vpn.bypass_expireAt)}, ${fmtBytes(vpn.bypass_trafficLimitBytes)}</div>`,
      };
    },
  });
}

function modalGift(userId, vpn, onDone) {
  actionModal({
    title: 'Подарить пользователю',
    url: `/api/users/${userId}/gift`,
    onDone,
    supportsForceLocal: true,
    fieldsHtml: `
      <div class="field"><label>Дней подписки (пусто = не дарить)</label>
        <input name="days" type="number" min="1" max="3650" placeholder="7"></div>
      <div class="field"><label>ГБ ByPass (пусто = не дарить)</label>
        <input name="gb" type="number" step="0.1" min="0.1" placeholder="5"></div>`,
    buildRequest($m) {
      const days = $m.querySelector('[name=days]').value.trim();
      const gb = $m.querySelector('[name=gb]').value.trim();
      if (!days && !gb) throw new Error('Укажите дни и/или гигабайты');
      const body = {};
      const parts = [];
      if (days) { body.days = parseInt(days, 10); parts.push(`+${body.days} дн. подписки`); }
      if (gb) { body.bypass_gb = parseFloat(gb); parts.push(`+${body.bypass_gb} ГБ ByPass`); }
      return { body, danger: false, confirmHtml: `Подарок: <b>${esc(parts.join(' и '))}</b>` };
    },
  });
}

function modalEmail(userId, u, onDone) {
  actionModal({
    title: 'Изменить email',
    url: `/api/users/${userId}/email`,
    onDone,
    fieldsHtml: `
      <div class="muted" style="margin-bottom:12px">Сейчас: <b>${esc(u.email || '—')}</b></div>
      <div class="field"><label>Новый email</label>
        <input name="email" type="email" required placeholder="user@example.com"></div>`,
    buildRequest($m) {
      const email = $m.querySelector('[name=email]').value.trim();
      if (!email) throw new Error('Введите email');
      return {
        body: { email },
        danger: false,
        confirmHtml: `Email: <b>${esc(u.email || '—')}</b><span class="arrow">→</span><b>${esc(email)}</b>`,
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
      ? `<div class="muted" style="margin-bottom:12px">Устройство: <span class="mono">${esc(hwid)}</span></div>`
      : `<div class="warn-note" style="margin-bottom:12px">Будут отвязаны все HWID-привязки пользователя в Remnawave.
         Пользователю придётся заново подключить свои устройства.</div>`,
    buildRequest() {
      return {
        body: hwid ? { hwid } : {},
        danger: true,
        confirmHtml: hwid
          ? `Отвязать устройство <span class="mono">${esc(hwid)}</span>`
          : `Отвязать <b>ВСЕ</b> устройства пользователя ${esc(userId)}`,
      };
    },
  });
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
        <td><span class="badge ${o.role === 'owner' ? 'badge-yellow' : 'badge-blue'}">${esc(o.role)}</span></td>
        <td>${o.active ? '<span class="badge badge-green">активен</span>' : '<span class="badge badge-red">отключён</span>'}</td>
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

function modalOperatorCreate() {
  const $m = openModal(`
    <h2>Новый оператор</h2>
    <form id="op-form">
      <div class="field"><label>Логин (a-z, 0-9, _.-)</label><input name="login" required minlength="3" pattern="[a-zA-Z0-9_.\\-]+"></div>
      <div class="field"><label>Имя</label><input name="name" required></div>
      <div class="field"><label>Пароль (мин. 8 символов)</label><input name="password" type="password" required minlength="8"></div>
      <div class="field"><label>Роль</label>
        <select name="role"><option value="operator">operator</option><option value="owner">owner</option></select></div>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" onclick="closeModal()">Отмена</button>
        <button type="submit" class="btn">Создать</button>
      </div>
    </form>`);
  $m.querySelector('#op-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    try {
      await api('/api/operators', {
        method: 'POST',
        body: { login: f.login.value.trim(), name: f.name.value.trim(), password: f.password.value, role: f.role.value },
      });
      closeModal();
      toast('Оператор создан ✓');
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
      <div class="field"><label>Новый пароль (пусто = не менять)</label><input name="password" type="password" minlength="8"></div>
      <div class="field"><label>Роль</label>
        <select name="role">
          <option value="operator" ${op.role === 'operator' ? 'selected' : ''}>operator</option>
          <option value="owner" ${op.role === 'owner' ? 'selected' : ''}>owner</option>
        </select></div>
      <label style="display:flex;gap:8px;align-items:center;color:var(--text)">
        <input type="checkbox" name="active" style="width:auto" ${op.active ? 'checked' : ''}> Учётка активна
      </label>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" onclick="closeModal()">Отмена</button>
        <button type="submit" class="btn">Сохранить</button>
      </div>
    </form>`);
  $m.querySelector('#op-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    const body = { name: f.name.value.trim(), role: f.role.value, active: f.active.checked };
    if (f.password.value) body.password = f.password.value;
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

  if (!S.token) { viewLogin(); return; }
  if (!S.me) {
    const ok = await loadMe();
    if (!ok) { viewLogin(); return; }
  }

  const userMatch = hash.match(/^#\/user\/(\d+)$/);
  if (userMatch) { setNav('search'); viewUser(parseInt(userMatch[1], 10)); return; }
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

// Интерфейс работника для Remnawave: ноды, профили, внутренние сквады, хосты.
// Вход — по API-токену с ограниченными скоупами. Все запросы идут через api.php (прокси).
'use strict';

const API_URL = 'api.php';
const TOKEN_KEY = 'rw_worker_token';
const MONACO_BASE = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min';
const GB = 1024 ** 3;

let token = readToken();
let currentPage = null; // { dirty?: boolean, destroy?: () => void }
let shellMounted = false;

/* =========================================================================
 * Утилиты
 * ========================================================================= */

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function readToken() {
    try {
        return sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY) || '';
    } catch {
        return '';
    }
}

function saveToken(value, remember) {
    try {
        sessionStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(TOKEN_KEY);
        if (value) (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, value);
    } catch { /* приватный режим — живём без хранилища */ }
    token = value;
}

function jwtPayload(t) {
    try {
        const part = t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        return JSON.parse(decodeURIComponent(escape(atob(part))));
    } catch {
        return null;
    }
}

function flag(cc) {
    if (!cc || cc.length !== 2 || cc.toUpperCase() === 'XX') return '🏳️';
    return String.fromCodePoint(...[...cc.toUpperCase()].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

function fmtBytes(b) {
    if (b === null || b === undefined || isNaN(b)) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let i = 0;
    let v = Number(b);
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 100 || i === 0 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}

function fmtUptime(sec) {
    if (!sec) return '—';
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d) return `${d}д ${h}ч`;
    if (h) return `${h}ч ${m}м`;
    return `${m}м`;
}

function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function plural(n, one, few, many) {
    const m10 = n % 10;
    const m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
    }
    toast('success', 'Скопировано');
}

function parseTags(str) {
    return String(str || '')
        .split(',')
        .map((s) => s.trim().toUpperCase().replace(/\s+/g, '_'))
        .filter(Boolean);
}

/* =========================================================================
 * API
 * ========================================================================= */

class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

async function api(method, path, body) {
    let res;
    try {
        res = await fetch(`${API_URL}?p=${encodeURIComponent(path)}`, {
            method,
            headers: {
                Authorization: `Bearer ${token}`,
                ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
            },
            body: body !== undefined ? JSON.stringify(body) : undefined,
        });
    } catch (e) {
        throw new ApiError(0, 'Нет связи с сервером');
    }

    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { /* не JSON */ }

    if (!res.ok) {
        let msg = (data && data.message) || `Ошибка ${res.status}`;
        if (res.status === 403 && /forbidden/i.test(msg)) {
            msg = 'Недостаточно прав: у вашего токена нет доступа к этому действию';
        }
        if (res.status === 401) {
            msg = 'Токен недействителен или истёк';
        }
        if (data && Array.isArray(data.errors) && data.errors.length) {
            msg += ': ' + data.errors.map((e) => (e.path && e.path.length ? e.path.join('.') + ' — ' : '') + e.message).join('; ');
        }
        throw new ApiError(res.status, msg);
    }
    return data && 'response' in data ? data.response : data;
}

// Обёртка для действий из UI: ловит ошибки, показывает тосты, разлогинивает на 401
async function run(fn, { success } = {}) {
    try {
        const r = await fn();
        if (success) toast('success', success);
        return r;
    } catch (e) {
        handleError(e);
        return undefined;
    }
}

function handleError(e) {
    if (e instanceof ApiError && e.status === 401) {
        logout();
        toast('error', 'Сессия завершена', e.message);
        return;
    }
    console.error(e);
    toast('error', 'Ошибка', e.message || String(e));
}

async function loadProfiles() {
    const r = await api('GET', '/api/config-profiles');
    return r.configProfiles || [];
}

/* =========================================================================
 * Тосты, меню, модалки
 * ========================================================================= */

function toast(type, title, message = '') {
    const icons = { success: 'ph-check-circle', error: 'ph-warning-circle', info: 'ph-info' };
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<i class="ph ${icons[type] || icons.info}"></i><div><div class="t">${esc(title)}</div>${message ? `<div class="m">${esc(message)}</div>` : ''}</div>`;
    $('#toasts').appendChild(el);
    setTimeout(() => {
        el.style.transition = 'opacity .25s';
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 260);
    }, type === 'error' ? 7000 : 3500);
}

let openMenuEl = null;
function closeMenu() {
    if (openMenuEl) { openMenuEl.remove(); openMenuEl = null; }
}

function openMenu(anchor, items) {
    closeMenu();
    const menu = document.createElement('div');
    menu.className = 'menu';
    items.forEach((it) => {
        if (!it) return;
        if (it.divider) { menu.insertAdjacentHTML('beforeend', '<div class="menu-divider"></div>'); return; }
        if (it.label && !it.onClick) { menu.insertAdjacentHTML('beforeend', `<div class="menu-label">${esc(it.label)}</div>`); return; }
        const row = document.createElement('div');
        row.className = 'menu-item' + (it.red ? ' red' : '');
        row.innerHTML = `<i class="ph ${it.icon || 'ph-dot'}"></i><span>${esc(it.text)}</span>`;
        row.addEventListener('click', (e) => { e.stopPropagation(); closeMenu(); it.onClick(); });
        menu.appendChild(row);
    });
    document.body.appendChild(menu);
    const r = anchor.getBoundingClientRect();
    const mw = menu.offsetWidth;
    const mh = menu.offsetHeight;
    let left = Math.min(r.right - mw, window.innerWidth - mw - 8);
    left = Math.max(8, left);
    let top = r.bottom + 6;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    openMenuEl = menu;
}
document.addEventListener('click', (e) => { if (openMenuEl && !openMenuEl.contains(e.target)) closeMenu(); });
window.addEventListener('scroll', closeMenu, true);
window.addEventListener('resize', closeMenu);

const modalStack = [];
function openModal({ title, icon, size = '', body = '', foot = '', onMount, onClose }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal ${size}" role="dialog" aria-modal="true">
            <div class="modal-head">
                <div class="modal-title">${icon ? `<i class="ph-duotone ${icon}" style="color:var(--cyan-4);font-size:20px"></i>` : ''}${esc(title)}</div>
                <button class="icon-btn subtle" data-close aria-label="Закрыть"><i class="ph ph-x"></i></button>
            </div>
            <div class="modal-body">${body}</div>
            ${foot ? `<div class="modal-foot">${foot}</div>` : ''}
        </div>`;
    const close = () => {
        const i = modalStack.indexOf(close);
        if (i >= 0) modalStack.splice(i, 1);
        overlay.remove();
        if (onClose) onClose();
    };
    let downOnOverlay = false;
    overlay.addEventListener('mousedown', (e) => { downOnOverlay = e.target === overlay; });
    overlay.addEventListener('click', (e) => { if (downOnOverlay && e.target === overlay) close(); });
    $('[data-close]', overlay).addEventListener('click', close);
    document.body.appendChild(overlay);
    modalStack.push(close);
    const modal = $('.modal', overlay);
    if (onMount) onMount(modal, close);
    const first = $('input:not([type=checkbox]):not([type=hidden]), select, textarea', modal);
    if (first) setTimeout(() => first.focus(), 30);
    return close;
}
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (openMenuEl) { closeMenu(); return; }
        if (modalStack.length) modalStack[modalStack.length - 1]();
    }
});

function confirmDialog({ title, text, confirmText = 'Подтвердить', danger = false }) {
    return new Promise((resolve) => {
        let done = false;
        openModal({
            title,
            size: 'sm',
            body: `<div class="sm" style="color:var(--dark-1)">${text}</div>`,
            foot: `<button class="btn btn-default" data-no>Отмена</button>
                   <button class="btn ${danger ? 'btn-red' : ''}" data-yes>${esc(confirmText)}</button>`,
            onMount: (m, close) => {
                $('[data-no]', m).onclick = () => close();
                $('[data-yes]', m).onclick = () => { done = true; close(); resolve(true); };
            },
            onClose: () => { if (!done) resolve(false); },
        });
    });
}

function setBusy(btn, busy) {
    if (!btn) return;
    if (busy) {
        btn.dataset.html = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span>';
    } else {
        btn.disabled = false;
        if (btn.dataset.html) btn.innerHTML = btn.dataset.html;
    }
}

// Кнопка "..." в строке: не даём клику всплыть до обработчика строки
function bindRowMenu(container, selector, getItems) {
    $$(selector, container).forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            openMenu(btn, getItems(btn.dataset.id));
        });
    });
}

/* =========================================================================
 * Вход
 * ========================================================================= */

function renderLogin() {
    shellMounted = false;
    destroyPage();
    $('#app').innerHTML = `
        <div class="auth">
            <form class="auth-card" id="login-form" autocomplete="off">
                <div class="logo">
                    <div class="logo-mark"><i class="ph ph-waves"></i></div>
                    <div class="logo-title">Remnawave</div>
                </div>
                <h2>Вход для работника</h2>
                <p>Вставьте API-токен, который выдал администратор.</p>
                <div class="field">
                    <label for="token">Токен доступа</label>
                    <div class="input-wrap">
                        <i class="ph ph-key"></i>
                        <input class="input" id="token" type="password" placeholder="eyJhbGciOi…" required spellcheck="false">
                        <button type="button" class="icon-btn subtle right" id="toggle-token" aria-label="Показать"><i class="ph ph-eye"></i></button>
                    </div>
                    <div class="error-text hidden" id="login-error"></div>
                </div>
                <label class="switch">
                    <input type="checkbox" id="remember"><span class="track"></span>
                    <span class="sm">Запомнить на этом устройстве</span>
                </label>
                <button class="btn btn-block" type="submit" id="login-btn"><i class="ph ph-sign-in"></i>Войти</button>
            </form>
        </div>`;

    const input = $('#token');
    $('#toggle-token').onclick = () => {
        input.type = input.type === 'password' ? 'text' : 'password';
        $('#toggle-token i').className = `ph ${input.type === 'password' ? 'ph-eye' : 'ph-eye-slash'}`;
    };
    $('#login-form').onsubmit = async (e) => {
        e.preventDefault();
        const value = input.value.trim().replace(/^Bearer\s+/i, '');
        const errEl = $('#login-error');
        errEl.classList.add('hidden');
        input.classList.remove('invalid');
        if (!value) return;
        const btn = $('#login-btn');
        setBusy(btn, true);
        const prev = token;
        token = value;
        try {
            await api('GET', '/api/config-profiles');
        } catch (err) {
            // 403 — токен рабочий, просто нет скоупа на профили. Пускаем.
            if (!(err instanceof ApiError && err.status === 403 && /прав/.test(err.message))) {
                token = prev;
                setBusy(btn, false);
                input.classList.add('invalid');
                errEl.textContent = err.message;
                errEl.classList.remove('hidden');
                return;
            }
        }
        saveToken(value, $('#remember').checked);
        if (!location.hash || location.hash === '#/') location.hash = '#/nodes';
        render();
    };
    setTimeout(() => input.focus(), 30);
}

function logout() {
    saveToken('', false);
    renderLogin();
}

/* =========================================================================
 * Оболочка и роутинг
 * ========================================================================= */

const NAV = [
    { id: 'squads', text: 'Внутренние сквады', icon: 'ph-circles-three-plus' },
    { id: 'profiles', text: 'Профили', icon: 'ph-file-code' },
    { id: 'hosts', text: 'Хосты', icon: 'ph-list-checks' },
    { id: 'nodes', text: 'Ноды', icon: 'ph-cpu' },
];

function mountShell() {
    const p = jwtPayload(token);
    const exp = p && p.exp ? new Date(p.exp * 1000) : null;
    $('#app').innerHTML = `
        <div class="shell">
            <div class="backdrop" id="backdrop"></div>
            <nav class="navbar" id="navbar">
                <div class="navbar-head">
                    <div class="logo">
                        <div class="logo-mark"><i class="ph ph-waves"></i></div>
                        <div>
                            <div class="logo-title">Remnawave</div>
                            <div class="logo-sub">Доступ работника</div>
                        </div>
                    </div>
                </div>
                <div class="nav-section">Управление</div>
                ${NAV.map((n) => `<a class="nav-link" href="#/${n.id}" data-nav="${n.id}"><i class="ph-duotone ${n.icon}"></i>${n.text}</a>`).join('')}
                <div class="navbar-foot">
                    <div class="token-info">
                        <i class="ph-duotone ph-key"></i>
                        <div style="min-width:0">
                            <div class="sm fw6">API-токен</div>
                            <div class="xs dimmed ellipsis">${exp ? 'действует до ' + esc(fmtDate(exp.toISOString())) : 'ограниченный доступ'}</div>
                        </div>
                    </div>
                    <button class="btn btn-default btn-block" id="logout"><i class="ph ph-sign-out"></i>Выйти</button>
                </div>
            </nav>
            <main class="main">
                <div class="mobile-bar">
                    <button class="icon-btn" id="burger" aria-label="Меню"><i class="ph ph-list"></i></button>
                    <div class="logo"><div class="logo-mark"><i class="ph ph-waves"></i></div><div class="logo-title">Remnawave</div></div>
                    <span style="width:34px"></span>
                </div>
                <div id="page"></div>
            </main>
        </div>`;
    const navbar = $('#navbar');
    const backdrop = $('#backdrop');
    const closeNav = () => { navbar.classList.remove('open'); backdrop.classList.remove('open'); };
    $('#burger').onclick = () => { navbar.classList.add('open'); backdrop.classList.add('open'); };
    backdrop.onclick = closeNav;
    $$('.nav-link', navbar).forEach((a) => a.addEventListener('click', closeNav));
    $('#logout').onclick = async () => {
        if (await confirmDialog({ title: 'Выйти?', text: 'Токен будет удалён из этого браузера.', confirmText: 'Выйти', danger: true })) logout();
    };
    shellMounted = true;
}

function destroyPage() {
    if (currentPage && currentPage.destroy) currentPage.destroy();
    currentPage = null;
}

let lastHash = location.hash;
function render() {
    if (!token) { renderLogin(); return; }
    if (!shellMounted) mountShell();

    const hash = location.hash.replace(/^#\/?/, '');
    const [route, param] = hash.split('/');
    const page = $('#page');

    $$('.nav-link').forEach((a) => {
        const id = a.dataset.nav;
        a.classList.toggle('active', id === route || (id === 'profiles' && route === 'profile'));
    });

    destroyPage();
    closeMenu();
    while (modalStack.length) modalStack[modalStack.length - 1]();

    const routes = { nodes: pageNodes, profiles: pageProfiles, profile: pageProfileEditor, squads: pageSquads, hosts: pageHosts };
    const fn = routes[route];
    if (!fn) { location.replace('#/nodes'); return; }
    currentPage = {};
    lastHash = location.hash;
    fn(page, param, currentPage);
}

window.addEventListener('hashchange', () => {
    if (currentPage && currentPage.dirty && location.hash !== lastHash) {
        if (!window.confirm('Есть несохранённые изменения. Уйти со страницы?')) {
            history.replaceState(null, '', lastHash);
            return;
        }
    }
    render();
});
window.addEventListener('beforeunload', (e) => {
    if (currentPage && currentPage.dirty) { e.preventDefault(); e.returnValue = ''; }
});

function pageHead({ title, crumbs = [], actions = '' }) {
    return `
        <div class="page-head">
            <div>
                <div class="crumbs">${['Управление', ...crumbs]
                    .map((c) => (typeof c === 'string' ? `<span>${esc(c)}</span>` : `<a href="${c.href}">${esc(c.text)}</a>`))
                    .join('<i class="ph ph-caret-right"></i>')}</div>
                <div class="page-title">${title}</div>
            </div>
            <div class="page-actions">${actions}</div>
        </div>`;
}

function loaderHtml() {
    return '<div class="loader-wrap"><span class="spinner"></span></div>';
}

function errorBox(e) {
    const noScope = e instanceof ApiError && e.status === 403;
    return `<div class="alert ${noScope ? 'warn' : ''}"><i class="ph ${noScope ? 'ph-lock-key' : 'ph-warning-circle'}"></i>
        <div><div class="fw6">${noScope ? 'Нет доступа к этому разделу' : 'Не удалось загрузить данные'}</div>
        <div class="sm">${esc(noScope ? 'Токен не даёт прав на этот раздел. Если доступ нужен — попросите администратора добавить скоуп.' : e.message)}</div></div></div>`;
}

/* =========================================================================
 * Ноды
 * ========================================================================= */

function nodeStatus(n) {
    if (n.isDisabled) return { cls: 'disabled', text: 'Отключена' };
    if (n.isConnecting) return { cls: 'connecting', text: 'Подключение…' };
    if (n.isConnected) return { cls: 'online', text: 'Онлайн' };
    return { cls: 'offline', text: n.lastStatusMessage ? `Офлайн: ${n.lastStatusMessage}` : 'Офлайн' };
}

function pageNodes(root, _param, page) {
    root.innerHTML = pageHead({
        title: 'Ноды',
        crumbs: ['Ноды'],
        actions: `
            <button class="icon-btn" id="refresh" title="Обновить"><i class="ph ph-arrows-clockwise"></i></button>
            <button class="btn btn-light" id="restart-all"><i class="ph ph-arrow-counter-clockwise"></i>Перезапустить все</button>
            <button class="btn" id="create"><i class="ph ph-plus"></i>Создать</button>`,
    }) + `<div id="list">${loaderHtml()}</div>`;

    let nodes = [];
    const list = $('#list', root);

    const load = async (silent) => {
        try {
            nodes = await api('GET', '/api/nodes');
            draw();
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) return handleError(e);
            if (!silent) list.innerHTML = errorBox(e);
        }
    };

    const draw = () => {
        if (!nodes.length) {
            list.innerHTML = `<div class="card empty"><i class="ph-duotone ph-cpu"></i>Нод пока нет<div class="sm" style="margin-top:6px">Нажмите «Создать», чтобы добавить первую ноду</div></div>`;
            return;
        }
        list.innerHTML = `<div class="node-list">${nodes.map((n) => {
            const st = nodeStatus(n);
            const limit = n.isTrafficTrackingActive && n.trafficLimitBytes ? n.trafficLimitBytes : 0;
            const pct = limit ? Math.min(100, (n.trafficUsedBytes / limit) * 100) : 0;
            return `
            <div class="card clickable node-row" data-id="${esc(n.uuid)}">
                <span class="dot ${st.cls}" title="${esc(st.text)}"></span>
                <div style="min-width:0">
                    <div class="name"><span class="flag">${flag(n.countryCode)}</span><span class="ellipsis">${esc(n.name)}</span></div>
                    <div class="xs dimmed ellipsis">${esc(st.text)}${n.tags && n.tags.length ? ' · ' + n.tags.map(esc).join(', ') : ''}</div>
                    <div class="xs dimmed mono ellipsis hidden m-only">${esc(n.address)}:${esc(n.port ?? '')}</div>
                </div>
                <div class="c-addr mono sm ellipsis dimmed">${esc(n.address)}${n.port ? ':' + esc(n.port) : ''}</div>
                <div class="c-traffic">
                    <div class="xs" style="margin-bottom:4px">${fmtBytes(n.trafficUsedBytes)} <span class="dimmed">/ ${limit ? fmtBytes(limit) : '∞'}</span></div>
                    ${limit ? `<div class="progress ${pct >= 100 ? 'full' : pct >= 80 ? 'warn' : ''}"><div style="width:${pct}%"></div></div>` : '<div class="progress"><div style="width:0"></div></div>'}
                </div>
                <div class="c-online"><span class="online-pill" title="Пользователей онлайн"><i class="ph-duotone ph-users"></i>${esc(n.usersOnline ?? 0)}</span></div>
                <div class="c-ver">${n.versions && n.versions.xray ? `<span class="badge gray lower">xray ${esc(n.versions.xray)}</span>` : `<span class="xs dimmed">—</span>`}
                    ${n.xrayUptime ? `<div class="xs dimmed" style="margin-top:3px">${fmtUptime(n.xrayUptime)}</div>` : ''}</div>
                <button class="icon-btn subtle" data-menu data-id="${esc(n.uuid)}" aria-label="Действия"><i class="ph ph-dots-three-vertical"></i></button>
            </div>`;
        }).join('')}</div>`;

        $$('.node-row', list).forEach((row) => row.addEventListener('click', () => {
            const n = nodes.find((x) => x.uuid === row.dataset.id);
            if (n) nodeModal(n, () => load(true));
        }));
        bindRowMenu(list, '[data-menu]', (id) => {
            const n = nodes.find((x) => x.uuid === id);
            const act = (path, body, msg) => run(() => api('POST', path, body), { success: msg }).then(() => load(true));
            return [
                { label: n.name },
                { text: 'Редактировать', icon: 'ph-pencil-simple', onClick: () => nodeModal(n, () => load(true)) },
                n.isDisabled
                    ? { text: 'Включить', icon: 'ph-power', onClick: () => act(`/api/nodes/${id}/actions/enable`, undefined, 'Нода включена') }
                    : { text: 'Отключить', icon: 'ph-power', onClick: () => act(`/api/nodes/${id}/actions/disable`, undefined, 'Нода отключена') },
                { text: 'Перезапустить', icon: 'ph-arrow-counter-clockwise', onClick: () => act(`/api/nodes/${id}/actions/restart`, { forceRestart: false }, 'Перезапуск отправлен') },
                {
                    text: 'Сбросить трафик', icon: 'ph-chart-line-down', onClick: async () => {
                        if (await confirmDialog({ title: 'Сбросить трафик?', text: `Счётчик трафика ноды <b>${esc(n.name)}</b> будет обнулён.`, confirmText: 'Сбросить' })) {
                            act(`/api/nodes/${id}/actions/reset-traffic`, undefined, 'Трафик сброшен');
                        }
                    },
                },
                { text: 'Копировать UUID', icon: 'ph-copy', onClick: () => copyText(id) },
                { divider: true },
                {
                    text: 'Удалить', icon: 'ph-trash', red: true, onClick: async () => {
                        if (await confirmDialog({ title: 'Удалить ноду?', text: `Нода <b>${esc(n.name)}</b> будет удалена из панели. Это действие необратимо.`, confirmText: 'Удалить', danger: true })) {
                            run(() => api('DELETE', `/api/nodes/${id}`), { success: 'Нода удалена' }).then(() => load(true));
                        }
                    },
                },
            ];
        });
    };

    $('#refresh', root).onclick = () => load();
    $('#create', root).onclick = () => nodeModal(null, () => load(true));
    $('#restart-all', root).onclick = async () => {
        if (await confirmDialog({ title: 'Перезапустить все ноды?', text: 'Xray будет перезапущен на всех нодах. Пользователи на пару секунд потеряют соединение.', confirmText: 'Перезапустить' })) {
            run(() => api('POST', '/api/nodes/actions/restart-all', { forceRestart: false }), { success: 'Перезапуск отправлен на все ноды' }).then(() => load(true));
        }
    };

    load();
    const timer = setInterval(() => { if (!modalStack.length && !openMenuEl) load(true); }, 15000);
    page.destroy = () => clearInterval(timer);
}

function dockerCompose(secret, port) {
    return `services:
  remnanode:
    container_name: remnanode
    hostname: remnanode
    image: remnawave/node:latest
    network_mode: host
    restart: always
    cap_add:
      - NET_ADMIN
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    environment:
      - NODE_PORT=${port || 2222}
      - SECRET_KEY="${(secret || '').trimEnd()}"`;
}

async function nodeModal(node, onDone) {
    const isNew = !node;
    const closeLoading = openModal({ title: isNew ? 'Новая нода' : node.name, icon: 'ph-cpu', size: 'lg', body: loaderHtml() });
    let profiles = [];
    let secret = '';
    try {
        const [p, k] = await Promise.all([
            loadProfiles(),
            isNew ? api('GET', '/api/keygen').catch((e) => { if (e.status === 401) throw e; return null; }) : Promise.resolve(null),
        ]);
        profiles = p;
        secret = k && k.secretKey ? k.secretKey : '';
    } catch (e) {
        closeLoading();
        handleError(e);
        return;
    }
    closeLoading();

    const n = node || {};
    const cp = n.configProfile || {};
    const activeProfile = cp.activeConfigProfileUuid || (profiles[0] && profiles[0].uuid) || '';
    const activeInbounds = new Set((cp.activeInbounds || []).map((i) => i.uuid));

    const body = `
        ${isNew ? `
        <div class="fieldset">
            <div class="fieldset-legend"><i class="ph ph-terminal-window"></i>1. Установите Remnawave Node на сервер</div>
            ${secret ? `
            <div class="sm dimmed">Создайте <span class="mono">docker-compose.yml</span> на сервере ноды и запустите <span class="mono">docker compose up -d</span>.</div>
            <div class="codeblock" id="compose">${esc(dockerCompose(secret, 2222))}<button class="icon-btn copy" type="button" id="copy-compose" title="Копировать"><i class="ph ph-copy"></i></button></div>`
            : `<div class="alert warn"><i class="ph ph-lock-key"></i><div class="sm">Не удалось получить SECRET_KEY: у токена нет скоупа <span class="mono">keygen:get</span>.</div></div>`}
        </div>
        <div class="fieldset-legend" style="margin-top:2px"><i class="ph ph-sliders"></i>2. Параметры ноды в панели</div>` : ''}
        <form class="fgrid" id="node-form" autocomplete="off">
            <div class="field"><label>Название<span class="req">*</span></label>
                <input class="input" name="name" value="${esc(n.name)}" minlength="3" maxlength="30" required placeholder="Germany 1"></div>
            <div class="field"><label>Код страны</label>
                <div class="input-wrap"><i class="flag" id="cc-flag" style="font-style:normal">${flag(n.countryCode)}</i>
                <input class="input" name="countryCode" value="${esc(n.countryCode && n.countryCode !== 'XX' ? n.countryCode : '')}" maxlength="2" placeholder="DE" style="text-transform:uppercase"></div></div>
            <div class="field"><label>Адрес<span class="req">*</span></label>
                <input class="input mono" name="address" value="${esc(n.address)}" required minlength="2" placeholder="1.2.3.4 или node.example.com"></div>
            <div class="field"><label>Порт ноды</label>
                <input class="input mono" name="port" type="number" min="1" max="65535" value="${esc(n.port ?? 2222)}"></div>

            <div class="fieldset full">
                <div class="fieldset-legend"><i class="ph ph-file-code"></i>Профиль конфигурации</div>
                ${profiles.length ? `
                <select class="select" name="profile">${profiles.map((p) => `<option value="${esc(p.uuid)}" ${p.uuid === activeProfile ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
                <div class="row between"><span class="sm dimmed">Активные инбаунды</span><button type="button" class="btn btn-subtle btn-sm" id="inb-all">Выбрать все</button></div>
                <div id="inb-list" class="stack" style="gap:2px"></div>` : `<div class="alert warn"><i class="ph ph-warning"></i><div class="sm">Нет доступных профилей. Сначала создайте профиль конфигурации.</div></div>`}
            </div>

            <div class="fieldset full">
                <div class="fieldset-legend"><i class="ph ph-chart-bar"></i>Трафик</div>
                <label class="switch"><input type="checkbox" name="isTrafficTrackingActive" ${n.isTrafficTrackingActive ? 'checked' : ''}><span class="track"></span><span>Учитывать трафик</span></label>
                <div class="fgrid">
                    <div class="field"><label>Лимит, ГБ</label><input class="input" name="limit" type="number" min="0" step="any" value="${n.trafficLimitBytes ? +(n.trafficLimitBytes / GB).toFixed(2) : ''}" placeholder="0 — без лимита"></div>
                    <div class="field"><label>День сброса</label><input class="input" name="trafficResetDay" type="number" min="1" max="31" value="${esc(n.trafficResetDay ?? '')}" placeholder="1–31"></div>
                    <div class="field"><label>Уведомить при, %</label><input class="input" name="notifyPercent" type="number" min="0" max="100" value="${esc(n.notifyPercent ?? '')}" placeholder="например 80"></div>
                    <div class="field"><label>Множитель потребления</label><input class="input" name="consumptionMultiplier" type="number" min="0" max="100" step="0.1" value="${esc(n.consumptionMultiplier ?? 1)}"></div>
                </div>
            </div>

            <div class="field full"><label>Теги</label>
                <div class="desc">Через запятую. Только A–Z, 0–9, _ и :</div>
                <input class="input mono" name="tags" value="${esc((n.tags || []).join(', '))}" placeholder="EU, PREMIUM"></div>
            <div class="field full"><label>Заметка</label>
                <textarea class="textarea" name="note" maxlength="255" rows="2">${esc(n.note || '')}</textarea></div>
        </form>
        ${!isNew ? `<div class="xs dimmed">UUID: <span class="mono">${esc(n.uuid)}</span> · создана ${esc(fmtDate(n.createdAt))}</div>` : ''}`;

    openModal({
        title: isNew ? 'Новая нода' : node.name,
        icon: 'ph-cpu',
        size: 'lg',
        body,
        foot: `${!isNew ? '<button class="btn btn-red-light left" id="del"><i class="ph ph-trash"></i>Удалить</button>' : ''}
               <button class="btn btn-default" data-cancel>Отмена</button>
               <button class="btn" id="save"><i class="ph ph-floppy-disk"></i>${isNew ? 'Создать' : 'Сохранить'}</button>`,
        onMount: (m, close) => {
            const form = $('#node-form', m);
            const f = (name) => form.elements[name];
            $('[data-cancel]', m).onclick = close;

            f('countryCode').addEventListener('input', () => { $('#cc-flag', m).textContent = flag(f('countryCode').value); });

            if (isNew && secret) {
                const updateCompose = () => {
                    const block = $('#compose', m);
                    block.firstChild.textContent = dockerCompose(secret, f('port').value);
                };
                f('port').addEventListener('input', updateCompose);
                $('#copy-compose', m).onclick = () => copyText(dockerCompose(secret, f('port').value));
            }

            const drawInbounds = () => {
                const list = $('#inb-list', m);
                if (!list) return;
                const p = profiles.find((x) => x.uuid === f('profile').value);
                const inb = (p && p.inbounds) || [];
                const firstTime = !list.dataset.drawn;
                list.dataset.drawn = '1';
                list.innerHTML = inb.length
                    ? inb.map((i) => `<label class="check"><input type="checkbox" value="${esc(i.uuid)}" ${
                        (firstTime && !isNew && p.uuid === activeProfile ? activeInbounds.has(i.uuid) : true) ? 'checked' : ''}>
                        <span class="tag mono sm fw6">${esc(i.tag)}</span><span class="spacer"></span>
                        <span class="badge gray lower">${esc(i.type)}</span>${i.port ? `<span class="badge lower">${esc(i.port)}</span>` : ''}</label>`).join('')
                    : '<div class="sm dimmed" style="padding:6px 10px">В профиле нет инбаундов</div>';
            };
            if (profiles.length) {
                drawInbounds();
                f('profile').addEventListener('change', drawInbounds);
                $('#inb-all', m).onclick = () => {
                    const boxes = $$('#inb-list input', m);
                    const all = boxes.every((b) => b.checked);
                    boxes.forEach((b) => { b.checked = !all; });
                };
            }

            if (!isNew) {
                $('#del', m).onclick = async () => {
                    if (await confirmDialog({ title: 'Удалить ноду?', text: `Нода <b>${esc(n.name)}</b> будет удалена. Это действие необратимо.`, confirmText: 'Удалить', danger: true })) {
                        const ok = await run(() => api('DELETE', `/api/nodes/${n.uuid}`), { success: 'Нода удалена' });
                        if (ok !== undefined) { close(); onDone(); }
                    }
                };
            }

            $('#save', m).onclick = async () => {
                if (!form.reportValidity()) return;
                if (!profiles.length) { toast('error', 'Выберите профиль конфигурации'); return; }
                const num = (name) => (f(name).value === '' ? undefined : Number(f(name).value));
                const limitGb = num('limit');
                const payload = {
                    name: f('name').value.trim(),
                    address: f('address').value.trim(),
                    port: num('port'),
                    countryCode: (f('countryCode').value.trim() || 'XX').toUpperCase(),
                    isTrafficTrackingActive: f('isTrafficTrackingActive').checked,
                    trafficLimitBytes: limitGb !== undefined ? Math.round(limitGb * GB) : 0,
                    trafficResetDay: num('trafficResetDay'),
                    notifyPercent: num('notifyPercent'),
                    consumptionMultiplier: num('consumptionMultiplier'),
                    tags: parseTags(f('tags').value),
                    configProfile: {
                        activeConfigProfileUuid: f('profile').value,
                        activeInbounds: $$('#inb-list input:checked', m).map((b) => b.value),
                    },
                };
                const note = f('note').value.trim();
                if (isNew) { if (note) payload.note = note; } else { payload.note = note || null; }
                Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);

                const btn = $('#save', m);
                setBusy(btn, true);
                const r = await run(
                    () => (isNew ? api('POST', '/api/nodes', payload) : api('PATCH', '/api/nodes', { uuid: n.uuid, ...payload })),
                    { success: isNew ? 'Нода создана' : 'Нода сохранена' },
                );
                setBusy(btn, false);
                if (r !== undefined) { close(); onDone(); }
            };
        },
    });
}

/* =========================================================================
 * Профили конфигурации
 * ========================================================================= */

function defaultProfileConfig() {
    const rnd = Math.floor(Math.random() * 999999) + 1;
    return {
        log: { loglevel: 'info' },
        inbounds: [
            {
                tag: `Shadowsocks_${rnd}`,
                port: 1234,
                protocol: 'shadowsocks',
                settings: { clients: [], method: 'chacha20-ietf-poly1305', network: 'tcp,udp' },
                sniffing: { enabled: true, destOverride: ['http', 'tls', 'quic'] },
            },
        ],
        outbounds: [
            { protocol: 'freedom', tag: 'DIRECT' },
            { protocol: 'blackhole', tag: 'BLOCK' },
        ],
        routing: { rules: [] },
    };
}

const NAME_RE = /^[A-Za-z0-9_\s-]+$/;
const NAME_HINT = 'От 2 до 30 символов: латиница, цифры, пробел, _ и -';

function nameModal({ title, icon, value = '', confirmText, onSubmit }) {
    openModal({
        title,
        icon,
        size: 'sm',
        body: `<form id="nf"><div class="field"><label>Название<span class="req">*</span></label>
                <input class="input" name="name" value="${esc(value)}" required minlength="2" maxlength="30">
                <div class="desc">${NAME_HINT}</div></div></form>`,
        foot: `<button class="btn btn-default" data-cancel>Отмена</button><button class="btn" id="ok">${esc(confirmText)}</button>`,
        onMount: (m, close) => {
            const form = $('#nf', m);
            $('[data-cancel]', m).onclick = close;
            const submit = async (e) => {
                if (e) e.preventDefault();
                if (!form.reportValidity()) return;
                const v = form.elements.name.value.trim();
                if (!NAME_RE.test(v)) { toast('error', NAME_HINT); return; }
                const btn = $('#ok', m);
                setBusy(btn, true);
                const ok = await onSubmit(v);
                setBusy(btn, false);
                if (ok) close();
            };
            form.onsubmit = submit;
            $('#ok', m).onclick = submit;
        },
    });
}

function pageProfiles(root) {
    root.innerHTML = pageHead({
        title: 'Профили',
        crumbs: ['Профили'],
        actions: `<button class="icon-btn" id="refresh" title="Обновить"><i class="ph ph-arrows-clockwise"></i></button>
                  <button class="btn" id="create"><i class="ph ph-plus"></i>Создать</button>`,
    }) + `<div id="list">${loaderHtml()}</div>`;
    const list = $('#list', root);
    let profiles = [];

    const load = async () => {
        try {
            profiles = await loadProfiles();
            draw();
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) return handleError(e);
            list.innerHTML = errorBox(e);
        }
    };

    const draw = () => {
        if (!profiles.length) {
            list.innerHTML = '<div class="card empty"><i class="ph-duotone ph-file-code"></i>Профилей пока нет</div>';
            return;
        }
        list.innerHTML = `<div class="grid">${profiles.map((p) => `
            <div class="card clickable" data-id="${esc(p.uuid)}">
                <div class="card-head">
                    <div class="card-icon"><i class="ph-duotone ph-file-code"></i></div>
                    <div style="min-width:0;flex:1">
                        <div class="card-title ellipsis">${esc(p.name)}</div>
                        <div class="xs dimmed">изменён ${esc(fmtDate(p.updatedAt))}</div>
                    </div>
                    <button class="icon-btn subtle" data-menu data-id="${esc(p.uuid)}"><i class="ph ph-dots-three-vertical"></i></button>
                </div>
                <div class="stats">
                    <div class="stat"><div class="v">${(p.inbounds || []).length}</div><div class="l">Инбаундов</div></div>
                    <div class="stat"><div class="v">${(p.nodes || []).length}</div><div class="l">Нод</div></div>
                </div>
                ${(p.nodes || []).length ? `<div class="row wrap" style="margin-top:10px;gap:6px">${p.nodes.slice(0, 6).map((n) => `<span class="badge gray lower">${flag(n.countryCode)} ${esc(n.name)}</span>`).join('')}${p.nodes.length > 6 ? `<span class="badge gray lower">+${p.nodes.length - 6}</span>` : ''}</div>` : ''}
            </div>`).join('')}</div>`;

        $$('.card[data-id]', list).forEach((c) => c.addEventListener('click', () => { location.hash = `#/profile/${c.dataset.id}`; }));
        bindRowMenu(list, '[data-menu]', (id) => {
            const p = profiles.find((x) => x.uuid === id);
            return [
                { label: p.name },
                { text: 'Открыть редактор', icon: 'ph-code', onClick: () => { location.hash = `#/profile/${id}`; } },
                {
                    text: 'Переименовать', icon: 'ph-pencil-simple', onClick: () => nameModal({
                        title: 'Переименовать профиль', icon: 'ph-file-code', value: p.name, confirmText: 'Сохранить',
                        onSubmit: async (name) => {
                            const r = await run(() => api('PATCH', '/api/config-profiles', { uuid: id, name }), { success: 'Профиль переименован' });
                            if (r !== undefined) load();
                            return r !== undefined;
                        },
                    }),
                },
                { text: 'Копировать UUID', icon: 'ph-copy', onClick: () => copyText(id) },
                { divider: true },
                {
                    text: 'Удалить', icon: 'ph-trash', red: true, onClick: async () => {
                        const nodesNote = (p.nodes || []).length ? `<br><br>Профиль используется на ${p.nodes.length} ${plural(p.nodes.length, 'ноде', 'нодах', 'нодах')}.` : '';
                        if (await confirmDialog({ title: 'Удалить профиль?', text: `Профиль <b>${esc(p.name)}</b> будет удалён. Это действие необратимо.${nodesNote}`, confirmText: 'Удалить', danger: true })) {
                            run(() => api('DELETE', `/api/config-profiles/${id}`), { success: 'Профиль удалён' }).then(load);
                        }
                    },
                },
            ];
        });
    };

    $('#refresh', root).onclick = load;
    $('#create', root).onclick = () => nameModal({
        title: 'Новый профиль', icon: 'ph-file-code', confirmText: 'Создать',
        onSubmit: async (name) => {
            const r = await run(() => api('POST', '/api/config-profiles', { name, config: defaultProfileConfig() }), { success: 'Профиль создан' });
            if (r && r.uuid) location.hash = `#/profile/${r.uuid}`;
            else if (r !== undefined) load();
            return r !== undefined;
        },
    });
    load();
}

let monacoPromise = null;
function loadMonaco() {
    if (window.monaco) return Promise.resolve(window.monaco);
    if (monacoPromise) return monacoPromise;
    monacoPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = `${MONACO_BASE}/vs/loader.js`;
        s.onload = () => {
            window.require.config({ paths: { vs: `${MONACO_BASE}/vs` } });
            // воркеры с CDN — через blob-прокси, иначе браузер не даст их создать с чужого домена
            window.MonacoEnvironment = {
                getWorkerUrl: () => URL.createObjectURL(new Blob([
                    `self.MonacoEnvironment={baseUrl:'${MONACO_BASE}/'};importScripts('${MONACO_BASE}/vs/base/worker/workerMain.js');`,
                ], { type: 'text/javascript' })),
            };
            window.require(['vs/editor/editor.main'], () => {
                window.monaco.editor.defineTheme('rw-dark', {
                    base: 'vs-dark',
                    inherit: true,
                    rules: [],
                    colors: { 'editor.background': '#0d1117', 'editor.lineHighlightBackground': '#161b22', 'editorGutter.background': '#0d1117' },
                });
                resolve(window.monaco);
            }, reject);
        };
        s.onerror = reject;
        document.head.appendChild(s);
    });
    monacoPromise.catch(() => { monacoPromise = null; });
    return monacoPromise;
}

async function pageProfileEditor(root, uuid, page) {
    root.innerHTML = loaderHtml();
    let profile;
    try {
        profile = await api('GET', `/api/config-profiles/${uuid}`);
    } catch (e) {
        if (e instanceof ApiError && e.status === 401) return handleError(e);
        root.innerHTML = pageHead({ title: 'Профиль', crumbs: [{ text: 'Профили', href: '#/profiles' }] }) + errorBox(e);
        return;
    }
    if (page !== currentPage) return; // пользователь уже ушёл со страницы

    root.innerHTML = pageHead({
        title: `<i class="ph-duotone ph-file-code" style="color:var(--cyan-4)"></i><span id="pname">${esc(profile.name)}</span><span class="badge yellow hidden" id="dirty">не сохранено</span>`,
        crumbs: [{ text: 'Профили', href: '#/profiles' }, profile.name],
        actions: `<button class="btn btn-default" id="fmt"><i class="ph ph-brackets-curly"></i>Форматировать</button>
                  <button class="btn" id="save"><i class="ph ph-floppy-disk"></i>Сохранить</button>`,
    }) + `
        <div class="editor-layout">
            <div class="editor-box" id="editor"><textarea spellcheck="false" id="fallback"></textarea></div>
            <div class="editor-side" id="side"></div>
        </div>`;

    const initial = JSON.stringify(profile.config, null, 2);
    const fallback = $('#fallback', root);
    fallback.value = initial;
    let editor = null;
    const getValue = () => (editor ? editor.getValue() : fallback.value);
    const setValue = (v) => { if (editor) editor.setValue(v); else fallback.value = v; };
    let saved = initial;
    const markDirty = () => {
        page.dirty = getValue() !== saved;
        $('#dirty', root).classList.toggle('hidden', !page.dirty);
    };
    fallback.addEventListener('input', markDirty);

    const drawSide = (p) => {
        $('#side', root).innerHTML = `
            <div class="card stack">
                <div class="row between"><span class="fw6">Инбаунды</span><span class="badge">${(p.inbounds || []).length}</span></div>
                ${(p.inbounds || []).map((i) => `
                    <div class="inbound-chip">
                        <div style="min-width:0;flex:1"><div class="tag ellipsis">${esc(i.tag)}</div>
                        <div class="xs dimmed">${esc(i.type)}${i.network ? ' · ' + esc(i.network) : ''}${i.security ? ' · ' + esc(i.security) : ''}</div></div>
                        ${i.port ? `<span class="badge lower">${esc(i.port)}</span>` : ''}
                    </div>`).join('') || '<div class="sm dimmed">Нет инбаундов</div>'}
            </div>
            <div class="card stack">
                <div class="row between"><span class="fw6">Ноды с этим профилем</span><span class="badge gray">${(p.nodes || []).length}</span></div>
                ${(p.nodes || []).map((n) => `<div class="row sm"><span class="flag">${flag(n.countryCode)}</span><span class="ellipsis">${esc(n.name)}</span></div>`).join('') || '<div class="sm dimmed">Не используется</div>'}
            </div>
            <div class="alert info"><i class="ph ph-info"></i><div class="xs">После сохранения конфиг применится на всех нодах этого профиля. <b>Ctrl+S</b> — сохранить.</div></div>`;
    };
    drawSide(profile);

    const format = () => {
        try {
            setValue(JSON.stringify(JSON.parse(getValue()), null, 2));
            markDirty();
        } catch (e) {
            toast('error', 'Некорректный JSON', e.message);
        }
    };

    const save = async () => {
        let config;
        try {
            config = JSON.parse(getValue());
        } catch (e) {
            toast('error', 'Некорректный JSON', e.message);
            return;
        }
        const btn = $('#save', root);
        setBusy(btn, true);
        const r = await run(() => api('PATCH', '/api/config-profiles', { uuid, config }), { success: 'Профиль сохранён' });
        setBusy(btn, false);
        if (r !== undefined) {
            saved = getValue();
            markDirty();
            if (r && r.inbounds) drawSide({ ...profile, ...r });
        }
    };

    $('#fmt', root).onclick = format;
    $('#save', root).onclick = save;
    const onKey = (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
    };
    document.addEventListener('keydown', onKey);
    page.destroy = () => {
        document.removeEventListener('keydown', onKey);
        if (editor) editor.dispose();
    };

    try {
        const monaco = await loadMonaco();
        if (page !== currentPage) return;
        const box = $('#editor', root);
        const current = fallback.value;
        box.innerHTML = '';
        editor = monaco.editor.create(box, {
            value: current,
            language: 'json',
            theme: 'rw-dark',
            automaticLayout: true,
            minimap: { enabled: window.innerWidth > 1000 },
            fontFamily: 'Fira Mono, monospace',
            fontSize: 13,
            tabSize: 2,
            scrollBeyondLastLine: false,
            formatOnPaste: true,
            wordWrap: 'on',
        });
        editor.onDidChangeModelContent(markDirty);
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, save);
    } catch {
        // Monaco не загрузился — остаётся обычное текстовое поле
    }
}

/* =========================================================================
 * Внутренние сквады
 * ========================================================================= */

function inboundPickerHtml(profiles, selected) {
    if (!profiles.length) return '<div class="alert warn"><i class="ph ph-warning"></i><div class="sm">Нет профилей с инбаундами.</div></div>';
    return profiles.map((p) => `
        <div class="profile-group">
            <div class="profile-group-head">
                <span class="row" style="gap:8px"><i class="ph-duotone ph-file-code" style="color:var(--cyan-4)"></i>${esc(p.name)}</span>
                ${(p.inbounds || []).length ? `<button type="button" class="btn btn-subtle btn-sm" data-group-all="${esc(p.uuid)}">Все</button>` : ''}
            </div>
            <div class="profile-group-body" data-group="${esc(p.uuid)}">
                ${(p.inbounds || []).map((i) => `
                    <label class="check"><input type="checkbox" value="${esc(i.uuid)}" ${selected.has(i.uuid) ? 'checked' : ''}>
                        <span class="mono sm fw6">${esc(i.tag)}</span><span class="spacer"></span>
                        <span class="badge gray lower">${esc(i.type)}</span>${i.port ? `<span class="badge lower">${esc(i.port)}</span>` : ''}
                    </label>`).join('') || '<div class="sm dimmed" style="padding:6px 10px">Нет инбаундов</div>'}
            </div>
        </div>`).join('');
}

function bindInboundPicker(m) {
    $$('[data-group-all]', m).forEach((b) => {
        b.onclick = () => {
            const boxes = $$(`[data-group="${b.dataset.groupAll}"] input`, m);
            const all = boxes.every((x) => x.checked);
            boxes.forEach((x) => { x.checked = !all; });
        };
    });
}

function pageSquads(root) {
    root.innerHTML = pageHead({
        title: 'Внутренние сквады',
        crumbs: ['Внутренние сквады'],
        actions: `<button class="icon-btn" id="refresh" title="Обновить"><i class="ph ph-arrows-clockwise"></i></button>
                  <button class="btn" id="create"><i class="ph ph-plus"></i>Создать</button>`,
    }) + `<div id="list">${loaderHtml()}</div>`;
    const list = $('#list', root);
    let squads = [];

    const load = async () => {
        try {
            const r = await api('GET', '/api/internal-squads');
            squads = r.internalSquads || [];
            draw();
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) return handleError(e);
            list.innerHTML = errorBox(e);
        }
    };

    const draw = () => {
        if (!squads.length) {
            list.innerHTML = '<div class="card empty"><i class="ph-duotone ph-circles-three-plus"></i>Сквадов пока нет</div>';
            return;
        }
        list.innerHTML = `<div class="grid">${squads.map((s) => `
            <div class="card clickable" data-id="${esc(s.uuid)}">
                <div class="card-head">
                    <div class="card-icon violet"><i class="ph-duotone ph-circles-three-plus"></i></div>
                    <div style="min-width:0;flex:1">
                        <div class="card-title ellipsis">${esc(s.name)}</div>
                        <div class="xs dimmed">создан ${esc(fmtDate(s.createdAt))}</div>
                    </div>
                    <button class="icon-btn subtle" data-menu data-id="${esc(s.uuid)}"><i class="ph ph-dots-three-vertical"></i></button>
                </div>
                <div class="stats">
                    <div class="stat"><div class="v">${esc(s.info ? s.info.membersCount : 0)}</div><div class="l">Участников</div></div>
                    <div class="stat"><div class="v">${esc(s.info ? s.info.inboundsCount : (s.inbounds || []).length)}</div><div class="l">Инбаундов</div></div>
                </div>
                ${(s.inbounds || []).length ? `<div class="row wrap" style="margin-top:10px;gap:6px">${s.inbounds.slice(0, 6).map((i) => `<span class="badge gray lower mono">${esc(i.tag)}</span>`).join('')}${s.inbounds.length > 6 ? `<span class="badge gray lower">+${s.inbounds.length - 6}</span>` : ''}</div>` : ''}
            </div>`).join('')}</div>`;

        $$('.card[data-id]', list).forEach((c) => c.addEventListener('click', () => {
            squadModal(squads.find((x) => x.uuid === c.dataset.id), load);
        }));
        bindRowMenu(list, '[data-menu]', (id) => {
            const s = squads.find((x) => x.uuid === id);
            return [
                { label: s.name },
                { text: 'Редактировать', icon: 'ph-pencil-simple', onClick: () => squadModal(s, load) },
                {
                    text: 'Добавить всех пользователей', icon: 'ph-user-plus', onClick: async () => {
                        if (await confirmDialog({ title: 'Добавить всех пользователей?', text: `Все пользователи панели будут добавлены в сквад <b>${esc(s.name)}</b>.`, confirmText: 'Добавить' })) {
                            run(() => api('POST', `/api/internal-squads/${id}/bulk-actions/add-users`), { success: 'Задача запущена: пользователи добавляются' }).then(load);
                        }
                    },
                },
                {
                    text: 'Убрать всех пользователей', icon: 'ph-user-minus', onClick: async () => {
                        if (await confirmDialog({ title: 'Убрать всех пользователей?', text: `Все пользователи будут исключены из сквада <b>${esc(s.name)}</b> и потеряют доступ к его инбаундам.`, confirmText: 'Убрать', danger: true })) {
                            run(() => api('DELETE', `/api/internal-squads/${id}/bulk-actions/remove-users`), { success: 'Задача запущена: пользователи убираются' }).then(load);
                        }
                    },
                },
                { text: 'Копировать UUID', icon: 'ph-copy', onClick: () => copyText(id) },
                { divider: true },
                {
                    text: 'Удалить', icon: 'ph-trash', red: true, onClick: async () => {
                        if (await confirmDialog({ title: 'Удалить сквад?', text: `Сквад <b>${esc(s.name)}</b> будет удалён, его участники потеряют доступ к его инбаундам.`, confirmText: 'Удалить', danger: true })) {
                            run(() => api('DELETE', `/api/internal-squads/${id}`), { success: 'Сквад удалён' }).then(load);
                        }
                    },
                },
            ];
        });
    };

    $('#refresh', root).onclick = load;
    $('#create', root).onclick = () => squadModal(null, load);
    load();
}

async function squadModal(squad, onDone) {
    const isNew = !squad;
    let profiles;
    try {
        profiles = await loadProfiles();
    } catch (e) {
        handleError(e);
        return;
    }
    const selected = new Set(((squad && squad.inbounds) || []).map((i) => i.uuid));

    openModal({
        title: isNew ? 'Новый внутренний сквад' : squad.name,
        icon: 'ph-circles-three-plus',
        body: `
            <form id="sf" class="stack" autocomplete="off" style="gap:14px">
                <div class="field"><label>Название<span class="req">*</span></label>
                    <input class="input" name="name" value="${esc(squad ? squad.name : '')}" required minlength="2" maxlength="30">
                    <div class="desc">${NAME_HINT}</div></div>
                <div class="field"><label>Инбаунды</label>
                    <div class="desc">Пользователи сквада получат доступ к выбранным инбаундам</div></div>
                <div class="stack">${inboundPickerHtml(profiles, selected)}</div>
            </form>`,
        foot: `${!isNew ? '<button class="btn btn-red-light left" id="del"><i class="ph ph-trash"></i>Удалить</button>' : ''}
               <button class="btn btn-default" data-cancel>Отмена</button>
               <button class="btn" id="save"><i class="ph ph-floppy-disk"></i>${isNew ? 'Создать' : 'Сохранить'}</button>`,
        onMount: (m, close) => {
            const form = $('#sf', m);
            bindInboundPicker(m);
            $('[data-cancel]', m).onclick = close;
            form.onsubmit = (e) => e.preventDefault();
            if (!isNew) {
                $('#del', m).onclick = async () => {
                    if (await confirmDialog({ title: 'Удалить сквад?', text: `Сквад <b>${esc(squad.name)}</b> будет удалён.`, confirmText: 'Удалить', danger: true })) {
                        const r = await run(() => api('DELETE', `/api/internal-squads/${squad.uuid}`), { success: 'Сквад удалён' });
                        if (r !== undefined) { close(); onDone(); }
                    }
                };
            }
            $('#save', m).onclick = async () => {
                if (!form.reportValidity()) return;
                const name = form.elements.name.value.trim();
                if (!NAME_RE.test(name)) { toast('error', NAME_HINT); return; }
                const inbounds = $$('.profile-group-body input:checked', m).map((b) => b.value);
                const btn = $('#save', m);
                setBusy(btn, true);
                const r = await run(
                    () => (isNew ? api('POST', '/api/internal-squads', { name, inbounds }) : api('PATCH', '/api/internal-squads', { uuid: squad.uuid, name, inbounds })),
                    { success: isNew ? 'Сквад создан' : 'Сквад сохранён' },
                );
                setBusy(btn, false);
                if (r !== undefined) { close(); onDone(); }
            };
        },
    });
}

/* =========================================================================
 * Хосты
 * ========================================================================= */

const FINGERPRINTS = ['chrome', 'firefox', 'safari', 'ios', 'android', 'edge', '360', 'qq', 'random', 'randomized'];
const ALPNS = ['h3', 'h2', 'http/1.1', 'h2,http/1.1', 'h3,h2,http/1.1', 'h3,h2'];

function pageHosts(root) {
    root.innerHTML = pageHead({
        title: 'Хосты',
        crumbs: ['Хосты'],
        actions: `<button class="icon-btn" id="refresh" title="Обновить"><i class="ph ph-arrows-clockwise"></i></button>
                  <button class="btn" id="create"><i class="ph ph-plus"></i>Создать</button>`,
    }) + `<div id="list">${loaderHtml()}</div>`;
    const list = $('#list', root);
    let hosts = [];
    let profiles = [];

    const load = async () => {
        try {
            [hosts, profiles] = await Promise.all([
                api('GET', '/api/hosts'),
                loadProfiles().catch((e) => { if (e.status === 401) throw e; return []; }),
            ]);
            draw();
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) return handleError(e);
            list.innerHTML = errorBox(e);
        }
    };

    const inboundLabel = (h) => {
        const p = profiles.find((x) => x.uuid === (h.inbound && h.inbound.configProfileUuid));
        const i = p && (p.inbounds || []).find((x) => x.uuid === h.inbound.configProfileInboundUuid);
        return { profile: p ? p.name : '—', tag: i ? i.tag : '—' };
    };

    const draw = () => {
        if (!hosts.length) {
            list.innerHTML = '<div class="card empty"><i class="ph-duotone ph-list-checks"></i>Хостов пока нет</div>';
            return;
        }
        list.innerHTML = `<div class="node-list">${hosts.map((h) => {
            const il = inboundLabel(h);
            return `
            <div class="card clickable host-row ${h.isDisabled ? 'off' : ''}" data-id="${esc(h.uuid)}">
                <span class="dot ${h.isDisabled ? 'disabled' : 'online'}" title="${h.isDisabled ? 'Отключён' : 'Включён'}"></span>
                <div style="min-width:0">
                    <div class="fw6 ellipsis">${esc(h.remark)}</div>
                    <div class="row" style="gap:6px;margin-top:2px">
                        ${h.isHidden ? '<span class="badge gray">скрыт</span>' : ''}
                        ${h.securityLayer && h.securityLayer !== 'DEFAULT' ? `<span class="badge violet">${esc(h.securityLayer)}</span>` : ''}
                    </div>
                </div>
                <div class="c-addr mono sm dimmed ellipsis">${esc(h.address)}:${esc(h.port)}</div>
                <div class="c-inb" style="min-width:0"><div class="mono sm fw6 ellipsis">${esc(il.tag)}</div><div class="xs dimmed ellipsis">${esc(il.profile)}</div></div>
                <button class="icon-btn subtle" data-menu data-id="${esc(h.uuid)}"><i class="ph ph-dots-three-vertical"></i></button>
            </div>`;
        }).join('')}</div>`;

        $$('.host-row', list).forEach((row) => row.addEventListener('click', () => {
            hostModal(hosts.find((x) => x.uuid === row.dataset.id), profiles, load);
        }));
        bindRowMenu(list, '[data-menu]', (id) => {
            const h = hosts.find((x) => x.uuid === id);
            return [
                { label: h.remark },
                { text: 'Редактировать', icon: 'ph-pencil-simple', onClick: () => hostModal(h, profiles, load) },
                {
                    text: h.isDisabled ? 'Включить' : 'Отключить', icon: 'ph-power',
                    onClick: () => run(() => api('PATCH', '/api/hosts', { uuid: id, isDisabled: !h.isDisabled }), { success: h.isDisabled ? 'Хост включён' : 'Хост отключён' }).then(load),
                },
                { text: 'Копировать UUID', icon: 'ph-copy', onClick: () => copyText(id) },
                { divider: true },
                {
                    text: 'Удалить', icon: 'ph-trash', red: true, onClick: async () => {
                        if (await confirmDialog({ title: 'Удалить хост?', text: `Хост <b>${esc(h.remark)}</b> пропадёт из подписок пользователей.`, confirmText: 'Удалить', danger: true })) {
                            run(() => api('DELETE', `/api/hosts/${id}`), { success: 'Хост удалён' }).then(load);
                        }
                    },
                },
            ];
        });
    };

    $('#refresh', root).onclick = load;
    $('#create', root).onclick = () => hostModal(null, profiles, load);
    load();
}

function hostModal(host, profiles, onDone) {
    const isNew = !host;
    const h = host || { port: 443, securityLayer: 'DEFAULT' };
    const curInbound = h.inbound ? `${h.inbound.configProfileUuid}|${h.inbound.configProfileInboundUuid}` : '';
    const opt = (v, cur, label) => `<option value="${esc(v)}" ${v === (cur ?? '') ? 'selected' : ''}>${esc(label ?? v)}</option>`;

    openModal({
        title: isNew ? 'Новый хост' : h.remark,
        icon: 'ph-list-checks',
        size: 'lg',
        body: `
            <form class="fgrid" id="hf" autocomplete="off">
                <div class="field"><label>Название (remark)<span class="req">*</span></label>
                    <input class="input" name="remark" value="${esc(h.remark)}" required maxlength="100" placeholder="🇩🇪 Germany"></div>
                <div class="field"><label>Инбаунд<span class="req">*</span></label>
                    <select class="select" name="inbound" required>
                        <option value="">— выберите —</option>
                        ${profiles.map((p) => `<optgroup label="${esc(p.name)}">${(p.inbounds || []).map((i) => opt(`${p.uuid}|${i.uuid}`, curInbound, `${i.tag} (${i.type}${i.port ? ', ' + i.port : ''})`)).join('')}</optgroup>`).join('')}
                    </select></div>
                <div class="field"><label>Адрес<span class="req">*</span></label>
                    <input class="input mono" name="address" value="${esc(h.address)}" required placeholder="node.example.com"></div>
                <div class="field"><label>Порт<span class="req">*</span></label>
                    <input class="input mono" name="port" type="number" min="1" max="65535" value="${esc(h.port)}" required></div>

                <div class="fieldset full">
                    <div class="fieldset-legend"><i class="ph ph-shield-check"></i>Транспорт и безопасность</div>
                    <div class="fgrid">
                        <div class="field"><label>SNI</label><input class="input mono" name="sni" value="${esc(h.sni)}"></div>
                        <div class="field"><label>Host</label><input class="input mono" name="host" value="${esc(h.host)}"></div>
                        <div class="field"><label>Path</label><input class="input mono" name="path" value="${esc(h.path)}"></div>
                        <div class="field"><label>Security layer</label>
                            <select class="select" name="securityLayer">${['DEFAULT', 'TLS', 'NONE'].map((v) => opt(v, h.securityLayer)).join('')}</select></div>
                        <div class="field"><label>ALPN</label>
                            <select class="select" name="alpn">${opt('', h.alpn, 'по умолчанию')}${ALPNS.map((v) => opt(v, h.alpn)).join('')}</select></div>
                        <div class="field"><label>Fingerprint</label>
                            <select class="select" name="fingerprint">${opt('', h.fingerprint, 'по умолчанию')}${FINGERPRINTS.map((v) => opt(v, h.fingerprint)).join('')}</select></div>
                    </div>
                </div>

                <div class="field full"><label>Описание сервера</label>
                    <input class="input" name="serverDescription" maxlength="30" value="${esc(h.serverDescription)}" placeholder="до 30 символов"></div>
                <div class="row wrap full" style="gap:24px">
                    <label class="switch"><input type="checkbox" name="isDisabled" ${h.isDisabled ? 'checked' : ''}><span class="track"></span><span>Отключён</span></label>
                    <label class="switch"><input type="checkbox" name="isHidden" ${h.isHidden ? 'checked' : ''}><span class="track"></span><span>Скрыт из подписки</span></label>
                </div>
            </form>`,
        foot: `${!isNew ? '<button class="btn btn-red-light left" id="del"><i class="ph ph-trash"></i>Удалить</button>' : ''}
               <button class="btn btn-default" data-cancel>Отмена</button>
               <button class="btn" id="save"><i class="ph ph-floppy-disk"></i>${isNew ? 'Создать' : 'Сохранить'}</button>`,
        onMount: (m, close) => {
            const form = $('#hf', m);
            const f = (n) => form.elements[n];
            $('[data-cancel]', m).onclick = close;
            if (!isNew) {
                $('#del', m).onclick = async () => {
                    if (await confirmDialog({ title: 'Удалить хост?', text: `Хост <b>${esc(h.remark)}</b> будет удалён.`, confirmText: 'Удалить', danger: true })) {
                        const r = await run(() => api('DELETE', `/api/hosts/${h.uuid}`), { success: 'Хост удалён' });
                        if (r !== undefined) { close(); onDone(); }
                    }
                };
            }
            $('#save', m).onclick = async () => {
                if (!form.reportValidity()) return;
                const [configProfileUuid, configProfileInboundUuid] = f('inbound').value.split('|');
                const orNull = (n) => f(n).value.trim() || null;
                const payload = {
                    remark: f('remark').value.trim(),
                    address: f('address').value.trim(),
                    port: Number(f('port').value),
                    inbound: { configProfileUuid, configProfileInboundUuid },
                    sni: orNull('sni'),
                    host: orNull('host'),
                    path: orNull('path'),
                    securityLayer: f('securityLayer').value,
                    alpn: orNull('alpn'),
                    fingerprint: orNull('fingerprint'),
                    serverDescription: orNull('serverDescription'),
                    isDisabled: f('isDisabled').checked,
                    isHidden: f('isHidden').checked,
                };
                const btn = $('#save', m);
                setBusy(btn, true);
                const r = await run(
                    () => (isNew ? api('POST', '/api/hosts', payload) : api('PATCH', '/api/hosts', { uuid: h.uuid, ...payload })),
                    { success: isNew ? 'Хост создан' : 'Хост сохранён' },
                );
                setBusy(btn, false);
                if (r !== undefined) { close(); onDone(); }
            };
        },
    });
}

/* ========================================================================= */

render();

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

// Как xbytes в панели: 85.86 TiB, 215.70 GiB, 0 B
function fmtIec(b) {
    const v0 = Number(b) || 0;
    if (v0 < 1024) return `${Math.round(v0)} B`;
    const units = ['KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
    let v = v0 / 1024;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(2)} ${units[i]}`;
}

// Скорость в битах, SI: 1.00 Gb/s, 231.74 Mb/s
function fmtBitsPerSec(bytesPerSec) {
    if (!bytesPerSec) return '0 b/s';
    let v = Number(bytesPerSec) * 8;
    const units = ['b', 'Kb', 'Mb', 'Gb', 'Tb'];
    let i = 0;
    while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return `${i === 0 ? Math.round(v) : v.toFixed(2)} ${units[i]}/s`;
}

// Аптайм xray коротко: 2d / 14h / 5m / 30s
function fmtUptimeShort(sec) {
    const s = Number(sec) || 0;
    if (s >= 86400) return `${Math.round(s / 86400)}d`;
    if (s >= 3600) return `${Math.round(s / 3600)}h`;
    if (s >= 60) return `${Math.round(s / 60)}m`;
    return `${Math.round(s)}s`;
}

// Сколько дней до сброса трафика (день месяца targetDay)
function daysUntilReset(targetDay) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const at = (y, m) => {
        const last = new Date(y, m + 1, 0).getDate();
        return new Date(y, m, Math.min(targetDay || 1, last));
    };
    let target = at(today.getFullYear(), today.getMonth());
    if (target < today) target = at(today.getFullYear(), today.getMonth() + 1);
    return Math.round((target - today) / 86400000);
}

// Стабильный цвет по строке (как color-hash в панели)
function hashColor(str) {
    let h = 0;
    for (const ch of String(str)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return `hsl(${h % 360}, 65%, 65%)`;
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
        const [p, qs] = path.split('?'); // query-параметры идут отдельно от пути
        res = await fetch(`${API_URL}?p=${encodeURIComponent(p)}${qs ? `&${qs}` : ''}`, {
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
        if (data && data.success === false && Array.isArray(data.errors) && data.errors.length) {
            msg = data.errors.map((e) => e.message).join('; ');
        } else if (data && Array.isArray(data.errors) && data.errors.length) {
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

let profilesCache = null; // { at, data }
async function loadProfiles({ fresh = false } = {}) {
    if (!fresh && profilesCache && Date.now() - profilesCache.at < 30000) return profilesCache.data;
    const r = await api('GET', '/api/config-profiles');
    profilesCache = { at: Date.now(), data: r.configProfiles || [] };
    return profilesCache.data;
}
function invalidateProfiles() {
    profilesCache = null;
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
        row.className = 'menu-item' + (it.red ? ' red' : '') + (it.green ? ' green' : '');
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
function openModal({ title, titleHtml, icon, size = '', body = '', foot = '', onMount, onClose }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal ${size}" role="dialog" aria-modal="true">
            <div class="modal-head">
                <div class="modal-title">${icon ? `<i class="ph-duotone ${icon}" style="color:var(--cyan-4);font-size:20px"></i>` : ''}${titleHtml ?? esc(title)}</div>
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
    close.modal = modal;
    if (onMount) onMount(modal, close);
    focusFirst(modal);
    return close;
}

function focusFirst(modal) {
    // на телефоне не открываем клавиатуру сами; выпадающий список стран не раскрываем
    if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) return;
    const first = $('.modal-body input:not([type=checkbox]):not([type=hidden]):not(.combo-input):not([readonly]), .modal-body select, .modal-body textarea', modal);
    if (first) setTimeout(() => first.focus(), 30);
}

// Модалка открывается сразу с индикатором загрузки, содержимое появляется после load()
function openModalAsync({ title, titleHtml, icon, size, load, render: renderContent }) {
    let closed = false;
    const close = openModal({ title, titleHtml, icon, size, body: loaderHtml(), onClose: () => { closed = true; } });
    const modal = close.modal;
    (async () => {
        let data;
        try {
            data = await load();
        } catch (e) {
            if (!closed) close();
            handleError(e);
            return;
        }
        if (closed) return;
        const { body, foot, onMount } = renderContent(data);
        $('.modal-body', modal).innerHTML = body;
        if (foot) modal.insertAdjacentHTML('beforeend', `<div class="modal-foot">${foot}</div>`);
        if (onMount) onMount(modal, close);
        focusFirst(modal);
    })();
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
    { id: 'nodes', text: 'Ноды', icon: 'ph-cpu' },
    { id: 'hosts', text: 'Хосты', icon: 'ph-list-checks' },
    { id: 'profiles', text: 'Профили', icon: 'ph-file-code' },
    { id: 'squads', text: 'Внутренние сквады', icon: 'ph-circles-three-plus' },
    { id: 'domains', text: 'Домены', icon: 'ph-globe-hemisphere-west' },
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
        a.classList.toggle('active', id === route || (id === 'profiles' && route === 'profile') || (id === 'domains' && route === 'domain'));
    });

    destroyPage();
    closeMenu();
    while (modalStack.length) modalStack[modalStack.length - 1]();

    const routes = { nodes: pageNodes, profiles: pageProfiles, profile: pageProfileEditor, squads: pageSquads, hosts: pageHosts, domains: pageDomains, domain: pageZone };
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
    // порядок проверок — как в NodeStatusBadge панели
    if (n.isConnected) return { cls: 'online', icon: 'ph-pulse', text: 'Подключена' };
    if (n.isConnecting) return { cls: 'connecting', icon: 'ph-cloud-arrow-up', text: 'Подключение…' };
    if (n.isDisabled) return { cls: 'disabled', icon: 'ph-prohibit', text: 'Отключена' };
    return { cls: 'offline', icon: 'ph-warning-circle', text: n.lastStatusMessage ? `Офлайн: ${n.lastStatusMessage}` : 'Офлайн' };
}

function nodeCardTone(n) {
    if (n.isDisabled) return 'disabled';
    if (n.isConnected) return 'online';
    if (n.isConnecting) return 'connecting';
    return 'offline';
}

function nodeCardHtml(n, pluginName) {
    const st = nodeStatus(n);
    const cp = n.configProfile || {};
    const dangling = !cp.activeConfigProfileUuid || !(cp.activeInbounds || []).length;
    const online = n.usersOnline || 0;

    // трафик: без учёта — полная бирюзовая полоса и ∞, как в панели
    const tracking = n.isTrafficTrackingActive;
    const limit = n.trafficLimitBytes || 0;
    let pct = 100;
    let barCls = 'teal';
    if (tracking && limit > 0) {
        pct = Math.min(100, Math.floor(((n.trafficUsedBytes || 0) * 100) / limit));
        barCls = pct > 95 ? 'red' : pct > 80 ? 'yellow' : 'teal';
    }
    const maxText = tracking && limit ? fmtIec(limit) : '∞';

    const isOnline = n.isConnected && n.xrayUptime && !n.isDisabled;

    // нагрузка сервера
    const sys = n.system;
    let ramPct = null;
    let ramCls = 'teal';
    let loads = null;
    let cpus = 1;
    let rx = null;
    let tx = null;
    if (sys && sys.info && sys.stats) {
        if (sys.info.memoryTotal) {
            ramPct = Math.round((sys.stats.memoryUsed / sys.info.memoryTotal) * 100);
            ramCls = ramPct > 90 ? 'red' : ramPct > 70 ? 'yellow' : 'teal';
        }
        cpus = sys.info.cpus || 1;
        if (sys.stats.interface) {
            loads = sys.stats.loadAvg || null;
            rx = fmtBitsPerSec(sys.stats.interface.rxBytesPerSec);
            tx = fmtBitsPerSec(sys.stats.interface.txBytesPerSec);
        }
    }
    const loadCls = (l) => (l / cpus > 1 ? 'red' : l / cpus > 0.7 ? 'yellow' : '');
    const loadTitle = loads
        ? `Load Average (${cpus} ${plural(cpus, 'ядро', 'ядра', 'ядер')})\n` +
          ['1 мин', '5 мин', '15 мин'].map((p, i) => `${p}: ${loads[i].toFixed(2)} (${Math.round((loads[i] / cpus) * 100)}%)`).join('\n') +
          '\n0–70% норма · 70–100% высокая · >100% перегрузка'
        : 'Нет данных';

    return `
    <div class="node-card tone-${nodeCardTone(n)}" data-id="${esc(n.uuid)}">
        <div class="nc-grid">
            <div class="nc-main">
                ${dangling
                    ? '<span class="badge red filled-red nc-dangling"><i class="ph ph-warning-circle"></i>DANGLING</span>'
                    : `<span class="st-icon ${st.cls}" title="${esc(st.text)}"><i class="ph-duotone ${st.icon}"></i></span>
                       <span class="users-badge ${online > 0 ? 'active' : ''}" title="Пользователей онлайн"><i class="ph-duotone ph-users"></i>${esc(online)}</span>`}
                ${n.countryCode && n.countryCode !== 'XX' ? `<span class="flag">${flag(n.countryCode)}</span>` : ''}
                <span class="nc-name">${esc(n.name)}</span>
            </div>
            <div class="nc-addr"><i class="ph ph-globe-simple"></i><span class="nc-addr-text" data-copy="${esc(n.address)}" title="Скопировать">${esc(n.address)}</span></div>
            <div class="nc-traffic">
                <div class="row between"><span class="mono fw6 dimmed sm">${fmtIec(n.trafficUsedBytes)}</span><span class="xs dimmed">${maxText}</span></div>
                <div class="progress ${barCls}"><div style="width:${pct}%"></div></div>
            </div>
            <div class="nc-right">
                ${tracking ? `<span class="nc-meta" title="Дней до сброса трафика"><i class="ph ph-arrows-counter-clockwise"></i>${daysUntilReset(n.trafficResetDay)}</span>` : '<span></span>'}
                ${isOnline ? `<span class="nc-uptime" title="Аптайм Xray"><i class="ph-fill ph-star-four"></i>${fmtUptimeShort(n.xrayUptime)}</span>` : ''}
            </div>
        </div>
        <div class="nc-sub">
            <span class="nc-ram" title="Память"><i class="ph-duotone ph-memory"></i>
                <span class="progress xs ${ramCls}"><span style="width:${ramPct ?? 0}%"></span></span>
                <span class="mono">${ramPct !== null ? ramPct + '%' : '—'}</span></span>
            <span class="nc-stat" title="${esc(loadTitle)}"><i class="ph-duotone ph-cpu"></i>
                ${loads ? loads.slice(0, 3).map((l) => `<span class="${loadCls(l)}">${l.toFixed(2)}</span>`).join(' ') : '—'}</span>
            <span class="nc-stat" title="Входящий трафик"><i class="ph-duotone ph-arrow-down" style="color:var(--teal-5)"></i>${rx ?? '—'}</span>
            <span class="nc-stat" title="Исходящий трафик"><i class="ph-duotone ph-arrow-up" style="color:var(--cyan-6)"></i>${tx ?? '—'}</span>
            <span class="spacer"></span>
            ${pluginName ? `<span class="nc-stat nc-plugin" title="Плагин"><i class="ph-duotone ph-package"></i>${esc(pluginName)}</span>` : ''}
            <span class="nc-stat" title="Версия Xray"><i class="ph-fill ph-star-four"></i>${n.versions && n.versions.xray ? esc(n.versions.xray) : '—'}</span>
            <span class="nc-stat" title="Версия Remnawave Node"><i class="ph ph-waveform"></i>${n.versions && n.versions.node ? esc(n.versions.node) : '—'}</span>
        </div>
        <div class="nc-handle" data-drag title="Перетащите, чтобы изменить порядок"><i class="ph ph-dots-six-vertical"></i></div>
    </div>`;
}

let pluginsCache = null; // { at, data } — null в data, если нет доступа
async function loadPlugins() {
    if (pluginsCache && Date.now() - pluginsCache.at < 60000) return pluginsCache.data;
    let data = null;
    try {
        const r = await api('GET', '/api/node-plugins');
        data = r.nodePlugins || [];
    } catch (e) {
        if (e instanceof ApiError && e.status === 401) throw e;
    }
    pluginsCache = { at: Date.now(), data };
    return data;
}

function pageNodes(root, _param, page) {
    root.innerHTML = pageHead({
        title: 'Ноды',
        crumbs: ['Ноды'],
        actions: `
            <button class="icon-btn" id="refresh" title="Обновить"><i class="ph ph-arrows-clockwise"></i></button>
            <button class="btn" id="create"><i class="ph ph-plus"></i>Создать</button>`,
    }) + `<div id="list">${loaderHtml()}</div>`;

    let nodes = [];
    let plugins = [];
    let dragging = false;
    const list = $('#list', root);

    const load = async (silent) => {
        try {
            [nodes, plugins] = await Promise.all([api('GET', '/api/nodes'), loadPlugins()]);
            if (!dragging) draw();
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) return handleError(e);
            if (!silent) list.innerHTML = errorBox(e);
        }
    };

    const pluginName = (n) => {
        const p = n.activePluginUuid && (plugins || []).find((x) => x.uuid === n.activePluginUuid);
        return p ? p.name : '';
    };

    const draw = () => {
        if (!nodes.length) {
            list.innerHTML = `<div class="card empty"><i class="ph-duotone ph-cpu"></i>Нод пока нет<div class="sm" style="margin-top:6px">Нажмите «Создать», чтобы добавить первую ноду</div></div>`;
            return;
        }
        list.innerHTML = `<div class="node-list">${nodes.map((n) => nodeCardHtml(n, pluginName(n))).join('')}</div>`;

        $$('.node-card', list).forEach((row) => row.addEventListener('click', () => {
            const n = nodes.find((x) => x.uuid === row.dataset.id);
            if (n) nodeModal(n, () => load(true));
        }));
        $$('[data-copy]', list).forEach((el) => el.addEventListener('click', (e) => {
            e.stopPropagation();
            copyText(el.dataset.copy);
        }));
        $$('[data-drag]', list).forEach((h) => {
            h.addEventListener('click', (e) => e.stopPropagation());
            h.addEventListener('pointerdown', (e) => startDrag(e, h));
        });
    };

    // Перетаскивание за ручку справа (мышь и тач)
    const startDrag = (e, handle) => {
        if (e.button !== undefined && e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const card = handle.closest('.node-card');
        const cards = $$('.node-card', list);
        const from = cards.indexOf(card);
        const startScroll = window.scrollY;
        const rects = cards.map((c) => {
            const r = c.getBoundingClientRect();
            return { top: r.top + startScroll, height: r.height };
        });
        const gap = 8;
        const startY = e.clientY + startScroll;
        let lastClientY = e.clientY;
        let to = from;
        dragging = true;
        card.classList.add('dragging');
        cards.forEach((c, i) => { if (i !== from) c.classList.add('shifting'); });
        try { handle.setPointerCapture(e.pointerId); } catch { /* ignore */ }

        const update = () => {
            const dy = lastClientY + window.scrollY - startY;
            card.style.transform = `translateY(${dy}px)`;
            const center = rects[from].top + rects[from].height / 2 + dy;
            to = 0;
            rects.forEach((r, i) => { if (i !== from && r.top + r.height / 2 < center) to++; });
            const shift = rects[from].height + gap;
            cards.forEach((c, i) => {
                if (i === from) return;
                let t = 0;
                if (from < to && i > from && i <= to) t = -shift;
                if (to < from && i >= to && i < from) t = shift;
                c.style.transform = t ? `translateY(${t}px)` : '';
            });
        };
        // автопрокрутка у краёв экрана
        let raf = 0;
        const autoScroll = () => {
            const edge = 70;
            let v = 0;
            if (lastClientY < edge) v = -Math.ceil((edge - lastClientY) / 6);
            else if (lastClientY > window.innerHeight - edge) v = Math.ceil((lastClientY - (window.innerHeight - edge)) / 6);
            if (v) { window.scrollBy(0, v); update(); }
            raf = requestAnimationFrame(autoScroll);
        };
        raf = requestAnimationFrame(autoScroll);

        const onMove = (ev) => { lastClientY = ev.clientY; update(); };
        const onUp = async () => {
            cancelAnimationFrame(raf);
            handle.removeEventListener('pointermove', onMove);
            handle.removeEventListener('pointerup', onUp);
            handle.removeEventListener('pointercancel', onUp);
            dragging = false;
            cards.forEach((c) => { c.style.transform = ''; c.classList.remove('dragging', 'shifting'); });
            if (to === from) return;
            const [moved] = nodes.splice(from, 1);
            nodes.splice(to, 0, moved);
            draw();
            const r = await run(() => api('POST', '/api/nodes/actions/reorder', { nodes: nodes.map((n, i) => ({ uuid: n.uuid, viewPosition: i })) }));
            if (r === undefined) load();
        };
        handle.addEventListener('pointermove', onMove);
        handle.addEventListener('pointerup', onUp);
        handle.addEventListener('pointercancel', onUp);
    };

    $('#refresh', root).onclick = () => load();
    $('#create', root).onclick = () => nodeModal(null, () => load(true));

    load();
    const timer = setInterval(() => { if (!modalStack.length && !openMenuEl && !dragging) load(true); }, 10000);
    page.destroy = () => clearInterval(timer);
}

/* ---------- выбор страны (выпадающий список с поиском, как в панели) ---------- */

let ruRegionNames = null;
try { ruRegionNames = new Intl.DisplayNames(['ru'], { type: 'region' }); } catch { /* старый браузер */ }

function countryLabel(code) {
    const c = COUNTRIES.find((x) => x[0] === code) || COUNTRIES[0];
    return `${c[0] === 'XX' ? '🏴‍☠️' : flag(c[0])} ${c[1]}`;
}

function countrySelectHtml(name, value) {
    const v = COUNTRIES.some((c) => c[0] === value) ? value : 'XX';
    return `
        <div class="combo" data-combo>
            <input type="hidden" name="${esc(name)}" value="${esc(v)}">
            <div class="input-wrap">
                <i class="ph ph-map-pin"></i>
                <input class="input combo-input" placeholder="Выберите страну" autocomplete="off" spellcheck="false" value="${esc(v === 'XX' ? '' : countryLabel(v))}">
                <i class="ph ph-caret-up-down combo-chevron"></i>
            </div>
            <div class="combo-drop hidden" role="listbox"></div>
        </div>`;
}

function bindCountrySelect(root) {
    const box = $('[data-combo]', root);
    if (!box) return;
    const hidden = $('input[type=hidden]', box);
    const input = $('.combo-input', box);
    const drop = $('.combo-drop', box);
    let items = [];
    let active = 0;

    const searchText = (c) => `${c[0]} ${c[1]} ${ruRegionNames && c[0] !== 'XX' ? ruRegionNames.of(c[0]) || '' : ''}`.toLowerCase();

    const draw = (query) => {
        const q = (query || '').trim().toLowerCase();
        items = q ? COUNTRIES.filter((c) => searchText(c).includes(q)) : COUNTRIES;
        active = Math.max(0, items.findIndex((c) => c[0] === hidden.value));
        if (q) active = 0;
        drop.innerHTML = items.length
            ? items.map((c, i) => `<div class="combo-opt ${i === active ? 'active' : ''} ${c[0] === hidden.value ? 'selected' : ''}" data-i="${i}">${esc(countryLabel(c[0]))}</div>`).join('')
            : '<div class="combo-empty">Ничего не найдено</div>';
        const act = $('.combo-opt.active', drop);
        if (act) act.scrollIntoView({ block: 'nearest' });
    };
    const open = () => {
        drop.classList.remove('hidden');
        draw('');
    };
    const close = () => {
        drop.classList.add('hidden');
        input.value = hidden.value === 'XX' ? '' : countryLabel(hidden.value);
    };
    const choose = (i) => {
        const c = items[i];
        if (!c) return;
        hidden.value = c[0];
        close();
        input.blur();
    };
    const setActive = (i) => {
        active = Math.max(0, Math.min(items.length - 1, i));
        $$('.combo-opt', drop).forEach((el, k) => el.classList.toggle('active', k === active));
        const act = $('.combo-opt.active', drop);
        if (act) act.scrollIntoView({ block: 'nearest' });
    };

    input.addEventListener('focus', () => { input.select(); open(); });
    input.addEventListener('click', () => { if (drop.classList.contains('hidden')) open(); });
    input.addEventListener('input', () => { drop.classList.remove('hidden'); draw(input.value); });
    input.addEventListener('blur', () => setTimeout(close, 120));
    input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); if (drop.classList.contains('hidden')) open(); else setActive(active + 1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
        else if (e.key === 'Enter') { e.preventDefault(); choose(active); }
        else if (e.key === 'Escape' && !drop.classList.contains('hidden')) { e.stopPropagation(); close(); input.blur(); }
    });
    drop.addEventListener('mousedown', (e) => {
        e.preventDefault(); // не терять фокус до выбора
        const opt = e.target.closest('.combo-opt');
        if (opt) choose(Number(opt.dataset.i));
    });
    $('.combo-chevron', box).addEventListener('mousedown', (e) => {
        e.preventDefault();
        if (drop.classList.contains('hidden')) input.focus(); else { close(); input.blur(); }
    });
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

// Байты в SI для итоговых счётчиков интерфейса: 211.87 TB
function fmtSi(b) {
    let v = Number(b) || 0;
    if (v < 1000) return `${Math.round(v)} B`;
    const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
    let i = -1;
    while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return `${v.toFixed(2)} ${units[i]}`;
}

function fmtDuration(sec) {
    const s = Math.floor(Number(sec) || 0);
    return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h ${Math.floor((s % 3600) / 60)}m ${s % 60}s`;
}

function nodeModal(node, onDone) {
    const isNew = !node;
    openModalAsync({
        title: isNew ? 'Новая нода' : node.name,
        titleHtml: isNew
            ? 'Новая нода'
            : `${node.countryCode && node.countryCode !== 'XX' ? `<span class="flag">${flag(node.countryCode)}</span>` : ''}<span>${esc(node.name)}</span>
               <button type="button" class="icon-btn subtle nm-link" data-copy-uuid="${esc(node.uuid)}" title="Скопировать UUID"><i class="ph ph-link"></i></button>`,
        icon: 'ph-cpu',
        size: 'xl',
        load: () => Promise.all([
            loadProfiles(),
            api('GET', '/api/keygen').catch((e) => { if (e.status === 401) throw e; return null; }),
            loadPlugins(),
            isNew ? Promise.resolve(null) : api('GET', `/api/nodes/${node.uuid}`).catch((e) => { if (e.status === 401) throw e; return node; }),
        ]),
        render: ([profiles, k, plugins, fresh]) => nodeModalContent(fresh || node, profiles, k && k.secretKey ? k.secretKey : '', plugins, onDone),
    });
}

function nodeModalContent(node, profiles, secret, plugins, onDone) {
    const isNew = !node;
    const n = node || { port: 2222, countryCode: 'XX' };
    const cp = n.configProfile || {};
    // состояние конфигурации ядра (меняется в отдельном окне)
    const core = {
        profileUuid: cp.activeConfigProfileUuid || '',
        inbounds: (cp.activeInbounds || []).map((i) => i.uuid),
    };
    const initialCore = JSON.stringify(core);

    const sys = n.system;
    const isOnline = n.isConnected && n.xrayUptime && !n.isDisabled;
    const tracking = n.isTrafficTrackingActive;
    const limit = n.trafficLimitBytes || 0;
    let pct = 100;
    let barCls = 'teal';
    if (tracking && limit > 0) {
        pct = Math.min(100, Math.floor(((n.trafficUsedBytes || 0) * 100) / limit));
        barCls = pct > 95 ? 'red' : pct > 80 ? 'yellow' : 'teal';
    }

    const detailsCard = isNew ? '' : `
        <section class="nm-card">
            <div class="nm-head">
                <span class="nm-ico teal"><i class="ph-duotone ph-wifi-high"></i></span>
                <span class="nm-title">Подробности</span>
                <span class="spacer"></span>
                ${isOnline ? `<span class="nm-pill teal" title="Аптайм Xray"><i class="ph-fill ph-star-four"></i>${fmtUptimeShort(n.xrayUptime).toUpperCase()}</span>` : ''}
                <button type="button" class="nm-tool" id="nm-json" title="Посмотреть JSON ноды">JSON</button>
                <span id="nm-power-wrap"></span>
            </div>
            <div class="nm-sep"></div>
            <div class="nm-traffic">
                <div class="row between"><span class="mono fw6">${fmtIec(n.trafficUsedBytes)}</span><span class="dimmed">${tracking && limit ? fmtIec(limit) : '∞'}</span></div>
                <div class="progress ${barCls}"><div style="width:${pct}%"></div></div>
            </div>
            <div class="nm-sep"></div>
            <div class="nm-tiles">
                <div class="nm-tile teal" title="Пользователей онлайн"><i class="ph-duotone ph-users"></i>${esc(n.usersOnline ?? 0)}</div>
                <div class="nm-tile violet" title="Версия Xray"><i class="ph-fill ph-star-four"></i>${n.versions && n.versions.xray ? esc(n.versions.xray) : '—'}</div>
                <div class="nm-tile indigo" title="Версия Remnawave Node"><i class="ph ph-waveform"></i>${n.versions && n.versions.node ? esc(n.versions.node) : '—'}</div>
            </div>
        </section>`;

    const mainCard = `
        <section class="nm-card">
            <div class="nm-head">
                <span class="nm-ico blue"><i class="ph-duotone ph-hard-drives"></i></span>
                <div style="min-width:0"><div class="nm-title">Основное</div>${!isNew ? `<div class="xs dimmed ellipsis">${esc(n.uuid)}</div>` : ''}</div>
            </div>
            <div class="nm-sep"></div>
            <form class="stack" id="node-form" autocomplete="off" style="gap:16px">
                <div class="field"><label>Страна<span class="req">*</span></label>${countrySelectHtml('countryCode', n.countryCode)}</div>
                <div class="field"><label>Внутреннее название<span class="req">*</span></label>
                    <div class="input-wrap"><i class="ph ph-user"></i>
                    <input class="input" name="name" value="${esc(n.name)}" minlength="3" maxlength="30" required placeholder="Германия-1 | Общий"></div></div>
                <div class="nm-2">
                    <div class="field"><label>Адрес<span class="req">*</span></label>
                        <div class="input-wrap"><i class="ph ph-globe"></i>
                        <input class="input" name="address" value="${esc(n.address)}" required minlength="2" placeholder="1.2.3.4"></div></div>
                    <div class="field"><label>Node Port<span class="req">*</span></label>
                        <input class="input" name="port" type="number" min="1" max="65535" required value="${esc(n.port ?? 2222)}"></div>
                </div>
                ${secret ? `
                <div class="field"><label>Secret Key (SECRET_KEY)</label>
                    <div class="input-wrap"><i class="ph ph-certificate"></i>
                    <input class="input mono" value="${esc(secret)}" readonly style="padding-right:40px">
                    <button type="button" class="icon-btn subtle right" id="copy-secret" title="Копировать"><i class="ph ph-copy"></i></button></div>
                    ${isNew ? '<button type="button" class="btn btn-default btn-block" id="copy-compose" style="margin-top:8px"><i class="ph ph-copy"></i>Скопировать docker-compose.yml</button>' : ''}
                </div>` : ''}
                ${plugins ? `
                <div class="field"><label>Плагин</label>
                    <div class="input-wrap"><i class="ph ph-package"></i>
                    <select class="select" name="plugin" style="padding-left:36px">
                        <option value="">Без плагина</option>
                        ${plugins.map((p) => `<option value="${esc(p.uuid)}" ${p.uuid === n.activePluginUuid ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}
                    </select></div></div>` : ''}
            </form>
        </section>`;

    let systemCard = '';
    if (!isNew) {
        if (sys && sys.info && sys.stats) {
            const memPct = sys.info.memoryTotal ? Math.round((sys.stats.memoryUsed / sys.info.memoryTotal) * 100) : 0;
            const memCls = memPct > 90 ? 'red' : memPct > 70 ? 'yellow' : 'teal';
            const ifc = sys.stats.interface;
            const nics = sys.info.networkInterfaces || [];
            systemCard = `
            <section class="nm-card">
                <div class="nm-head">
                    <span class="nm-ico violet"><i class="ph-duotone ph-monitor"></i></span>
                    <span class="nm-title">О системе</span>
                    <span class="spacer"></span>
                    <span class="nm-pill outline violet">${esc((sys.info.platform || '').toUpperCase())} / ${esc((sys.info.arch || '').toUpperCase())}</span>
                    <span class="nm-pill teal mono" title="Аптайм сервера"><i class="ph ph-timer"></i>${esc(fmtDuration(sys.stats.uptime).toUpperCase())}</span>
                </div>
                <div class="nm-sep"></div>
                <div class="nm-block">
                    <div class="nm-label">Память</div>
                    <div class="mono fw6">${fmtIec(sys.stats.memoryUsed)} / ${fmtIec(sys.info.memoryTotal)} <span class="dimmed">(${memPct}%)</span></div>
                    <div class="progress ${memCls}" style="margin-top:8px"><div style="width:${memPct}%"></div></div>
                </div>
                ${ifc ? `
                <div class="nm-block">
                    <div class="row between"><span class="nm-label">Интерфейс</span><span class="badge lower mono">${esc(ifc.interface)}</span></div>
                    <div class="nm-2" style="margin-top:6px">
                        <div><div class="dimmed sm"><i class="ph ph-arrow-down"></i> RX</div><div class="mono fw6">${fmtBitsPerSec(ifc.rxBytesPerSec)}</div><div class="mono xs dimmed">Всего: ${fmtSi(ifc.rxTotal)}</div></div>
                        <div><div class="dimmed sm"><i class="ph ph-arrow-up"></i> TX</div><div class="mono fw6">${fmtBitsPerSec(ifc.txBytesPerSec)}</div><div class="mono xs dimmed">Всего: ${fmtSi(ifc.txTotal)}</div></div>
                    </div>
                </div>` : ''}
                <div class="nm-block">
                    <div class="nm-label">Система</div>
                    <div class="dimmed sm" style="margin-top:4px"><i class="ph ph-cpu"></i> CPU</div>
                    <div class="mono fw6">${esc(sys.info.cpus)} x ${esc(sys.info.cpuModel)}</div>
                    <div class="nm-2" style="margin-top:6px">
                        <div><div class="dimmed sm"><i class="ph ph-linux-logo"></i> Ядро</div><div class="mono fw6 ellipsis">${esc(sys.info.release)}</div></div>
                        <div><div class="dimmed sm"><i class="ph ph-tree-structure"></i> Сеть</div><div class="mono fw6 ellipsis" title="${esc(nics.join(', '))}">${esc(nics.slice(0, 3).join(', '))}${nics.length > 3 ? ` +${nics.length - 3}` : ''}</div></div>
                    </div>
                </div>
            </section>`;
        } else {
            systemCard = `
            <section class="nm-card">
                <div class="nm-head"><span class="nm-ico violet"><i class="ph-duotone ph-monitor"></i></span><span class="nm-title">О системе</span></div>
                <div class="nm-sep"></div>
                <div class="sm dimmed">Нет данных: нода не подключена.</div>
            </section>`;
        }
    }

    const coreCard = `
        <section class="nm-card">
            <div class="nm-head">
                <span class="nm-ico green"><i class="ph-duotone ph-atom"></i></span>
                <span class="nm-title">Конфигурация ядра</span>
            </div>
            <div class="nm-sep"></div>
            <div id="nm-core"></div>
        </section>`;

    const body = `
        <div class="nm-grid">
            <div class="nm-col">${detailsCard}${mainCard}</div>
            <div class="nm-col">${systemCard}${coreCard}</div>
        </div>`;

    return {
        body,
        foot: `${!isNew ? '<button class="btn btn-default" id="more"><i class="ph ph-dots-three"></i>Ещё действия</button>' : '<button class="btn btn-default" data-cancel>Отмена</button>'}
               <button class="btn" id="save" ${isNew ? '' : 'disabled'}><i class="ph ph-floppy-disk"></i>${isNew ? 'Создать' : 'Сохранить'}</button>`,
        onMount: (m, close) => {
            const form = $('#node-form', m);
            const f = (name) => form.elements[name];
            const saveBtn = $('#save', m);
            bindCountrySelect(m);
            if ($('[data-cancel]', m)) $('[data-cancel]', m).onclick = close;
            const link = $('[data-copy-uuid]', m);
            if (link) link.onclick = () => copyText(link.dataset.copyUuid);
            if ($('#copy-secret', m)) $('#copy-secret', m).onclick = () => copyText(secret);
            if ($('#copy-compose', m)) $('#copy-compose', m).onclick = () => copyText(dockerCompose(secret, f('port').value));

            // ---- кнопка Сохранить активна только при изменениях ----
            const snapshot = () => JSON.stringify([
                f('countryCode').value, f('name').value, f('address').value, f('port').value,
                f('plugin') ? f('plugin').value : '', JSON.stringify(core),
            ]);
            const initial = snapshot();
            const markDirty = () => { if (!isNew) saveBtn.disabled = snapshot() === initial; };
            form.addEventListener('input', markDirty);
            form.addEventListener('change', markDirty);
            // скрытое поле страны меняется программно — следим через выбор в списке
            $('.combo-drop', m).addEventListener('mousedown', () => setTimeout(markDirty, 0));
            $('.combo-input', m).addEventListener('keydown', () => setTimeout(markDirty, 0));

            // ---- конфигурация ядра ----
            const drawCore = () => {
                const p = profiles.find((x) => x.uuid === core.profileUuid);
                const active = p ? (p.inbounds || []).filter((i) => core.inbounds.includes(i.uuid)) : [];
                $('#nm-core', m).innerHTML = p
                    ? `<div class="nm-block">
                        <div class="row" style="gap:12px">
                            <span class="nm-ico sm cyan"><i class="ph-fill ph-star-four"></i></span>
                            <span class="mono fw6 ellipsis" style="flex:1;font-size:13px">${esc(p.name)}</span>
                            <span class="nm-pill cyan" title="Активных инбаундов"><i class="ph ph-tag"></i>${active.length}</span>
                            <button type="button" class="icon-btn" id="core-edit" title="Изменить"><i class="ph ph-pencil-simple-line"></i></button>
                        </div>
                        <div class="row wrap" style="gap:6px;margin-top:10px">
                            ${active.length ? active.map((i) => `<span class="badge gray lower mono" title="${esc(i.tag)}">${esc(i.port || i.tag)}</span>`).join('') : '<span class="xs" style="color:var(--red-5)">Нет активных инбаундов</span>'}
                        </div>
                    </div>`
                    : `<div class="nm-block row between"><span class="sm dimmed">Профиль не выбран</span>
                        <button type="button" class="btn btn-light btn-sm" id="core-edit"><i class="ph ph-plus"></i>Выбрать</button></div>`;
                $('#core-edit', m).onclick = () => coreEditModal(profiles, core, () => { drawCore(); markDirty(); });
            };
            drawCore();

            // ---- включить/выключить ----
            const drawPower = () => {
                const wrap = $('#nm-power-wrap', m);
                if (!wrap) return;
                wrap.innerHTML = n.isDisabled
                    ? '<button type="button" class="nm-power on" id="nm-power" title="Включить ноду"><i class="ph ph-power"></i></button>'
                    : '<button type="button" class="nm-power" id="nm-power" title="Выключить ноду"><i class="ph ph-power"></i></button>';
                $('#nm-power', m).onclick = togglePower;
            };
            const togglePower = async () => {
                const action = n.isDisabled ? 'enable' : 'disable';
                if (action === 'disable' && !(await confirmDialog({ title: 'Выключить ноду?', text: `Нода <b>${esc(n.name)}</b> перестанет обслуживать пользователей.`, confirmText: 'Выключить', danger: true }))) return;
                const r = await run(() => api('POST', `/api/nodes/${n.uuid}/actions/${action}`), { success: action === 'enable' ? 'Нода включена' : 'Нода выключена' });
                if (r !== undefined) { n.isDisabled = action === 'disable'; drawPower(); onDone(); }
            };
            drawPower();

            if ($('#nm-json', m)) {
                $('#nm-json', m).onclick = () => {
                    const json = JSON.stringify(n, null, 2);
                    openModal({
                        title: 'JSON ноды', icon: 'ph-brackets-curly', size: 'lg',
                        body: `<pre class="codeblock" style="max-height:60vh;overflow:auto">${esc(json)}</pre>`,
                        foot: '<button class="btn btn-default" id="cj"><i class="ph ph-copy"></i>Копировать</button>',
                        onMount: (mm) => { $('#cj', mm).onclick = () => copyText(json); },
                    });
                };
            }

            // ---- ещё действия ----
            if (!isNew) {
                $('#more', m).onclick = (e) => {
                    e.stopPropagation();
                    openMenu($('#more', m), [
                        {
                            text: 'Удалить', icon: 'ph-trash', red: true, onClick: async () => {
                                if (await confirmDialog({ title: 'Удалить ноду?', text: `Нода <b>${esc(n.name)}</b> будет удалена. Это действие необратимо.`, confirmText: 'Удалить', danger: true })) {
                                    const r = await run(() => api('DELETE', `/api/nodes/${n.uuid}`), { success: 'Нода удалена' });
                                    if (r !== undefined) { close(); onDone(); }
                                }
                            },
                        },
                        { divider: true },
                        { label: 'Управление' },
                        { text: 'Копировать UUID', icon: 'ph-copy', onClick: () => copyText(n.uuid) },
                        { text: 'Перезапустить', icon: 'ph-arrow-clockwise', green: true, onClick: () => run(() => api('POST', `/api/nodes/${n.uuid}/actions/restart`, { forceRestart: false }), { success: 'Перезапуск отправлен' }) },
                        n.isDisabled
                            ? { text: 'Включить', icon: 'ph-power', green: true, onClick: togglePower }
                            : { text: 'Выключить', icon: 'ph-plugs', red: true, onClick: togglePower },
                    ]);
                };
            }

            // ---- сохранение ----
            saveBtn.onclick = async () => {
                if (!form.reportValidity()) return;
                if (!core.profileUuid) { toast('error', 'Выберите профиль конфигурации', 'Блок «Конфигурация ядра» → «Выбрать»'); return; }
                const payload = {
                    name: f('name').value.trim(),
                    address: f('address').value.trim(),
                    port: Number(f('port').value),
                    countryCode: f('countryCode').value || 'XX',
                    configProfile: { activeConfigProfileUuid: core.profileUuid, activeInbounds: core.inbounds },
                };
                if (f('plugin')) {
                    const v = f('plugin').value || null;
                    if (!isNew || v) payload.activePluginUuid = v;
                }
                setBusy(saveBtn, true);
                const r = await run(
                    () => (isNew ? api('POST', '/api/nodes', payload) : api('PATCH', '/api/nodes', { uuid: n.uuid, ...payload })),
                    { success: isNew ? 'Нода создана' : 'Нода сохранена' },
                );
                setBusy(saveBtn, false);
                if (r !== undefined) { close(); onDone(); }
                else markDirty();
            };
        },
    };
}

// Выбор профиля и активных инбаундов ноды
function coreEditModal(profiles, core, onApply) {
    openModal({
        title: 'Конфигурация ядра',
        icon: 'ph-atom',
        body: profiles.length ? `
            <div class="field"><label>Профиль конфигурации</label>
                <select class="select" id="ce-profile">${profiles.map((p) => `<option value="${esc(p.uuid)}" ${p.uuid === core.profileUuid ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></div>
            <div class="row between"><span class="sm dimmed">Активные инбаунды</span><button type="button" class="btn btn-subtle btn-sm" id="ce-all">Выбрать все</button></div>
            <div id="ce-list" class="stack" style="gap:2px"></div>`
            : '<div class="alert warn"><i class="ph ph-warning"></i><div class="sm">Нет профилей. Сначала создайте профиль конфигурации.</div></div>',
        foot: `<button class="btn btn-default" data-cancel>Отмена</button>${profiles.length ? '<button class="btn" id="ce-ok"><i class="ph ph-check"></i>Применить</button>' : ''}`,
        onMount: (m, close) => {
            $('[data-cancel]', m).onclick = close;
            if (!profiles.length) return;
            const sel = $('#ce-profile', m);
            if (!core.profileUuid) sel.value = profiles[0].uuid;
            const draw = () => {
                const p = profiles.find((x) => x.uuid === sel.value);
                const keep = p.uuid === core.profileUuid;
                $('#ce-list', m).innerHTML = (p.inbounds || []).map((i) => `
                    <label class="check"><input type="checkbox" value="${esc(i.uuid)}" ${keep ? (core.inbounds.includes(i.uuid) ? 'checked' : '') : 'checked'}>
                        <span class="mono sm fw6">${esc(i.tag)}</span><span class="spacer"></span>
                        <span class="badge gray lower">${esc(i.type)}</span>${i.port ? `<span class="badge lower">${esc(i.port)}</span>` : ''}</label>`).join('')
                    || '<div class="sm dimmed" style="padding:6px 10px">В профиле нет инбаундов</div>';
            };
            draw();
            sel.onchange = draw;
            $('#ce-all', m).onclick = () => {
                const boxes = $$('#ce-list input', m);
                const all = boxes.every((b) => b.checked);
                boxes.forEach((b) => { b.checked = !all; });
            };
            $('#ce-ok', m).onclick = () => {
                core.profileUuid = sel.value;
                core.inbounds = $$('#ce-list input:checked', m).map((b) => b.value);
                close();
                onApply();
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
            profiles = await loadProfiles({ fresh: true });
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
                            invalidateProfiles();
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
                            run(() => api('DELETE', `/api/config-profiles/${id}`), { success: 'Профиль удалён' }).then(() => { invalidateProfiles(); load(); });
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
            invalidateProfiles();
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
            invalidateProfiles();
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

function squadModal(squad, onDone) {
    openModalAsync({
        title: squad ? squad.name : 'Новый внутренний сквад',
        icon: 'ph-circles-three-plus',
        load: () => loadProfiles(),
        render: (profiles) => squadModalContent(squad, profiles, onDone),
    });
}

function squadModalContent(squad, profiles, onDone) {
    const isNew = !squad;
    const selected = new Set(((squad && squad.inbounds) || []).map((i) => i.uuid));

    return {
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
    };
}

/* =========================================================================
 * Хосты
 * ========================================================================= */

const FINGERPRINTS = ['chrome', 'firefox', 'safari', 'ios', 'android', 'edge', '360', 'qq', 'random', 'randomized'];
const ALPNS = ['h3', 'h2', 'http/1.1', 'h2,http/1.1', 'h3,h2,http/1.1', 'h3,h2'];

function hostStatus(h) {
    if (h.isDisabled) return { cls: 'disabled', icon: 'ph-prohibit', text: 'Отключён' };
    if (h.isHidden) return { cls: 'hidden-host', icon: 'ph-eye-slash', text: 'Скрыт из подписки' };
    return { cls: 'online', icon: 'ph-pulse', text: 'Активен' };
}

function pageHosts(root, _param, page) {
    root.innerHTML = pageHead({
        title: 'Хосты',
        crumbs: ['Хосты'],
        actions: `<button class="icon-btn" id="refresh" title="Обновить"><i class="ph ph-arrows-clockwise"></i></button>
                  <button class="btn" id="create"><i class="ph ph-plus"></i>Создать</button>`,
    }) + `<div id="list">${loaderHtml()}</div>`;
    const list = $('#list', root);
    let hosts = [];
    let profiles = [];
    const selected = new Set();

    const bar = document.createElement('div');
    bar.className = 'bulk-bar hidden';
    document.body.appendChild(bar);
    page.destroy = () => bar.remove();

    const load = async () => {
        try {
            [hosts, profiles] = await Promise.all([
                api('GET', '/api/hosts'),
                loadProfiles({ fresh: true }).catch((e) => { if (e.status === 401) throw e; return []; }),
            ]);
            const alive = new Set(hosts.map((h) => h.uuid));
            [...selected].forEach((u) => { if (!alive.has(u)) selected.delete(u); });
            draw();
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) return handleError(e);
            list.innerHTML = errorBox(e);
            selected.clear();
            drawBar();
        }
    };

    const inboundInfo = (h) => {
        const p = profiles.find((x) => x.uuid === (h.inbound && h.inbound.configProfileUuid));
        const i = p && (p.inbounds || []).find((x) => x.uuid === h.inbound.configProfileInboundUuid);
        return { profile: p, tag: i ? i.tag : null };
    };

    const draw = () => {
        if (!hosts.length) {
            list.innerHTML = '<div class="card empty"><i class="ph-duotone ph-list-checks"></i>Хостов пока нет</div>';
            drawBar();
            return;
        }
        list.innerHTML = `<div class="node-list">${hosts.map((h) => {
            const st = hostStatus(h);
            const inf = inboundInfo(h);
            return `
            <div class="host-card ${selected.has(h.uuid) ? 'selected' : ''} ${inf.profile ? '' : 'dangling'}" data-id="${esc(h.uuid)}">
                <label class="hc-check" title="Выбрать"><input type="checkbox" data-sel="${esc(h.uuid)}" ${selected.has(h.uuid) ? 'checked' : ''}></label>
                <div class="hc-body" data-open="${esc(h.uuid)}">
                    <div class="hc-line">
                        <span class="st-icon sm ${st.cls}" title="${esc(st.text)}"><i class="ph-duotone ${st.icon}"></i></span>
                        <span class="hc-remark">${esc(h.remark)}</span>
                        <span class="hc-addr">${esc(h.address)}${h.port ? ':' + esc(h.port) : ''}</span>
                    </div>
                    <div class="hc-tags">
                        ${inf.profile
                            ? `<span class="hc-tag" style="color:${hashColor(inf.profile.uuid)}"><i class="ph-fill ph-star-four"></i>${esc(inf.profile.name)}<span class="sep">›</span><span style="opacity:.75">${esc(inf.tag || 'UNKNOWN')}</span></span>`
                            : '<span class="hc-tag" style="color:var(--red-5)"><i class="ph ph-warning-circle"></i>DANGLING</span>'}
                        ${(h.tags || []).slice().sort().map((t) => `<span class="hc-tag" style="color:${hashColor(t)}"><i class="ph ph-star"></i>${esc(t)}</span>`).join('')}
                    </div>
                </div>
                <button class="icon-btn subtle" data-menu data-id="${esc(h.uuid)}" aria-label="Действия"><i class="ph ph-dots-three-vertical"></i></button>
            </div>`;
        }).join('')}</div>`;

        $$('[data-open]', list).forEach((el) => el.addEventListener('click', () => {
            hostModal(hosts.find((x) => x.uuid === el.dataset.open), profiles, load);
        }));
        $$('[data-sel]', list).forEach((cb) => cb.addEventListener('change', () => {
            if (cb.checked) selected.add(cb.dataset.sel); else selected.delete(cb.dataset.sel);
            cb.closest('.host-card').classList.toggle('selected', cb.checked);
            drawBar();
        }));
        bindRowMenu(list, '[data-menu]', (id) => {
            const h = hosts.find((x) => x.uuid === id);
            return [
                { label: h.remark },
                { text: 'Редактировать', icon: 'ph-pencil-simple', onClick: () => hostModal(h, profiles, load) },
                {
                    text: h.isDisabled ? 'Включить' : 'Отключить', icon: h.isDisabled ? 'ph-pulse' : 'ph-prohibit',
                    onClick: () => run(() => api('PATCH', '/api/hosts', { uuid: id, isDisabled: !h.isDisabled }), { success: h.isDisabled ? 'Хост включён' : 'Хост отключён' }).then(load),
                },
                {
                    text: h.isHidden ? 'Показать в подписке' : 'Скрыть из подписки', icon: h.isHidden ? 'ph-eye' : 'ph-eye-slash',
                    onClick: () => run(() => api('PATCH', '/api/hosts', { uuid: id, isHidden: !h.isHidden }), { success: h.isHidden ? 'Хост снова в подписке' : 'Хост скрыт из подписки' }).then(load),
                },
                { text: 'Клонировать', icon: 'ph-copy', onClick: () => run(() => api('POST', '/api/hosts/actions/clone', { cloneFromUuid: id }), { success: 'Хост склонирован' }).then(load) },
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
        drawBar();
    };

    // ---------- массовые действия ----------

    const uuids = () => hosts.filter((h) => selected.has(h.uuid)).map((h) => h.uuid);

    const bulk = async (method, path, body, msg) => {
        const r = await run(() => api(method, path, body), { success: msg });
        if (r !== undefined) selected.clear();
        load();
    };

    const move = async (dir) => {
        const order = hosts.slice();
        const sel = (h) => selected.has(h.uuid);
        if (dir === 'top') order.sort((a, b) => sel(b) - sel(a));
        if (dir === 'bottom') order.sort((a, b) => sel(a) - sel(b));
        if (dir === 'up') {
            for (let i = 1; i < order.length; i++) {
                if (sel(order[i]) && !sel(order[i - 1])) [order[i - 1], order[i]] = [order[i], order[i - 1]];
            }
        }
        if (dir === 'down') {
            for (let i = order.length - 2; i >= 0; i--) {
                if (sel(order[i]) && !sel(order[i + 1])) [order[i], order[i + 1]] = [order[i + 1], order[i]];
            }
        }
        if (order.every((h, i) => h === hosts[i])) return;
        hosts = order;
        draw();
        const r = await run(() => api('POST', '/api/hosts/actions/reorder', { hosts: order.map((h, i) => ({ uuid: h.uuid, viewPosition: i })) }));
        if (r === undefined) load();
    };

    const drawBar = () => {
        const ids = uuids();
        if (!ids.length) { bar.classList.add('hidden'); bar.innerHTML = ''; return; }
        const allHidden = hosts.filter((h) => selected.has(h.uuid)).every((h) => h.isHidden);
        bar.classList.remove('hidden');
        bar.innerHTML = `
            <div class="bulk-inner">
                <div class="row between">
                    <span class="badge gray filled-gray">Выбрано: ${ids.length}</span>
                    <span class="row" style="gap:2px">
                        <button class="icon-btn subtle" data-b="all" title="Выбрать все"><i class="ph ph-selection-all"></i></button>
                        <button class="icon-btn subtle" data-b="clear" title="Снять выделение"><i class="ph ph-x"></i></button>
                    </span>
                </div>
                <div class="btn-group">
                    <button class="btn btn-soft-gray" data-b="top" title="В начало"><i class="ph ph-arrow-line-up"></i></button>
                    <button class="btn btn-soft-gray" data-b="up" title="Выше"><i class="ph ph-arrow-fat-up"></i></button>
                    <button class="btn btn-soft-gray" data-b="down" title="Ниже"><i class="ph ph-arrow-fat-down"></i></button>
                    <button class="btn btn-soft-gray" data-b="bottom" title="В конец"><i class="ph ph-arrow-line-down"></i></button>
                </div>
                <div class="bulk-pair">
                    <button class="btn btn-soft-green" data-b="enable"><i class="ph-duotone ph-pulse"></i>Включить</button>
                    <button class="btn btn-soft-gray" data-b="disable"><i class="ph-duotone ph-prohibit"></i>Выключить</button>
                </div>
                ${allHidden
                    ? '<button class="btn btn-soft-violet btn-block" data-b="show"><i class="ph ph-eye"></i>Показать в подписке</button>'
                    : '<button class="btn btn-soft-violet btn-block" data-b="hide"><i class="ph ph-eye-slash"></i>Скрыть из подписки</button>'}
                <button class="btn btn-soft-indigo btn-block" data-b="clone"><i class="ph ph-copy"></i>Клонировать</button>
                <button class="btn btn-red-light btn-block" data-b="delete"><i class="ph ph-trash"></i>Удалить</button>
            </div>`;

        const actions = {
            all: () => { hosts.forEach((h) => selected.add(h.uuid)); draw(); },
            clear: () => { selected.clear(); draw(); },
            top: () => move('top'),
            up: () => move('up'),
            down: () => move('down'),
            bottom: () => move('bottom'),
            enable: () => bulk('POST', '/api/hosts/bulk/enable', { uuids: ids }, `Включено: ${ids.length}`),
            disable: () => bulk('POST', '/api/hosts/bulk/disable', { uuids: ids }, `Выключено: ${ids.length}`),
            hide: () => bulk('PATCH', '/api/hosts/bulk/update', { uuids: ids, isHidden: true }, `Скрыто из подписки: ${ids.length}`),
            show: () => bulk('PATCH', '/api/hosts/bulk/update', { uuids: ids, isHidden: false }, `Возвращено в подписку: ${ids.length}`),
            clone: async () => {
                const src = hosts.filter((h) => selected.has(h.uuid) && h.inbound && h.inbound.configProfileInboundUuid);
                if (!src.length) { toast('error', 'Нечего клонировать', 'У выбранных хостов нет инбаунда'); return; }
                if (!(await confirmDialog({ title: 'Клонировать хосты?', text: `Будет создано копий: <b>${src.length}</b>.`, confirmText: 'Клонировать' }))) return;
                let ok = 0;
                for (const h of src) {
                    try { await api('POST', '/api/hosts/actions/clone', { cloneFromUuid: h.uuid }); ok++; } catch (e) { handleError(e); break; }
                }
                if (ok) toast('success', `Склонировано: ${ok}`);
                selected.clear();
                load();
            },
            delete: async () => {
                if (await confirmDialog({ title: 'Удалить хосты?', text: `Будет удалено хостов: <b>${ids.length}</b>. Они пропадут из подписок пользователей.`, confirmText: 'Удалить', danger: true })) {
                    bulk('POST', '/api/hosts/bulk/delete', { uuids: ids }, `Удалено: ${ids.length}`);
                }
            },
        };
        $$('[data-b]', bar).forEach((b) => { b.onclick = () => actions[b.dataset.b](); });
    };

    $('#refresh', root).onclick = load;
    $('#create', root).onclick = () => hostModal(null, profiles, load);
    load();
}

function hostInboundOptions(profiles, cur, emptyLabel) {
    const opt = (v, label) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(label)}</option>`;
    return opt('', emptyLabel) + profiles.map((p) => `<optgroup label="${esc(p.name)}">${(p.inbounds || []).map((i) => opt(`${p.uuid}|${i.uuid}`, `${i.tag} (${i.type}${i.port ? ', ' + i.port : ''})`)).join('')}</optgroup>`).join('');
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
                    <select class="select" name="inbound" required>${hostInboundOptions(profiles, curInbound, '— выберите —')}</select></div>
                <div class="field"><label>Адрес<span class="req">*</span></label>
                    <input class="input mono" name="address" value="${esc(h.address)}" required placeholder="node.example.com"></div>
                <div class="field"><label>Порт<span class="req">*</span></label>
                    <input class="input mono" name="port" type="number" min="1" max="65535" value="${esc(h.port)}" required></div>

                <div class="fieldset full">
                    <div class="fieldset-legend"><i class="ph ph-shield-check"></i>Транспорт и безопасность</div>
                    <div class="fgrid">
                        <div class="field"><label>SNI</label><input class="input mono" name="sni" value="${esc(h.sni)}"></div>
                        <div class="field"><label>Path</label><input class="input mono" name="path" value="${esc(h.path)}"></div>
                        <div class="field"><label>Security layer</label>
                            <select class="select" name="securityLayer">${['DEFAULT', 'TLS', 'NONE'].map((v) => opt(v, h.securityLayer)).join('')}</select></div>
                        <div class="field"><label>ALPN</label>
                            <select class="select" name="alpn">${opt('', h.alpn, 'по умолчанию')}${ALPNS.map((v) => opt(v, h.alpn)).join('')}</select></div>
                        <div class="field"><label>Fingerprint</label>
                            <select class="select" name="fingerprint">${opt('', h.fingerprint, 'по умолчанию')}${FINGERPRINTS.map((v) => opt(v, h.fingerprint)).join('')}</select></div>
                        <div class="field"><label>Описание сервера</label>
                            <input class="input" name="serverDescription" maxlength="30" value="${esc(h.serverDescription)}" placeholder="до 30 символов"></div>
                    </div>
                </div>

                <div class="field full"><label>Теги</label>
                    <div class="desc">Через запятую. Только A–Z, 0–9, _ и :</div>
                    <input class="input mono" name="tags" value="${esc((h.tags || []).join(', '))}" placeholder="ALL_COUNTRY, NETHERLANDSGROUP_MAIN"></div>
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
                // поле host не трогаем: оно берётся из конфига инбаунда
                const payload = {
                    remark: f('remark').value.trim(),
                    address: f('address').value.trim(),
                    port: Number(f('port').value),
                    inbound: { configProfileUuid, configProfileInboundUuid },
                    sni: orNull('sni'),
                    path: orNull('path'),
                    securityLayer: f('securityLayer').value,
                    alpn: orNull('alpn'),
                    fingerprint: orNull('fingerprint'),
                    serverDescription: orNull('serverDescription'),
                    tags: parseTags(f('tags').value),
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

/* =========================================================================
 * Домены (DNS Cloudflare через прокси; ключ Cloudflare хранится на сервере)
 * ========================================================================= */

const CF_TTLS = [[1, 'Авто'], [60, '1 мин'], [120, '2 мин'], [300, '5 мин'], [600, '10 мин'], [900, '15 мин'], [1800, '30 мин'],
    [3600, '1 ч'], [7200, '2 ч'], [18000, '5 ч'], [43200, '12 ч'], [86400, '1 день']];
const CF_TYPES = ['A', 'AAAA', 'CNAME', 'TXT', 'MX', 'NS'];
const CF_PROXIABLE = ['A', 'AAAA', 'CNAME'];
const CF_TYPE_COLORS = { A: 'var(--cyan-4)', AAAA: '#4dabf7', CNAME: 'var(--violet-4)', TXT: 'var(--yellow-5)', MX: '#ff922b', NS: 'var(--teal-5)' };
const CF_CONTENT_LABEL = { A: 'IPv4-адрес', AAAA: 'IPv6-адрес', CNAME: 'Цель (домен)', TXT: 'Текст', MX: 'Почтовый сервер', NS: 'Сервер имён' };
const CF_CONTENT_PH = { A: '1.2.3.4', AAAA: '2001:db8::1', CNAME: 'target.example.com', TXT: 'v=spf1 include:_spf.example.com ~all', MX: 'mail.example.com', NS: 'ns1.example.com' };

const cf = (method, path, body) => api(method, `/cf${path}`, body);

// Все страницы списка Cloudflare
async function cfAll(path, perPage = 100) {
    const out = [];
    for (let page = 1; page <= 50; page++) {
        const sep = path.includes('?') ? '&' : '?';
        const r = await cf('GET', `${path}${sep}page=${page}&per_page=${perPage}`);
        out.push(...(r.result || []));
        const info = r.result_info || {};
        if (!info.total_pages || page >= info.total_pages) break;
    }
    return out;
}

function cfTtlLabel(ttl) {
    const t = CF_TTLS.find((x) => x[0] === ttl);
    if (t) return t[1];
    if (ttl >= 86400 && ttl % 86400 === 0) return `${ttl / 86400} д`;
    if (ttl >= 3600 && ttl % 3600 === 0) return `${ttl / 3600} ч`;
    if (ttl >= 60 && ttl % 60 === 0) return `${ttl / 60} мин`;
    return `${ttl} с`;
}

function cfRelName(name, zone) {
    if (name === zone) return '@';
    return name.endsWith(`.${zone}`) ? name.slice(0, -(zone.length + 1)) : name;
}

function cfFqdn(rel, zone) {
    const v = rel.trim().replace(/\.$/, '').toLowerCase();
    if (!v || v === '@') return zone;
    return v === zone || v.endsWith(`.${zone}`) ? v : `${v}.${zone}`;
}

function cfNotConfigured(e) {
    return e instanceof ApiError && e.status === 501
        ? `<div class="alert warn"><i class="ph ph-cloud-slash"></i><div><div class="fw6">Cloudflare не подключён</div>
            <div class="sm">Администратор должен указать ключ Cloudflare (CF_API_TOKEN) в настройках сервера интерфейса.</div></div></div>`
        : null;
}

function pageDomains(root) {
    root.innerHTML = pageHead({
        title: 'Домены',
        crumbs: ['Домены'],
        actions: '<button class="icon-btn" id="refresh" title="Обновить"><i class="ph ph-arrows-clockwise"></i></button>',
    }) + `<div id="list">${loaderHtml()}</div>`;
    const list = $('#list', root);

    const load = async () => {
        list.innerHTML = loaderHtml();
        let zones;
        try {
            zones = await cfAll('/zones', 50);
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) return handleError(e);
            list.innerHTML = cfNotConfigured(e) || errorBox(e);
            return;
        }
        if (!zones.length) {
            list.innerHTML = '<div class="card empty"><i class="ph-duotone ph-globe-hemisphere-west"></i>Нет доступных доменов<div class="sm" style="margin-top:6px">Ключ Cloudflare не даёт доступа ни к одной зоне</div></div>';
            return;
        }
        zones.sort((a, b) => a.name.localeCompare(b.name));
        list.innerHTML = `<div class="grid">${zones.map((z) => {
            const st = z.status === 'active' ? ['teal', 'Активен'] : z.status === 'pending' ? ['yellow', 'Ожидает NS'] : ['gray', z.status];
            return `
            <div class="card clickable" data-zone="${esc(z.id)}">
                <div class="card-head">
                    <div class="card-icon" style="color:#f6821f;border-color:rgba(246,130,31,.35);background:rgba(246,130,31,.1)"><i class="ph-duotone ph-globe-hemisphere-west"></i></div>
                    <div style="min-width:0;flex:1">
                        <div class="card-title ellipsis">${esc(z.name)}</div>
                        <div class="xs dimmed">${esc(z.plan && z.plan.name ? z.plan.name : '')}</div>
                    </div>
                    <span class="badge ${st[0]}">${esc(st[1])}</span>
                </div>
                ${z.paused ? '<div class="xs" style="margin-top:10px;color:var(--yellow-5)">Cloudflare на паузе — прокси не работает</div>' : ''}
            </div>`;
        }).join('')}</div>`;
        $$('[data-zone]', list).forEach((c) => c.addEventListener('click', () => { location.hash = `#/domain/${c.dataset.zone}`; }));
    };

    $('#refresh', root).onclick = load;
    load();
}

async function pageZone(root, zoneId, page) {
    if (!/^[a-f0-9]{32}$/.test(zoneId || '')) { location.replace('#/domains'); return; }
    root.innerHTML = loaderHtml();
    let zone;
    try {
        zone = (await cf('GET', `/zones/${zoneId}`)).result;
    } catch (e) {
        if (e instanceof ApiError && e.status === 401) return handleError(e);
        root.innerHTML = pageHead({ title: 'Домен', crumbs: [{ text: 'Домены', href: '#/domains' }] }) + (cfNotConfigured(e) || errorBox(e));
        return;
    }
    if (page !== currentPage) return;

    root.innerHTML = pageHead({
        title: `<i class="ph-duotone ph-globe-hemisphere-west" style="color:#f6821f"></i>${esc(zone.name)}`,
        crumbs: [{ text: 'Домены', href: '#/domains' }, zone.name],
        actions: `<button class="icon-btn" id="refresh" title="Обновить"><i class="ph ph-arrows-clockwise"></i></button>
                  <button class="btn" id="create"><i class="ph ph-plus"></i>Добавить запись</button>`,
    }) + `
        <div class="row wrap dns-filters">
            <div class="input-wrap" style="flex:1;min-width:200px"><i class="ph ph-magnifying-glass"></i>
                <input class="input" id="q" placeholder="Поиск по имени или значению"></div>
            <select class="select" id="type" style="width:auto;min-width:140px"><option value="">Все типы</option></select>
        </div>
        <div id="list">${loaderHtml()}</div>`;
    const list = $('#list', root);
    let records = [];

    const load = async () => {
        try {
            records = await cfAll(`/zones/${zoneId}/dns_records`);
            const types = [...new Set(records.map((r) => r.type))].sort();
            const sel = $('#type', root);
            const cur = sel.value;
            sel.innerHTML = '<option value="">Все типы</option>' + types.map((t) => `<option ${t === cur ? 'selected' : ''}>${esc(t)}</option>`).join('');
            draw();
        } catch (e) {
            if (e instanceof ApiError && e.status === 401) return handleError(e);
            list.innerHTML = errorBox(e);
        }
    };

    const draw = () => {
        const q = $('#q', root).value.trim().toLowerCase();
        const t = $('#type', root).value;
        const order = { A: 0, AAAA: 1, CNAME: 2, MX: 3, TXT: 4, NS: 5 };
        const shown = records
            .filter((r) => (!t || r.type === t) && (!q || r.name.toLowerCase().includes(q) || String(r.content).toLowerCase().includes(q)))
            .sort((a, b) => (order[a.type] ?? 9) - (order[b.type] ?? 9) || a.name.localeCompare(b.name));
        if (!records.length) {
            list.innerHTML = '<div class="card empty"><i class="ph-duotone ph-list-dashes"></i>Записей пока нет</div>';
            return;
        }
        if (!shown.length) {
            list.innerHTML = '<div class="card empty"><i class="ph-duotone ph-magnifying-glass"></i>Ничего не найдено</div>';
            return;
        }
        list.innerHTML = `<div class="dns-count xs dimmed">${shown.length} ${plural(shown.length, 'запись', 'записи', 'записей')}</div>
            <div class="node-list">${shown.map((r) => `
            <div class="dns-row" data-id="${esc(r.id)}">
                <span class="dns-type" style="color:${CF_TYPE_COLORS[r.type] || 'var(--dark-1)'}">${esc(r.type)}</span>
                <div class="dns-name" title="${esc(r.name)}">${esc(cfRelName(r.name, zone.name))}</div>
                <div class="dns-content" title="${esc(r.content)}">${r.type === 'MX' && r.priority !== undefined ? `<span class="dimmed">${esc(r.priority)}</span> ` : ''}${esc(r.content)}</div>
                <div class="dns-proxy">${r.proxiable
                    ? `<button class="cf-cloud ${r.proxied ? 'on' : ''}" data-proxy="${esc(r.id)}" title="${r.proxied ? 'Проксируется (оранжевое облако). Нажмите, чтобы выключить' : 'Только DNS (серое облако). Нажмите, чтобы включить прокси'}">
                        <i class="ph-fill ph-cloud"></i><span>${r.proxied ? 'Прокси' : 'DNS'}</span></button>`
                    : '<span class="xs dimmed">—</span>'}</div>
                <div class="dns-ttl xs dimmed">${esc(cfTtlLabel(r.ttl))}</div>
                <button class="icon-btn subtle" data-menu data-id="${esc(r.id)}" aria-label="Действия"><i class="ph ph-dots-three-vertical"></i></button>
            </div>`).join('')}</div>`;

        $$('.dns-row', list).forEach((row) => row.addEventListener('click', () => {
            dnsRecordModal(zone, records.find((x) => x.id === row.dataset.id), load);
        }));
        $$('[data-proxy]', list).forEach((b) => b.addEventListener('click', async (e) => {
            e.stopPropagation();
            const r = records.find((x) => x.id === b.dataset.proxy);
            b.disabled = true;
            const res = await run(() => cf('PATCH', `/zones/${zoneId}/dns_records/${r.id}`, { proxied: !r.proxied }), { success: r.proxied ? 'Прокси выключен' : 'Прокси включён' });
            if (res !== undefined) load(); else b.disabled = false;
        }));
        bindRowMenu(list, '[data-menu]', (id) => {
            const r = records.find((x) => x.id === id);
            return [
                { label: `${r.type} ${cfRelName(r.name, zone.name)}` },
                { text: 'Редактировать', icon: 'ph-pencil-simple', onClick: () => dnsRecordModal(zone, r, load) },
                r.proxiable ? {
                    text: r.proxied ? 'Выключить прокси' : 'Включить прокси', icon: 'ph-cloud',
                    onClick: () => run(() => cf('PATCH', `/zones/${zoneId}/dns_records/${r.id}`, { proxied: !r.proxied }), { success: 'Сохранено' }).then(load),
                } : null,
                { text: 'Копировать значение', icon: 'ph-copy', onClick: () => copyText(r.content) },
                { text: 'Копировать имя', icon: 'ph-copy', onClick: () => copyText(r.name) },
                { divider: true },
                { text: 'Удалить', icon: 'ph-trash', red: true, onClick: () => dnsDelete(zone, r, load) },
            ];
        });
    };

    $('#q', root).addEventListener('input', draw);
    $('#type', root).addEventListener('change', draw);
    $('#refresh', root).onclick = load;
    $('#create', root).onclick = () => dnsRecordModal(zone, null, load);
    load();
}

async function dnsDelete(zone, r, onDone) {
    if (await confirmDialog({
        title: 'Удалить запись?',
        text: `<span class="mono">${esc(r.type)} ${esc(r.name)} → ${esc(r.content)}</span><br><br>Запись будет удалена из Cloudflare. Это действие необратимо.`,
        confirmText: 'Удалить', danger: true,
    })) {
        const res = await run(() => cf('DELETE', `/zones/${zone.id}/dns_records/${r.id}`), { success: 'Запись удалена' });
        if (res !== undefined) onDone();
        return res !== undefined;
    }
    return false;
}

function dnsRecordModal(zone, rec, onDone) {
    const isNew = !rec;
    const r = rec || { type: 'A', name: '', content: '', ttl: 1, proxied: false, comment: '' };
    const editable = CF_TYPES.includes(r.type);
    const types = editable ? CF_TYPES : [r.type];

    openModal({
        title: isNew ? 'Новая DNS-запись' : `${r.type} ${cfRelName(r.name, zone.name)}`,
        icon: 'ph-globe-hemisphere-west',
        body: `
            ${!editable ? `<div class="alert warn"><i class="ph ph-info"></i><div class="sm">Записи типа ${esc(r.type)} здесь можно только удалить. Изменить — в панели Cloudflare.</div></div>` : ''}
            <form class="fgrid" id="df" autocomplete="off">
                <div class="field"><label>Тип</label>
                    <select class="select" name="type" ${isNew ? '' : 'disabled'}>${types.map((t) => `<option ${t === r.type ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div>
                <div class="field"><label>Имя<span class="req">*</span></label>
                    <div class="input-wrap dns-name-wrap">
                        <input class="input mono" name="name" value="${esc(isNew ? '' : cfRelName(r.name, zone.name))}" placeholder="@ или www" ${editable ? '' : 'disabled'}>
                        <span class="dns-suffix">.${esc(zone.name)}</span>
                    </div>
                    <div class="desc">@ — сам домен ${esc(zone.name)}</div></div>
                <div class="field full"><label id="content-label">${esc(CF_CONTENT_LABEL[r.type] || 'Значение')}<span class="req">*</span></label>
                    ${r.type === 'TXT'
                        ? `<textarea class="textarea mono" name="content" rows="3" ${editable ? '' : 'disabled'}>${esc(r.content)}</textarea>`
                        : `<input class="input mono" name="content" value="${esc(r.content)}" ${editable ? '' : 'disabled'}>`}</div>
                <div class="field" id="prio-field"><label>Приоритет</label>
                    <input class="input" name="priority" type="number" min="0" max="65535" value="${esc(r.priority ?? 10)}"></div>
                <div class="field" id="ttl-field"><label>TTL</label>
                    <select class="select" name="ttl">${CF_TTLS.map(([v, l]) => `<option value="${v}" ${v === r.ttl ? 'selected' : ''}>${l}</option>`).join('')}
                        ${CF_TTLS.some((x) => x[0] === r.ttl) ? '' : `<option value="${esc(r.ttl)}" selected>${esc(cfTtlLabel(r.ttl))}</option>`}</select></div>
                <div class="field full" id="proxy-field">
                    <label class="switch"><input type="checkbox" name="proxied" ${r.proxied ? 'checked' : ''}><span class="track"></span>
                        <span><i class="ph-fill ph-cloud" style="color:#f6821f"></i> Проксировать через Cloudflare</span></label>
                    <div class="desc" style="margin-top:4px">Оранжевое облако: трафик идёт через Cloudflare, реальный IP скрыт. Для нод VPN обычно выключают.</div>
                </div>
                <div class="field full"><label>Комментарий</label>
                    <input class="input" name="comment" maxlength="100" value="${esc(r.comment || '')}" placeholder="необязательно" ${editable ? '' : 'disabled'}></div>
            </form>`,
        foot: `${!isNew ? '<button class="btn btn-red-light left" id="del"><i class="ph ph-trash"></i>Удалить</button>' : ''}
               <button class="btn btn-default" data-cancel>Отмена</button>
               ${editable ? `<button class="btn" id="save"><i class="ph ph-floppy-disk"></i>${isNew ? 'Создать' : 'Сохранить'}</button>` : ''}`,
        onMount: (m, close) => {
            const form = $('#df', m);
            const f = (n) => form.elements[n];
            $('[data-cancel]', m).onclick = close;

            const sync = () => {
                const t = f('type').value;
                $('#prio-field', m).classList.toggle('hidden', t !== 'MX');
                $('#proxy-field', m).classList.toggle('hidden', !CF_PROXIABLE.includes(t));
                const proxied = CF_PROXIABLE.includes(t) && f('proxied').checked;
                f('ttl').disabled = proxied || !editable;
                if (proxied) f('ttl').value = '1';
                $('#content-label', m).firstChild.textContent = CF_CONTENT_LABEL[t] || 'Значение';
                // TXT — многострочное поле, остальное — однострочное
                const cur = f('content');
                const wantArea = t === 'TXT';
                if ((cur.tagName === 'TEXTAREA') !== wantArea) {
                    const el = document.createElement(wantArea ? 'textarea' : 'input');
                    el.className = wantArea ? 'textarea mono' : 'input mono';
                    el.name = 'content';
                    if (wantArea) el.rows = 3;
                    el.value = cur.value;
                    cur.replaceWith(el);
                }
                f('content').placeholder = CF_CONTENT_PH[t] || '';
            };
            f('type').addEventListener('change', sync);
            f('proxied').addEventListener('change', sync);
            sync();

            if (!isNew) {
                $('#del', m).onclick = async () => { if (await dnsDelete(zone, r, onDone)) close(); };
            }
            if (!editable) return;

            $('#save', m).onclick = async () => {
                const type = f('type').value;
                const content = f('content').value.trim();
                const nameRaw = f('name').value;
                if (!nameRaw.trim()) { toast('error', 'Укажите имя', 'Для самого домена введите @'); return; }
                if (!content) { toast('error', 'Укажите значение'); return; }
                if (type === 'A' && !/^(\d{1,3}\.){3}\d{1,3}$/.test(content)) { toast('error', 'Некорректный IPv4-адрес'); return; }
                if (type === 'AAAA' && !content.includes(':')) { toast('error', 'Некорректный IPv6-адрес'); return; }
                const payload = {
                    type,
                    name: cfFqdn(nameRaw, zone.name),
                    content,
                    ttl: Number(f('ttl').value) || 1,
                };
                if (CF_PROXIABLE.includes(type)) payload.proxied = f('proxied').checked;
                if (type === 'MX') payload.priority = Number(f('priority').value) || 0;
                const comment = f('comment').value.trim();
                if (comment || (!isNew && r.comment)) payload.comment = comment;

                const btn = $('#save', m);
                setBusy(btn, true);
                const res = await run(
                    () => (isNew ? cf('POST', `/zones/${zone.id}/dns_records`, payload) : cf('PATCH', `/zones/${zone.id}/dns_records/${r.id}`, payload)),
                    { success: isNew ? 'Запись создана' : 'Запись сохранена' },
                );
                setBusy(btn, false);
                if (res !== undefined) { close(); onDone(); }
            };
        },
    });
}

/* ========================================================================= */

render();

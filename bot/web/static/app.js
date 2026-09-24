/* Мини-приложение «Моя музыка»: медиатека, поиск, плеер, скачивание и загрузка треков. */
(() => {
  'use strict';

  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || '';
  const tgUser = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) || null;
  const $ = (sel) => document.querySelector(sel);
  const supports = (v) => !!(tg && tg.isVersionAtLeast && tg.isVersionAtLeast(v));
  const CREDIT = 'kpenkuu4au';

  // ---------- иконки (24×24, линейные) ----------
  const ICONS = {
    library: '<path d="M4 4v16M8 8v12M12 6v14"/><path d="m16 6 4 14"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    cloud: '<path d="M12 13v8"/><path d="m8 17 4-4 4 4"/><path d="M20.4 18.4A5 5 0 0 0 18 9h-1.3A8 8 0 1 0 4 16.3"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21.2l8.8-8.8a5.5 5.5 0 0 0 0-7.8z"/>',
    heartFill: ['<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21.2l8.8-8.8a5.5 5.5 0 0 0 0-7.8z"/>', true],
    more: ['<circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/>', true],
    play: ['<path d="M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1 1 0 0 0 0-1.7l-12-7.5A1 1 0 0 0 7 4.5z"/>', true],
    pause: ['<rect x="6" y="4" width="4" height="16" rx="1.2"/><rect x="14" y="4" width="4" height="16" rx="1.2"/>', true],
    next: ['<path d="M5 5.3v13.4a.8.8 0 0 0 1.2.7L16 13v5.5a1 1 0 0 0 2 0v-13a1 1 0 0 0-2 0V11L6.2 4.6a.8.8 0 0 0-1.2.7z"/>', true],
    prev: ['<path d="M19 5.3v13.4a.8.8 0 0 1-1.2.7L8 13v5.5a1 1 0 0 1-2 0v-13a1 1 0 0 1 2 0V11l9.8-6.4a.8.8 0 0 1 1.2.7z"/>', true],
    shuffle: '<path d="M16 3h5v5"/><path d="M4 20 21 3"/><path d="M21 16v5h-5"/><path d="m15 15 6 6"/><path d="M4 4l5 5"/>',
    repeat: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    repeatOne: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/>',
    send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
    disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>',
    user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    pin: '<path d="M12 17v5"/><path d="M5 17h14v-1.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.8V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.8a2 2 0 0 1-1.1 1.7l-1.8.9A2 2 0 0 0 5 15.2z"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/>',
    external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M8 16H3v5"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  };

  function icon(name, cls = '') {
    const def = ICONS[name];
    const [paths, filled] = Array.isArray(def) ? def : [def, false];
    const t = document.createElement('template');
    t.innerHTML = `<svg class="i${filled ? ' fill' : ''} ${cls}" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
    return t.content.firstChild;
  }

  // Как replaceChildren, но пропускает null/false: встроенный метод вывел бы их текстом «null».
  function put(el, ...children) {
    el.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false));
    return el;
  }

  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k in el && typeof el[k] !== 'object') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) {
      if (c != null && c !== false) el.append(c.nodeType ? c : String(c));
    }
    return el;
  }

  function coverEl(url, cls = '', fallback = 'music') {
    if (url) {
      const img = h('img', { class: `cover ${cls}`, src: url, loading: 'lazy', decoding: 'async', alt: '' });
      img.addEventListener('error', () => img.replaceWith(coverEl(null, cls, fallback)), { once: true });
      return img;
    }
    return h('div', { class: `cover ${cls}` }, icon(fallback));
  }

  const bigCover = (url) => (url ? url.replace('200x200', '400x400') : url);

  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
  }
  const tracksWord = (n) => `${n} ${plural(n, 'трек', 'трека', 'треков')}`;
  const filesWord = (n) => `${n} ${plural(n, 'файл', 'файла', 'файлов')}`;

  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
  }

  function fmtSize(bytes) {
    return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  }

  // «Исполнитель - Название.mp3» -> {artist, title}
  function guessFromName(name) {
    const stem = name.replace(/\.[^.]+$/, '').replace(/_/g, ' ').trim();
    const m = stem.split(/\s+[-–—]\s+/);
    return m.length >= 2 ? { artist: m[0].trim(), title: m.slice(1).join(' - ').trim() } : { artist: '', title: stem };
  }

  function shuffled(list) {
    const a = list.slice();
    for (let i = a.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // Мелочи вроде истории поиска: localStorage может быть недоступен — тогда просто не помним.
  const local = {
    key: (k) => `ym:${tgUser ? tgUser.id : 0}:${k}`,
    get(k, fallback) {
      try { const v = localStorage.getItem(this.key(k)); return v == null ? fallback : JSON.parse(v); } catch (_) { return fallback; }
    },
    set(k, v) { try { localStorage.setItem(this.key(k), JSON.stringify(v)); } catch (_) { /* приватный режим */ } },
  };

  // ---------- связь с Telegram ----------
  const haptic = {
    tap: () => { try { tg.HapticFeedback.impactOccurred('light'); } catch (_) { /* нет в браузере */ } },
    ok: () => { try { tg.HapticFeedback.notificationOccurred('success'); } catch (_) { /* нет в браузере */ } },
    err: () => { try { tg.HapticFeedback.notificationOccurred('error'); } catch (_) { /* нет в браузере */ } },
  };

  function confirmAsk(text) {
    return new Promise((resolve) => {
      if (supports('6.2')) tg.showConfirm(text, (ok) => resolve(!!ok));
      else resolve(window.confirm(text));
    });
  }

  function openLink(url) {
    if (tg && initData && tg.openLink) tg.openLink(url);
    else window.open(url, '_blank', 'noopener');
  }

  function openTelegram(url) {
    if (supports('6.1')) tg.openTelegramLink(url);
    else openLink(url);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const area = h('textarea', { value: text, style: 'position:fixed;opacity:0' });
      document.body.append(area);
      area.select();
      try { document.execCommand('copy'); } catch (__) { /* не судьба */ }
      area.remove();
    }
    haptic.tap();
    toast('Скопировано');
  }

  async function api(path, { method = 'GET', body } = {}) {
    const headers = { 'X-Telegram-Init-Data': initData };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let resp;
    try {
      resp = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (_) {
      throw new Error('Нет связи с сервером');
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error(data.error || `Ошибка ${resp.status}`);
      err.status = resp.status;
      err.code = data.code;
      if (data.code === 'login_required' && !document.body.classList.contains('no-tabs')) showLogin();
      throw err;
    }
    return data;
  }

  let toastTimer;
  function toast(text, ms = 2600) {
    const el = $('#toast');
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), ms);
  }

  const fail = (e) => { if (e && e.code === 'login_required') return; haptic.err(); toast(e.message || String(e)); };

  // ---------- состояние ----------
  const state = {
    me: null,
    playlists: [],
    liked: new Set(),
    libraryLoaded: false,
    libraryAt: 0,
    tab: null,
  };

  async function loadLibrary() {
    const data = await api('/api/library');
    state.playlists = data.playlists;
    state.liked = new Set(data.liked_ids);
    state.libraryLoaded = true;
    state.libraryAt = Date.now();
  }

  // ---------- навигация: у каждой вкладки свой стек экранов ----------
  // Экраны не пересоздаются при возврате «назад»: остаются прокрутка и уже загруженные треки.
  const view = $('#view');
  let stacks = { library: [], search: [], upload: [] };
  const stackOf = () => stacks[state.tab] || [];
  const top = () => stackOf()[stackOf().length - 1];

  function creditEl() {
    const link = h('a', { href: `https://t.me/${CREDIT}`, onclick: (e) => { e.preventDefault(); openTelegram(`https://t.me/${CREDIT}`); } },
      `@${CREDIT}`);
    return h('footer', { class: 'credit' }, '© all rights reserved by ', link, ' ', h('span', { class: 'beat' }, '❤️'));
  }

  function mountPage(fn) {
    const content = h('div', { class: 'content' });
    const entry = { el: h('div', { class: 'page enter' }, content, creditEl()), scroll: 0, dead: false, onShow: null };
    entry.el.addEventListener('animationend', () => entry.el.classList.remove('enter'), { once: true });
    const alive = () => !entry.dead;
    fn(content, alive, entry);
    return entry;
  }

  function show(entry, scroll = entry.scroll) {
    put(view, entry.el);
    window.scrollTo(0, scroll);
    if (entry.onShow) entry.onShow();
    syncChrome();
  }

  function remember() { const t = top(); if (t) t.scroll = window.scrollY; }

  function push(fn) {
    remember();
    const entry = mountPage(fn);
    stackOf().push(entry);
    show(entry, 0);
  }

  function pop() {
    const s = stackOf();
    if (s.length < 2) return;
    s.pop().dead = true;
    show(top());
  }

  function setTab(tab) {
    const s = stacks[tab];
    if (tab === state.tab && s.length) {
      if (s.length === 1) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
      while (s.length > 1) s.pop().dead = true;
      show(s[0], 0);
      return;
    }
    remember();
    state.tab = tab;
    if (!s.length) s.push(mountPage(ROOTS[tab]));
    show(top());
  }

  function resetNav() {
    for (const s of Object.values(stacks)) s.forEach((e) => { e.dead = true; });
    stacks = { library: [], search: [], upload: [] };
    state.tab = null;
  }

  function syncChrome() {
    updateBackButton();
    document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === state.tab));
    markPlaying();
    document.querySelectorAll('[data-like]').forEach(drawLike);
  }

  function updateBackButton() {
    if (!tg || !supports('6.1')) return;
    if (stackOf().length > 1 || sheetOpen()) tg.BackButton.show();
    else tg.BackButton.hide();
  }

  function onBack() {
    if (sheetOpen()) closeSheet();
    else pop();
  }

  // ---------- нижний лист ----------
  const sheet = $('#sheet');
  const backdrop = $('#backdrop');
  const sheetOpen = () => sheet.classList.contains('open');
  let onSheetClose = null;

  function openSheet(children, cls = '') {
    if (onSheetClose) { const fn = onSheetClose; onSheetClose = null; fn(); }
    sheet.className = cls;
    put(sheet, h('div', { class: 'grip' }), ...[children].flat());
    sheet.scrollTop = 0;
    sheet.classList.add('open');
    backdrop.classList.add('open');
    updateBackButton();
  }

  function closeSheet() {
    sheet.classList.remove('open');
    backdrop.classList.remove('open');
    if (onSheetClose) { const fn = onSheetClose; onSheetClose = null; fn(); }
    updateBackButton();
  }
  backdrop.addEventListener('click', closeSheet);

  // Свайп вниз по листу закрывает его.
  let dragY = null;
  sheet.addEventListener('touchstart', (e) => { dragY = sheet.scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
  sheet.addEventListener('touchmove', (e) => {
    if (dragY == null) return;
    const dy = e.touches[0].clientY - dragY;
    if (dy > 0) sheet.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  sheet.addEventListener('touchend', (e) => {
    if (dragY == null) return;
    const dy = e.changedTouches[0].clientY - dragY;
    sheet.style.transform = '';
    dragY = null;
    if (dy > 110) closeSheet();
  });

  function menuRow(ic, text, action, cls = '') {
    return h('button', { class: `row ${cls}`, onclick: () => { closeSheet(); action(); } },
      icon(ic), h('div', { class: 'meta' }, h('div', { class: 'title' }, text)));
  }

  function promptSheet(title, placeholder, value, button, onSubmit) {
    const input = h('input', { class: 'text', placeholder, value, maxLength: 100, enterKeyHint: 'done' });
    const btn = h('button', { class: 'btn block' }, button);
    const submit = async () => {
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      btn.disabled = true;
      try { await onSubmit(text); closeSheet(); } catch (e) { fail(e); } finally { btn.disabled = false; }
    };
    btn.addEventListener('click', submit);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    openSheet([h('div', { class: 'sheet-title' }, title), input, btn]);
    setTimeout(() => input.focus(), 250);
  }

  // ---------- аккаунт ----------
  function avatarEl(cls = '') {
    const name = (tgUser && (tgUser.first_name || tgUser.username)) || (state.me && state.me.login) || '?';
    if (tgUser && tgUser.photo_url) return h('img', { class: `avatar ${cls}`, src: tgUser.photo_url, alt: '' });
    const hue = ((tgUser ? tgUser.id : 7) * 47) % 360;
    return h('div', { class: `avatar ${cls}`, style: `--a1:hsl(${hue} 70% 62%);--a2:hsl(${(hue + 40) % 360} 75% 52%)` },
      name.trim().charAt(0).toUpperCase());
  }

  function openAccount() {
    if (!state.me || !state.me.connected) return;
    const name = tgUser ? [tgUser.first_name, tgUser.last_name].filter(Boolean).join(' ') : 'Вы';
    openSheet([
      h('div', { class: 'account-card' }, avatarEl('lg'),
        h('div', { class: 'meta' }, h('div', { class: 'title' }, name),
          h('div', { class: 'sub' }, `Яндекс: ${state.me.login || '—'}`)),
        state.me.has_plus ? h('span', { class: 'badge plus' }, 'Плюс') : h('span', { class: 'badge' }, 'без Плюса')),
      h('div', { class: 'card list' },
        menuRow('refresh', 'Сменить аккаунт Яндекса', () => showLogin(true)),
        menuRow('logout', 'Отключить аккаунт', logout, 'danger')),
      h('div', { class: 'sheet-note' },
        'Бот хранит только ваш вход в Яндекс Музыку. Отключите аккаунт — и бот его забудет; музыка и плейлисты в Яндексе останутся.'),
    ]);
  }

  async function logout() {
    if (!(await confirmAsk('Отключить аккаунт Яндекса? Бот забудет ваш вход, музыка в Яндексе останется.'))) return;
    try {
      await api('/api/logout', { method: 'POST' });
      stopPlayer();
      showLogin();
      toast('Аккаунт отключён');
    } catch (e) { fail(e); }
  }

  // ---------- действия с треками ----------
  const isLiked = (id) => state.liked.has(id);

  function drawLike(b) {
    const on = isLiked(b.dataset.like);
    if (b.classList.contains('liked') === on && b.firstChild) return;
    b.classList.toggle('liked', on);
    put(b, icon(on ? 'heartFill' : 'heart'));
  }

  function likeButton(t) {
    const btn = h('button', { class: 'icon-btn', 'data-like': t.id, 'aria-label': 'Нравится' });
    drawLike(btn);
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggleLike(t); });
    return btn;
  }

  const refreshLikes = (id) => document.querySelectorAll(`[data-like="${CSS.escape(id)}"]`).forEach(drawLike);

  async function toggleLike(t) {
    const was = isLiked(t.id);
    if (was) state.liked.delete(t.id); else state.liked.add(t.id);
    refreshLikes(t.id);
    haptic.tap();
    try {
      await api(`/api/likes/${encodeURIComponent(t.id)}`, { method: was ? 'DELETE' : 'POST' });
      toast(was ? 'Убрано из «Мне нравится»' : 'Добавлено в «Мне нравится»');
    } catch (e) {
      if (was) state.liked.add(t.id); else state.liked.delete(t.id);
      refreshLikes(t.id);
      fail(e);
    }
  }

  function downloadTrack(t) {
    const url = new URL(t.download, location.href).href;
    if (supports('8.0') && tg.downloadFile) {
      tg.downloadFile({ url, file_name: t.filename }, (accepted) => { if (accepted) toast('Скачивание началось'); });
    } else if (tg && tg.openLink && initData) {
      tg.openLink(url); // старые версии Telegram: откроется браузер и скачает файл
    } else {
      const a = h('a', { href: url, download: t.filename });
      document.body.append(a);
      a.click();
      a.remove();
    }
  }

  async function sendTrack(t) {
    toast('Отправляю в чат…', 30000);
    try {
      await api(`/api/tracks/${encodeURIComponent(t.id)}/send`, { method: 'POST' });
      haptic.ok();
      toast('Трек отправлен в чат с ботом');
    } catch (e) { fail(e); }
  }

  function playlistRow(p, onclick, extra = null) {
    return h('button', { class: 'row', onclick },
      coverEl(p.cover, '', 'list'),
      h('div', { class: 'meta' }, h('div', { class: 'title' }, p.title), h('div', { class: 'sub' }, tracksWord(p.count))),
      extra);
  }

  const newPlaylistRow = (then) => h('button', { class: 'row', onclick: () => createPlaylistSheet(then) },
    h('div', { class: 'cover' }, icon('plus')), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Новый плейлист')));

  function addToPlaylistSheet(t) {
    const add = async (p) => {
      try {
        await api(`/api/playlists/${p.kind}/tracks`, { method: 'POST', body: { track_id: t.id } });
        p.count += 1;
        haptic.ok();
        toast(`Добавлено в «${p.title}»`);
      } catch (e) { fail(e); }
    };
    openSheet([
      h('div', { class: 'sheet-title' }, 'Добавить в плейлист'),
      h('div', { class: 'card list' }, newPlaylistRow(add),
        state.playlists.map((p) => playlistRow(p, () => { closeSheet(); add(p); }))),
    ]);
  }

  function createPlaylistSheet(then) {
    promptSheet('Новый плейлист', 'Название', '', 'Создать', async (title) => {
      const p = await api('/api/playlists', { method: 'POST', body: { title } });
      state.playlists.unshift(p);
      haptic.ok();
      toast(`Плейлист «${p.title}» создан`);
      if (then) setTimeout(() => then(p), 50);
      else if (top() && top().onShow) top().onShow();
    });
  }

  function trackMenu(t, ctx, row) {
    openSheet([
      h('div', { class: 'sheet-track' }, coverEl(t.cover, 'lg'),
        h('div', { class: 'meta' }, h('div', { class: 'title' }, t.title), h('div', { class: 'sub' }, t.artists))),
      h('div', { class: 'card list' },
        menuRow('download', 'Скачать на телефон', () => downloadTrack(t)),
        menuRow('send', 'Отправить в чат', () => sendTrack(t)),
        menuRow('plus', 'Добавить в плейлист', () => addToPlaylistSheet(t)),
        t.album_id && menuRow('disc', 'Открыть альбом', () => push(sourceView('alb', t.album_id))),
        t.artist_id && menuRow('user', 'Открыть исполнителя', () => push(sourceView('art', t.artist_id))),
        ctx && ctx.ownKind != null && menuRow('trash', 'Удалить из плейлиста', () => removeFromPlaylist(t, ctx, row), 'danger')),
    ]);
  }

  async function removeFromPlaylist(t, ctx, row) {
    if (!(await confirmAsk(`Удалить «${t.title}» из плейлиста?`))) return;
    try {
      await api(`/api/playlists/${ctx.ownKind}/tracks/${t.index}?track_id=${encodeURIComponent(t.id)}`, { method: 'DELETE' });
      const i = ctx.tracks.indexOf(t);
      if (i >= 0) ctx.tracks.splice(i, 1);
      ctx.tracks.forEach((x) => { if (x.index > t.index) x.index -= 1; });
      row.remove();
      if (ctx.onRemoved) ctx.onRemoved();
      const p = state.playlists.find((x) => x.kind === ctx.ownKind);
      if (p) p.count = Math.max(0, p.count - 1);
      toast('Удалено из плейлиста');
    } catch (e) { fail(e); }
  }

  function trackRow(t, ctx) {
    const more = h('button', { class: 'icon-btn', 'aria-label': 'Ещё' }, icon('more'));
    const row = h('div', { class: `row${t.available ? '' : ' unavailable'}`, role: 'button', 'data-id': t.id },
      h('div', { class: 'cov' }, coverEl(t.cover), h('div', { class: 'eq' }, h('i'), h('i'), h('i'))),
      h('div', { class: 'meta' },
        h('div', { class: 'title' }, t.title),
        h('div', { class: 'sub' }, `${t.artists} · ${fmtTime(t.duration)}`)),
      likeButton(t), more);
    row.addEventListener('click', () => playQueue(ctx.tracks, ctx.tracks.indexOf(t)));
    more.addEventListener('click', (e) => { e.stopPropagation(); trackMenu(t, ctx, row); });
    return row;
  }

  const emptyEl = (ic, text) => h('div', { class: 'empty' }, icon(ic), h('div', {}, text));
  const spinner = () => h('div', { class: 'spinner' });
  const skRows = (n) => Array.from({ length: n }, () => h('div', { class: 'row' }, h('div', { class: 'cover sk' }),
    h('div', { class: 'meta' }, h('div', { class: 'sk sk-line' }), h('div', { class: 'sk sk-line short' }))));
  const skTiles = (n) => Array.from({ length: n }, () => h('div', { class: 'tile' }, h('div', { class: 'tile-cover sk' }),
    h('div', { class: 'sk sk-line' }), h('div', { class: 'sk sk-line short' })));

  // Включить список целиком (кнопка ▶ на «Мне нравится»).
  async function playSource(src, ref, shuffle) {
    try {
      const data = await api(`/api/source/${src}?${new URLSearchParams({ ref, offset: 0, limit: 100 })}`);
      if (!data.tracks.length) { toast('Здесь пока пусто'); return; }
      playQueue(data.tracks, 0, shuffle);
    } catch (e) { fail(e); }
  }

  // ---------- экран: медиатека ----------
  function libraryView(root, alive, entry) {
    const name = tgUser && tgUser.first_name;
    const avatar = h('button', { class: 'avatar-btn', 'aria-label': 'Аккаунт', onclick: openAccount }, avatarEl());
    const chip = h('button', { class: 'chip', onclick: openAccount }, icon('user', 'sm'), state.me.login || 'Аккаунт Яндекса',
      state.me.has_plus ? h('span', { class: 'badge plus' }, 'Плюс') : h('span', { class: 'badge' }, 'без Плюса'));
    const likesSub = h('div', { class: 's' }, '…');
    const likes = h('div', { class: 'likes-tile', role: 'button', onclick: () => push(sourceView('likes', '')) },
      h('div', { class: 'heart' }, icon('heartFill')),
      h('div', { class: 'meta' }, h('div', { class: 't' }, 'Мне нравится'), likesSub),
      h('button', { class: 'tile-play', 'aria-label': 'Слушать', onclick: (e) => { e.stopPropagation(); haptic.tap(); playSource('likes', '', true); } },
        icon('play')));
    const grid = h('div', { class: 'grid' }, skTiles(4));

    root.append(
      h('div', { class: 'lib-head' },
        h('div', {}, h('div', { class: 'hello' }, name ? `Привет, ${name} 👋` : 'Привет 👋'), h('h1', { class: 'page-title' }, 'Медиатека')),
        avatar),
      chip, likes,
      h('div', { class: 'section-head' }, h('h2', {}, 'Мои плейлисты'),
        h('button', { class: 'link-btn', onclick: () => createPlaylistSheet() }, icon('plus', 'sm'), 'Создать')),
      grid);

    function tile(p) {
      const pinned = p.kind === state.me.upload_target;
      return h('button', { class: 'tile', onclick: () => push(sourceView('pl', p.ref)) },
        h('div', { class: 'tile-cover' }, coverEl(bigCover(p.cover), '', 'list'),
          pinned ? h('span', { class: 'tile-pin', title: 'Сюда загружаются файлы из чата' }, icon('pin', 'sm')) : null),
        h('div', { class: 'tile-title' }, p.title),
        h('div', { class: 'tile-sub' }, tracksWord(p.count)));
    }

    const draw = () => {
      likesSub.textContent = tracksWord(state.liked.size);
      put(grid,
        h('button', { class: 'tile', onclick: () => createPlaylistSheet() },
          h('div', { class: 'tile-cover new' }, icon('plus')), h('div', { class: 'tile-title' }, 'Новый плейлист'),
          h('div', { class: 'tile-sub' }, 'создать')),
        ...state.playlists.map(tile));
    };
    const refresh = () => loadLibrary().then(() => alive() && draw()).catch(fail);
    entry.onShow = () => {
      if (state.libraryLoaded) draw();
      if (Date.now() - state.libraryAt > 30000) refresh(); // вернулись спустя время — тихо обновим
    };
    if (state.libraryLoaded) draw();
    refresh();
  }

  // ---------- экран: список треков (плейлист, альбом, артист, лайки) ----------
  function sourceView(src, ref) {
    return (root, alive) => {
      const hero = h('div', { class: 'hero' },
        h('div', { class: 'cover sk', style: 'width:184px;height:184px;border-radius:20px' }),
        h('div', { class: 'sk sk-line', style: 'width:50%;height:20px;margin-top:18px' }),
        h('div', { class: 'sk sk-line short', style: 'margin-top:8px' }));
      const list = h('div', { class: 'card list' }, skRows(6));
      const sentinel = h('div');
      root.append(hero, list, sentinel);

      const tracks = [];
      const ctx = { tracks, ownKind: null, onRemoved: () => { info.total -= 1; drawHero(); } };
      let info = null;
      let loading = false;
      let done = false;

      function drawHero() {
        const shape = src === 'art' ? 'round' : '';
        const fallback = { likes: 'heartFill', art: 'user', alb: 'disc' }[src] || 'list';
        const sub = [info.subtitle, tracksWord(src === 'likes' ? state.liked.size : info.total)].filter(Boolean).join(' · ');
        const play = h('button', { class: 'btn', onclick: () => playQueue(tracks, 0) }, icon('play', 'sm'), 'Слушать');
        const mix = h('button', { class: 'btn secondary', onclick: () => playQueue(tracks, 0, true) }, icon('shuffle', 'sm'), 'Перемешать');
        const more = h('button', { class: 'btn secondary round', 'aria-label': 'Ещё', onclick: sourceMenu }, icon('more'));
        const cover = info.cover
          ? coverEl(info.cover, shape, fallback)
          : h('div', { class: `cover ${shape}`, style: src === 'likes' ? 'background:radial-gradient(120% 140% at 0% 0%,#ff7a8a,#ff2e63 45%,#b3136b)' : null }, icon(fallback));
        put(hero,
          info.cover ? h('div', { class: 'hero-bg' }, h('div', { style: `background-image:url("${info.cover}")` })) : '',
          cover, h('h1', {}, info.title), h('div', { class: 'sub' }, sub),
          info.total ? h('div', { class: 'actions' }, play, mix, more) : null);
      }

      async function more() {
        if (loading || done || !alive()) return;
        loading = true;
        if (tracks.length) put(sentinel, spinner());
        try {
          const q = new URLSearchParams({ ref, offset: tracks.length, limit: 50 });
          const data = await api(`/api/source/${src}?${q}`);
          if (!alive()) return;
          if (!info) {
            info = data;
            ctx.ownKind = data.own_kind;
            drawHero();
            put(list);
          }
          for (const t of data.tracks) {
            tracks.push(t);
            list.append(trackRow(t, ctx));
          }
          done = !data.tracks.length || tracks.length >= data.total;
          if (!tracks.length) list.replaceWith(emptyEl('music', src === 'likes' ? 'Вы ещё ничего не лайкнули' : 'Здесь пока пусто'));
          markPlaying();
        } catch (e) {
          done = true;
          if (!info) { put(hero, emptyEl('x', e.message)); list.remove(); } else fail(e);
        } finally {
          loading = false;
          put(sentinel);
        }
        if (!done && alive() && sentinel.isConnected && sentinel.getBoundingClientRect().top < window.innerHeight + 400) more();
      }

      async function sendAll() {
        try {
          const r = await api(`/api/source/${src}/send?${new URLSearchParams({ ref: info.ref || ref })}`, { method: 'POST' });
          if (r.started) { haptic.ok(); toast('Отправляю все треки в чат с ботом — прогресс там же', 4000); }
          else toast('Уже идёт отправка другого списка — остановите её в чате или дождитесь');
        } catch (e) { fail(e); }
      }

      function sourceMenu() {
        const kind = info.own_kind;
        const own = kind != null;
        const isTarget = own && state.me.upload_target === kind;
        openSheet([
          h('div', { class: 'sheet-title' }, info.title),
          h('div', { class: 'card list' },
            menuRow('send', `Отправить ${tracksWord(info.total)} в чат`, sendAll),
            own && menuRow('pin', isTarget ? 'Не загружать сюда файлы из чата' : 'Загружать сюда файлы из чата', () => setTarget(isTarget ? null : kind)),
            own && menuRow('edit', 'Переименовать', () => promptSheet('Переименовать плейлист', 'Название', info.title, 'Сохранить', async (title) => {
              await api(`/api/playlists/${kind}`, { method: 'PATCH', body: { title } });
              info.title = title;
              const p = state.playlists.find((x) => x.kind === kind);
              if (p) p.title = title;
              drawHero();
              toast('Переименовано');
            })),
            own && menuRow('trash', 'Удалить плейлист', async () => {
              if (!(await confirmAsk(`Удалить плейлист «${info.title}»? Это нельзя отменить.`))) return;
              try {
                await api(`/api/playlists/${kind}`, { method: 'DELETE' });
                state.playlists = state.playlists.filter((x) => x.kind !== kind);
                if (state.me.upload_target === kind) state.me.upload_target = null;
                toast('Плейлист удалён');
                pop();
              } catch (e) { fail(e); }
            }, 'danger')),
        ]);
      }

      new IntersectionObserver((entries, io) => {
        if (!alive()) { io.disconnect(); return; }
        if (entries.some((e) => e.isIntersecting)) more();
      }, { rootMargin: '400px' }).observe(sentinel);
      more();
    };
  }

  async function setTarget(kind) {
    try {
      const r = await api('/api/target', { method: 'PUT', body: { kind } });
      state.me.upload_target = r.upload_target;
      const p = state.playlists.find((x) => x.kind === kind);
      toast(kind == null ? 'Файлы из чата снова будут спрашивать плейлист' : `Файлы из чата будут загружаться в «${p ? p.title : 'плейлист'}»`);
    } catch (e) { fail(e); }
  }

  // ---------- экран: поиск ----------
  const TYPES = [['track', 'Треки'], ['album', 'Альбомы'], ['artist', 'Артисты'], ['playlist', 'Плейлисты']];
  const search = { q: '', type: 'track', shown: 'track', items: [], corrected: null, ran: false };

  function rememberQuery(q) {
    const recent = local.get('recent', []).filter((x) => x.toLowerCase() !== q.toLowerCase());
    local.set('recent', [q, ...recent].slice(0, 8));
  }

  function searchView(root, alive) {
    const input = h('input', { type: 'search', placeholder: 'Треки, альбомы, артисты', value: search.q, enterKeyHint: 'search', autocomplete: 'off' });
    const clear = h('button', { class: 'icon-btn', 'aria-label': 'Очистить', hidden: !search.q, onclick: () => { input.value = ''; onInput(); input.focus(); } }, icon('x', 'sm'));
    const segs = h('div', { class: 'segments' });
    const results = h('div');
    let timer;
    let seq = 0;

    const drawSegs = () => put(segs, ...TYPES.map(([type, label]) => h('button', {
      class: type === search.type ? 'on' : '',
      onclick: () => { search.type = type; haptic.tap(); drawSegs(); run(); },
    }, label)));

    async function run() {
      const my = ++seq;
      const q = search.q.trim();
      if (!q) { search.items = []; search.ran = false; draw(); return; }
      put(results, h('div', { class: 'card list' }, skRows(5)));
      try {
        const data = await api(`/api/search?${new URLSearchParams({ q, type: search.type })}`);
        if (my !== seq || !alive()) return;
        Object.assign(search, { items: data.items, corrected: data.corrected, shown: data.type, ran: true });
        if (data.items.length) rememberQuery(q);
        draw();
      } catch (e) {
        if (alive()) put(results, emptyEl('x', e.message));
      }
    }

    function drawRecent() {
      const recent = local.get('recent', []);
      if (!recent.length) { put(results, emptyEl('search', 'Найдите трек, альбом, артиста или плейлист')); return; }
      put(results,
        h('div', { class: 'section-head' }, h('h2', {}, 'Недавнее'),
          h('button', { class: 'link-btn', onclick: () => { local.set('recent', []); drawRecent(); } }, 'Очистить')),
        h('div', { class: 'chips' }, recent.map((q) => h('button', { onclick: () => { input.value = q; onInput(); clearTimeout(timer); run(); } },
          icon('clock', 'sm'), h('span', {}, q)))));
    }

    function draw() {
      if (!search.ran) { drawRecent(); return; }
      if (!search.items.length) { put(results, emptyEl('search', 'Ничего не нашлось')); return; }
      const list = h('div', { class: 'card list' });
      const items = search.items;
      if (search.shown === 'track') {
        const ctx = { tracks: items, ownKind: null };
        list.append(...items.map((t) => trackRow(t, ctx)));
      } else if (search.shown === 'album') {
        list.append(...items.map((a) => h('button', { class: 'row', onclick: () => push(sourceView('alb', a.id)) },
          coverEl(a.cover, '', 'disc'),
          h('div', { class: 'meta' }, h('div', { class: 'title' }, a.title),
            h('div', { class: 'sub' }, [a.artists, a.year].filter(Boolean).join(' · '))), icon('chevron', 'sm'))));
      } else if (search.shown === 'artist') {
        list.append(...items.map((a) => h('button', { class: 'row', onclick: () => push(sourceView('art', a.id)) },
          coverEl(a.cover, 'round', 'user'), h('div', { class: 'meta' }, h('div', { class: 'title' }, a.name)), icon('chevron', 'sm'))));
      } else {
        list.append(...items.map((p) => h('button', { class: 'row', onclick: () => push(sourceView('pl', p.ref)) },
          coverEl(p.cover, '', 'list'),
          h('div', { class: 'meta' }, h('div', { class: 'title' }, p.title),
            h('div', { class: 'sub' }, [p.owner, tracksWord(p.count)].filter(Boolean).join(' · '))), icon('chevron', 'sm'))));
      }
      put(results,
        search.corrected ? h('div', { class: 'corrected' }, 'Показаны результаты для «', search.corrected, '»') : '', list);
      markPlaying();
    }

    function onInput() {
      search.q = input.value;
      clear.hidden = !search.q;
      clearTimeout(timer);
      timer = setTimeout(run, 350);
    }

    input.addEventListener('input', onInput);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(timer); input.blur(); run(); } });
    root.append(h('div', { class: 'search-bar' }, h('div', { class: 'search-field' }, icon('search', 'sm'), input, clear), segs), results);
    drawSegs();
    draw();
    if (!search.q) setTimeout(() => alive() && input.isConnected && input.focus(), 300);
  }

  // ---------- экран: загрузка ----------
  const up = { files: [], kind: null, running: false };
  const ACCEPT = 'audio/*,.mp3,.flac,.m4a,.aac,.ogg,.oga,.opus,.wav,.wma,.aiff,.alac,.ape';

  function uploadView(root, alive, entry) {
    const targetBox = h('div', { class: 'card list' }, skRows(1));
    const input = h('input', { type: 'file', multiple: true, accept: ACCEPT, hidden: true });
    const maxMb = state.me.max_upload_mb;
    const dz = h('label', { class: 'dropzone' },
      h('div', { class: 'bubble' }, icon('cloud')), h('b', {}, 'Выбрать аудиофайлы'),
      h('span', {}, `MP3, FLAC, M4A, WAV… до ${maxMb} МБ каждый`), input);
    const list = h('div', { class: 'card list' });
    const clear = h('button', { class: 'link-btn', onclick: () => { up.files = up.files.filter((f) => f.status !== 'done'); drawFiles(); } },
      'Убрать загруженные');
    const listHead = h('div', { class: 'section-head' }, h('h2', {}, 'Файлы'), clear);
    const button = h('button', { class: 'btn block big', onclick: runUploads });
    const uploadBar = h('div', { class: 'upload-bar' }, button);

    root.append(
      h('div', { class: 'lib-head' }, h('div', {}, h('div', { class: 'hello' }, 'В вашу Яндекс Музыку'), h('h1', { class: 'page-title' }, 'Загрузка'))),
      targetBox,
      h('div', { style: 'margin-top:14px' }, dz),
      h('div', { class: 'hint' }, state.me.can_convert
        ? 'Не-MP3 файлы сервер сам переведёт в MP3 320 kbps. Исполнителя и название можно не заполнять — возьмём теги из файла.'
        : 'Исполнителя и название можно не заполнять — возьмём теги из файла.'),
      listHead, list, uploadBar);

    input.addEventListener('change', () => { addFiles(input.files); input.value = ''; });
    for (const ev of ['dragenter', 'dragover']) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); });
    for (const ev of ['dragleave', 'drop']) dz.addEventListener(ev, () => dz.classList.remove('drag'));
    dz.addEventListener('drop', (e) => { e.preventDefault(); addFiles(e.dataTransfer.files); });

    function addFiles(fileList) {
      for (const file of fileList) {
        if (file.size > maxMb * 1048576) { toast(`${file.name}: больше ${maxMb} МБ`); continue; }
        up.files.push({ file, guess: guessFromName(file.name), artist: '', title: '', status: 'pending', progress: 0, message: '' });
      }
      haptic.tap();
      drawFiles();
    }

    const choose = (p) => { up.kind = p.kind; drawTarget(); drawButton(); };

    function drawTarget() {
      if (!state.playlists.length) {
        put(targetBox, h('button', { class: 'target', onclick: () => createPlaylistSheet(choose) },
          h('div', { class: 'cover' }, icon('plus')),
          h('div', { class: 'meta' }, h('div', { class: 'label' }, 'Куда загружать'), h('div', { class: 'value' }, 'Создать плейлист'))));
        return;
      }
      if (!state.playlists.some((p) => p.kind === up.kind)) {
        up.kind = state.playlists.some((p) => p.kind === state.me.upload_target) ? state.me.upload_target : state.playlists[0].kind;
      }
      const p = state.playlists.find((x) => x.kind === up.kind);
      const sw = h('input', { type: 'checkbox', checked: state.me.upload_target === up.kind });
      sw.addEventListener('change', async () => { await setTarget(sw.checked ? up.kind : null); sw.checked = state.me.upload_target === up.kind; });
      put(targetBox,
        h('button', { class: 'target', onclick: pickTarget },
          coverEl(p.cover, '', 'list'),
          h('div', { class: 'meta' }, h('div', { class: 'label' }, 'Загружать в плейлист'), h('div', { class: 'value' }, p.title)),
          icon('chevron', 'sm')),
        h('div', { class: 'toggle-row' },
          h('div', { class: 'meta' }, h('div', {}, 'Файлы из чата — сюда же'),
            h('div', { class: 'sub' }, 'Аудио, присланные боту, будут сразу загружаться в этот плейлист')),
          h('label', { class: 'switch' }, sw, h('span'))));
    }

    function pickTarget() {
      openSheet([
        h('div', { class: 'sheet-title' }, 'Куда загружать'),
        h('div', { class: 'card list' }, newPlaylistRow(choose),
          state.playlists.map((p) => playlistRow(p, () => { closeSheet(); choose(p); }, p.kind === up.kind ? icon('pin', 'sm') : null))),
      ]);
    }

    function fileCard(f) {
      const artist = h('input', { placeholder: f.guess.artist || 'Исполнитель', value: f.artist, 'aria-label': 'Исполнитель' });
      const title = h('input', { placeholder: f.guess.title || 'Название', value: f.title, 'aria-label': 'Название' });
      artist.addEventListener('input', () => { f.artist = artist.value; });
      title.addEventListener('input', () => { f.title = title.value; });
      const bar = h('div');
      const progress = h('div', { class: 'progress' }, bar);
      const status = h('div', { class: 'status' });
      const remove = h('button', { class: 'icon-btn', 'aria-label': 'Убрать' });
      remove.addEventListener('click', () => { up.files = up.files.filter((x) => x !== f); drawFiles(); });
      f.el = h('div', { class: 'file' },
        h('div', { class: 'file-head' }, h('div', { class: 'ficon' }, icon('music', 'sm')), h('div', { class: 'name' }, f.file.name),
          h('div', { class: 'size' }, fmtSize(f.file.size)), remove),
        h('div', { class: 'fields' }, artist, title), progress, status);
      // Меняем только нужные элементы карточки, чтобы не сбить ввод в полях.
      f.redraw = () => {
        const busy = f.status === 'uploading';
        bar.style.width = `${Math.round(f.progress * 100)}%`;
        progress.hidden = f.status === 'pending';
        status.textContent = f.message;
        status.className = `status${f.status === 'done' ? ' ok' : f.status === 'error' ? ' err' : ''}`;
        f.el.classList.toggle('done', f.status === 'done');
        remove.hidden = busy;
        put(remove, icon(f.status === 'done' ? 'x' : 'trash', 'sm'));
        artist.disabled = title.disabled = busy || f.status === 'done';
        drawButton();
      };
      f.redraw();
      return f.el;
    }

    function drawFiles() {
      list.hidden = listHead.hidden = !up.files.length;
      put(list, ...up.files.map(fileCard));
      drawButton();
    }

    function drawButton() {
      const todo = up.files.filter((f) => f.status === 'pending' || f.status === 'error').length;
      uploadBar.hidden = !todo && !up.running;
      clear.hidden = up.running || !up.files.some((f) => f.status === 'done');
      button.disabled = up.running || !todo || up.kind == null;
      put(button, up.running ? h('div', { class: 'spinner inline' }) : icon('upload', 'sm'),
        up.running ? 'Загружаю…' : `Загрузить ${filesWord(todo)}`);
    }

    async function runUploads() {
      up.running = true;
      drawButton();
      try { if (supports('6.2')) tg.enableClosingConfirmation(); } catch (_) { /* старый клиент */ }
      let ok = 0;
      for (const f of up.files.filter((x) => x.status === 'pending' || x.status === 'error')) {
        if (await uploadOne(f)) ok += 1;
      }
      up.running = false;
      try { if (supports('6.2')) tg.disableClosingConfirmation(); } catch (_) { /* старый клиент */ }
      drawButton();
      if (ok) {
        haptic.ok();
        toast(`Загружено: ${filesWord(ok)}. Яндекс обработает треки за пару минут`, 4000);
        loadLibrary().catch(() => {});
      }
    }

    function uploadOne(f) {
      return new Promise((resolve) => {
        const form = new FormData();
        form.append('kind', String(up.kind));
        form.append('artist', f.artist.trim());
        form.append('title', f.title.trim());
        form.append('fallback_artist', f.guess.artist);
        form.append('fallback_title', f.guess.title);
        form.append('file', f.file, f.file.name);
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload');
        xhr.setRequestHeader('X-Telegram-Init-Data', initData);
        Object.assign(f, { status: 'uploading', progress: 0, message: 'Отправляю на сервер…' });
        f.redraw();
        xhr.upload.onprogress = (e) => {
          if (!e.lengthComputable) return;
          f.progress = e.loaded / e.total;
          f.message = `Отправляю на сервер… ${Math.round(f.progress * 100)}%`;
          f.redraw();
        };
        xhr.upload.onload = () => { f.progress = 1; f.message = 'Обрабатываю и загружаю в Яндекс Музыку…'; f.redraw(); };
        xhr.onload = () => {
          let data = {};
          try { data = JSON.parse(xhr.responseText); } catch (_) { /* не JSON */ }
          const good = xhr.status === 200;
          f.status = good ? 'done' : 'error';
          f.message = good
            ? `Загружено${data.notes && data.notes.length ? ` · ${data.notes.join('; ')}` : ''}`
            : (data.error || `Ошибка ${xhr.status}`);
          f.redraw();
          resolve(good);
        };
        xhr.onerror = () => { Object.assign(f, { status: 'error', message: 'Нет связи с сервером' }); f.redraw(); resolve(false); };
        xhr.send(form);
      });
    }

    drawFiles();
    const ready = () => { if (alive()) { drawTarget(); drawButton(); } };
    entry.onShow = ready;
    if (state.libraryLoaded) ready();
    loadLibrary().then(ready).catch(fail);
  }

  const ROOTS = { library: libraryView, search: searchView, upload: uploadView };

  // ---------- вход в Яндекс Музыку ----------
  function showLogin(switching = false) {
    closeSheet();
    resetNav();
    $('#tabs').hidden = true;
    document.body.classList.add('no-tabs');
    show(mountPage((root, alive) => loginView(root, alive, switching)), 0);
  }

  function loginView(root, alive, switching) {
    const box = h('div', { class: 'login' });
    root.append(box);
    let timer = null;
    const stop = () => { clearInterval(timer); timer = null; };
    const back = switching ? h('button', { class: 'link-btn center', onclick: () => { stop(); enterApp(); } }, 'Вернуться в медиатеку') : null;

    function intro(error) {
      stop();
      const btn = h('button', { class: 'btn block big', onclick: () => begin(btn) }, icon('key', 'sm'), 'Получить код для входа');
      put(box,
        h('div', { class: 'login-logo' }, icon('music')),
        h('h1', {}, switching ? 'Другой аккаунт' : 'Подключите Яндекс Музыку'),
        h('p', { class: 'lead' }, 'Бот работает с вашим собственным аккаунтом Яндекса. Вход — на странице Яндекса, пароль бот не видит.'),
        error ? h('div', { class: 'login-error' }, error) : null,
        btn, back,
        switching ? null : h('ul', { class: 'perks' },
          h('li', {}, icon('heart'), 'Ваши плейлисты и «Мне нравится»'),
          h('li', {}, icon('download'), 'Скачивание треков в MP3'),
          h('li', {}, icon('upload'), 'Загрузка своих файлов в Яндекс Музыку'),
          h('li', {}, icon('lock'), 'Отключить аккаунт можно в любой момент')));
    }

    async function begin(btn) {
      btn.disabled = true;
      haptic.tap();
      try {
        pending(await api('/api/login', { method: 'POST' }));
      } catch (e) {
        haptic.err();
        intro(e.message);
      }
    }

    function pending(s) {
      const code = h('button', { class: 'code', 'aria-label': 'Скопировать код', onclick: () => copyText(s.code) },
        [...s.code].map((ch) => h('span', {}, ch)));
      const left = h('span', { class: 'left' });
      put(box,
        h('div', { class: 'login-logo sm' }, icon('key')),
        h('h1', {}, 'Вход в Яндекс'),
        h('ol', { class: 'steps' },
          h('li', {}, h('b', {}, 'Откройте страницу Яндекса'),
            h('button', { class: 'btn block', onclick: () => openLink(s.url) }, icon('external', 'sm'), s.url.replace(/^https?:\/\//, ''))),
          h('li', {}, h('b', {}, 'Введите код'), code, h('div', { class: 'hint' }, 'Нажмите на код, чтобы скопировать')),
          h('li', {}, h('b', {}, 'Подтвердите вход'), h('div', { class: 'hint' }, 'Приложение само поймёт, что всё готово'))),
        h('div', { class: 'waiting' }, h('div', { class: 'spinner inline' }), 'Жду подтверждения', left),
        h('button', { class: 'link-btn center', onclick: cancel }, 'Отмена'));

      const expiresAt = Date.now() + s.expires_in * 1000;
      let ticks = 0;
      let polling = false;
      stop();
      timer = setInterval(async () => {
        if (!alive()) { stop(); return; }
        left.textContent = ` · ${fmtTime((expiresAt - Date.now()) / 1000)}`;
        ticks += 1;
        if (ticks % 3 || polling) return;
        polling = true;
        try {
          const st = await api('/api/login');
          if (!alive()) return;
          if (st.status === 'done') {
            stop();
            haptic.ok();
            toast('Яндекс Музыка подключена 🎉');
            enterApp();
          } else if (st.status === 'failed') {
            intro(st.error);
          } else if (st.status !== 'pending') {
            intro();
          }
        } catch (_) {
          /* сеть моргнула — спросим ещё раз */
        } finally {
          polling = false;
        }
      }, 1000);
    }

    async function cancel() {
      stop();
      api('/api/login', { method: 'DELETE' }).catch(() => {});
      intro();
    }

    intro();
  }

  // ---------- плеер ----------
  const audio = $('#audio');
  const playerBar = $('#player');
  const player = { queue: [], original: null, index: -1, shuffle: false, repeat: 'off' };
  let now = null; // элементы открытого полноэкранного плеера

  const current = () => player.queue[player.index];

  function playQueue(queue, index, shuffle = false) {
    if (shuffle) {
      const pool = queue.filter((x) => x.available);
      if (!pool.length) { toast('Нет доступных треков'); return; }
      Object.assign(player, { original: pool, queue: shuffled(pool), index: 0, shuffle: true });
    } else {
      const t = queue[index];
      if (!t) return;
      if (!t.available) { toast('Трек недоступен'); return; }
      Object.assign(player, { original: null, queue: queue.slice(), index, shuffle: false });
    }
    start();
  }

  function start() {
    const t = current();
    audio.src = t.stream;
    audio.play().catch((e) => { if (e.name !== 'AbortError') toast('Не удалось включить трек'); });
    drawBar();
    markPlaying();
    if (now) drawNow();
    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: t.title, artist: t.artists, album: t.album || '',
        artwork: t.cover ? [{ src: bigCover(t.cover), sizes: '400x400', type: 'image/jpeg' }] : [],
      });
    }
  }

  function step(delta, auto = false) {
    if (!player.queue.length) return false;
    if (auto && player.repeat === 'one') { audio.currentTime = 0; audio.play().catch(() => {}); return true; }
    const n = player.queue.length;
    let i = player.index;
    for (let k = 0; k < n; k += 1) {
      i += delta;
      if (i < 0 || i >= n) {
        if (player.repeat !== 'all') return false;
        i = (i + n) % n;
      }
      if (player.queue[i].available) { player.index = i; start(); return true; }
    }
    return false;
  }

  function toggle() {
    if (!current()) return;
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  }

  function toggleShuffle() {
    const cur = current();
    if (!cur) return;
    if (!player.shuffle) {
      const rest = player.queue.filter((x, i) => i !== player.index && x.available);
      Object.assign(player, { original: player.queue.slice(), queue: [cur, ...shuffled(rest)], index: 0, shuffle: true });
    } else {
      const orig = player.original || player.queue;
      Object.assign(player, { queue: orig, index: Math.max(0, orig.indexOf(cur)), original: null, shuffle: false });
    }
    haptic.tap();
    toast(player.shuffle ? 'Перемешано' : 'По порядку');
    if (now) drawNow();
  }

  function cycleRepeat() {
    player.repeat = { off: 'all', all: 'one', one: 'off' }[player.repeat];
    haptic.tap();
    toast({ off: 'Повтор выключен', all: 'Повтор списка', one: 'Повтор трека' }[player.repeat]);
    if (now) drawNow();
  }

  function stopPlayer() {
    audio.pause();
    audio.removeAttribute('src');
    Object.assign(player, { queue: [], original: null, index: -1 });
    drawBar();
  }

  function markPlaying() {
    const t = current();
    document.querySelectorAll('.row[data-id]').forEach((r) => r.classList.toggle('playing', !!t && r.dataset.id === t.id));
  }

  function drawBar() {
    const t = current();
    playerBar.hidden = !t;
    document.body.classList.toggle('has-player', !!t);
    document.body.classList.toggle('paused', audio.paused);
    if (!t) return;
    playerBar.querySelector('.cover').replaceWith(coverEl(t.cover));
    playerBar.querySelector('.title').textContent = t.title;
    playerBar.querySelector('.sub').textContent = t.artists;
    put(playerBar.querySelector('[data-act=toggle]'), icon(audio.paused ? 'play' : 'pause'));
    put(playerBar.querySelector('[data-act=next]'), icon('next'));
  }

  function drawProgress() {
    const d = audio.duration || (current() && current().duration) || 0;
    playerBar.querySelector('.bar div').style.width = d ? `${(audio.currentTime / d) * 100}%` : '0';
    if (now && !now.dragging) {
      now.seek.max = Math.floor(d) || 1;
      now.seek.value = Math.floor(audio.currentTime);
      now.cur.textContent = fmtTime(audio.currentTime);
      now.total.textContent = fmtTime(d);
    }
  }

  function drawNow() {
    const t = current();
    if (!t || !now) return;
    now.bg.style.backgroundImage = t.cover ? `url("${bigCover(t.cover)}")` : 'none';
    now.cover.replaceWith(now.cover = coverEl(bigCover(t.cover)));
    now.title.textContent = t.title;
    now.sub.textContent = t.artists;
    put(now.play, icon(audio.paused ? 'play' : 'pause'));
    now.like.replaceWith(now.like = likeButton(t));
    now.shuffle.classList.toggle('on', player.shuffle);
    now.repeat.classList.toggle('on', player.repeat !== 'off');
    put(now.repeat, icon(player.repeat === 'one' ? 'repeatOne' : 'repeat'));
    drawProgress();
  }

  function openNowPlaying() {
    const t = current();
    if (!t) return;
    const seek = h('input', { type: 'range', class: 'seek', min: 0, max: Math.max(1, t.duration), step: 1, value: 0 });
    const els = {
      bg: h('div', { class: 'now-bg' }),
      cover: coverEl(bigCover(t.cover)),
      title: h('h2'), sub: h('div', { class: 'sub' }),
      seek, cur: h('span'), total: h('span'),
      play: h('button', { class: 'play', 'aria-label': 'Пауза', onclick: toggle }),
      like: h('span'),
      shuffle: h('button', { class: 'icon-btn mode', 'aria-label': 'Перемешать', onclick: toggleShuffle }, icon('shuffle')),
      repeat: h('button', { class: 'icon-btn mode', 'aria-label': 'Повтор', onclick: cycleRepeat }),
      dragging: false,
    };
    seek.addEventListener('input', () => { els.dragging = true; els.cur.textContent = fmtTime(seek.value); });
    seek.addEventListener('change', () => { audio.currentTime = Number(seek.value); els.dragging = false; });
    const trackNow = () => current();
    openSheet([els.bg, h('div', { class: 'now' },
      els.cover,
      h('div', { class: 'now-head' }, h('div', { class: 'meta' }, els.title, els.sub), els.like),
      seek,
      h('div', { class: 'times' }, els.cur, els.total),
      h('div', { class: 'controls' },
        els.shuffle,
        h('button', { class: 'icon-btn', 'aria-label': 'Предыдущий', onclick: () => (audio.currentTime > 5 ? (audio.currentTime = 0) : step(-1)) }, icon('prev')),
        els.play,
        h('button', { class: 'icon-btn', 'aria-label': 'Следующий', onclick: () => step(1) }, icon('next')),
        els.repeat),
      h('div', { class: 'now-actions' },
        h('button', { class: 'icon-btn', 'aria-label': 'Скачать', onclick: () => downloadTrack(trackNow()) }, icon('download')),
        h('button', { class: 'icon-btn', 'aria-label': 'В чат', onclick: () => sendTrack(trackNow()) }, icon('send')),
        h('button', { class: 'icon-btn', 'aria-label': 'Ещё', onclick: () => trackMenu(trackNow(), null, null) }, icon('more'))))],
    'player');
    now = els;
    onSheetClose = () => { now = null; };
    drawNow();
  }

  playerBar.addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]');
    if (act) { e.stopPropagation(); haptic.tap(); if (act.dataset.act === 'toggle') toggle(); else step(1); return; }
    openNowPlaying();
  });
  audio.addEventListener('timeupdate', drawProgress);
  audio.addEventListener('loadedmetadata', drawProgress);
  for (const ev of ['play', 'pause']) audio.addEventListener(ev, () => { drawBar(); if (now) drawNow(); });
  audio.addEventListener('ended', () => { if (!step(1, true)) drawBar(); });
  audio.addEventListener('error', () => { if (audio.getAttribute('src') && current()) toast('Не удалось загрузить трек'); });
  if ('mediaSession' in navigator) {
    const handlers = { play: () => audio.play(), pause: () => audio.pause(), previoustrack: () => step(-1), nexttrack: () => step(1) };
    for (const [action, fn] of Object.entries(handlers)) {
      try { navigator.mediaSession.setActionHandler(action, fn); } catch (_) { /* не поддерживается */ }
    }
  }

  // ---------- запуск ----------
  function gate(title, text, action = null, onAction = null) {
    closeSheet();
    resetNav();
    $('#tabs').hidden = true;
    document.body.classList.add('no-tabs');
    show(mountPage((root) => root.append(h('div', { class: 'gate' }, icon('music'), h('b', {}, title), h('div', {}, text),
      action ? h('button', { class: 'btn', onclick: onAction }, action) : null))), 0);
  }

  async function enterApp() {
    try {
      state.me = await api('/api/me');
    } catch (e) {
      if (e.code === 'yandex_unavailable') gate('Яндекс Музыка недоступна', e.message, 'Подключить заново', () => showLogin());
      else gate('Не удалось открыть', e.message, 'Повторить', enterApp);
      return;
    }
    if (!state.me.connected) { showLogin(); return; }
    resetNav();
    state.libraryLoaded = false;
    $('#tabs').hidden = false;
    document.body.classList.remove('no-tabs');
    setTab('library');
  }

  // fxTunnel показывает страницу-предупреждение при заходе на поддомен и после «Продолжить» помнит
  // согласие 12 часов. Это наше собственное приложение — продлеваем согласие на год.
  function extendTunnelConsent() {
    const m = location.hostname.match(/^([a-z0-9-]+)\.fxtun\.(dev|ru)$/);
    if (m) document.cookie = `_fxt_consent_${m[1]}=1; Path=/; Max-Age=31536000; SameSite=Lax; Secure`;
  }

  function boot() {
    extendTunnelConsent();
    const labels = { library: ['library', 'Медиатека'], search: ['search', 'Поиск'], upload: ['upload', 'Загрузка'] };
    document.querySelectorAll('#tabs button').forEach((b) => {
      const [ic, label] = labels[b.dataset.tab];
      put(b, h('span', { class: 'pill' }, icon(ic)), label);
      b.addEventListener('click', () => { haptic.tap(); setTab(b.dataset.tab); });
    });

    if (tg) {
      tg.ready();
      tg.expand();
      if (initData) document.documentElement.classList.add('tg');
      try {
        if (supports('6.1')) { tg.setHeaderColor('secondary_bg_color'); tg.setBackgroundColor('secondary_bg_color'); }
        if (supports('7.7')) tg.disableVerticalSwipes();
        if (supports('6.1')) tg.BackButton.onClick(onBack);
        if (supports('7.0')) { tg.SettingsButton.onClick(openAccount); tg.SettingsButton.show(); }
      } catch (_) { /* старый клиент Telegram */ }
    }

    if (!initData) {
      gate('Откройте через Telegram', 'Это мини-приложение работает внутри бота: нажмите кнопку «Медиатека» в чате с ним.');
      return;
    }
    enterApp();
  }

  boot();
})();

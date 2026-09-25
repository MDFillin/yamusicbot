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
    check: '<path d="M20 6 9 17l-5-5"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><path d="m1 1 22 22"/>',
    copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    palette: '<circle cx="13.5" cy="6.5" r="1.5"/><circle cx="17.5" cy="10.5" r="1.5"/><circle cx="8.5" cy="7.5" r="1.5"/><circle cx="6.5" cy="12.5" r="1.5"/><path d="M12 2a10 10 0 0 0 0 20c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2h2.4A5.6 5.6 0 0 0 22 10c0-4.4-4.5-8-10-8z"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    vibrate: '<rect x="7" y="4" width="10" height="16" rx="2"/><path d="M3 9v6M21 9v6"/>',
    sparkle: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 17v4M17 19h4"/>',
    headphones: '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>',
    droplet: '<path d="M12 2.7 6.3 8.4a8 8 0 1 0 11.4 0z"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    megaphone: '<path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>',
    chart: '<path d="M3 3v18h18"/><path d="M7 16v-4M11 16V8M15 16v-6M19 16V5"/>',
    alert: '<path d="m10.3 3.9-8.2 14a2 2 0 0 0 1.7 3h16.4a2 2 0 0 0 1.7-3l-8.2-14a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    ban: '<circle cx="12" cy="12" r="9"/><path d="m5.7 5.7 12.6 12.6"/>',
    server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
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

  // Адрес картинки для CSS url(""): кавычка, обратная косая или перевод строки из данных не должны менять стиль.
  const cssUrl = (url) => `url("${String(url).replace(/["\\\s]/g, encodeURIComponent)}")`;

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
    if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} ГБ`;
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

  // ---------- оформление и настройки устройства ----------
  // Хранятся в localStorage и в облаке Telegram (CloudStorage) — так они одинаковые на телефоне и компьютере.
  const PREFS_DEFAULT = { mode: 'telegram', palette: 'telegram', tint: false, haptics: true, motion: true };
  const prefs = { ...PREFS_DEFAULT, ...local.get('prefs', {}) };

  const MODES = [['telegram', 'Telegram'], ['light', 'Светлая'], ['dark', 'Тёмная'], ['amoled', 'Чёрная']];
  const PALETTES = [
    { id: 'telegram', name: 'Telegram' },
    { id: 'blue', name: 'Синий', accent: '#2f7cf6' },
    { id: 'indigo', name: 'Индиго', accent: '#5856d6' },
    { id: 'violet', name: 'Фиолет', accent: '#8e44ef' },
    { id: 'pink', name: 'Розовый', accent: '#ff2d78' },
    { id: 'red', name: 'Красный', accent: '#f2344b' },
    { id: 'orange', name: 'Оранжевый', accent: '#ff8a00' },
    { id: 'amber', name: 'Янтарь', accent: '#f5b400', text: '#1c1c1e', link: '#d99a00' },
    { id: 'green', name: 'Зелёный', accent: '#28b463' },
    { id: 'teal', name: 'Бирюза', accent: '#12a89a' },
    { id: 'cyan', name: 'Голубой', accent: '#0fb5d6' },
    { id: 'graphite', name: 'Графит', accent: '#5b6472', link: '#7b8594' },
    { id: 'mono', name: 'Монохром', accent: 'var(--text)', text: 'var(--bg)', swatch: 'linear-gradient(135deg, #111 50%, #f2f2f2 50%)' },
    { id: 'sunset', name: 'Закат', grad: ['#ff9a3d', '#ff2d78'], accent: '#ff5a5f' },
    { id: 'ocean', name: 'Океан', grad: ['#00c6ff', '#0062ff'], accent: '#1a8cff' },
    { id: 'neon', name: 'Неон', grad: ['#ff3cac', '#784ba0', '#2b86c5'], accent: '#a150b8' },
    { id: 'cosmos', name: 'Космос', grad: ['#4776e6', '#8e54e9'], accent: '#6a65e8' },
    { id: 'candy', name: 'Конфета', grad: ['#f093fb', '#f5576c'], accent: '#f0628f' },
    { id: 'fire', name: 'Пламя', grad: ['#f12711', '#f5af19'], accent: '#f35a14' },
    { id: 'mint', name: 'Мята', grad: ['#11998e', '#38ef7d'], accent: '#16a889' },
    { id: 'lavender', name: 'Лаванда', grad: ['#9d7fe0', '#e68fc7'], accent: '#a57fd9' },
    { id: 'peach', name: 'Персик', grad: ['#ff9966', '#ff5e62'], accent: '#ff7462' },
    { id: 'sky', name: 'Небо', grad: ['#56ccf2', '#2f80ed'], accent: '#3a93ee' },
    { id: 'amethyst', name: 'Аметист', grad: ['#7f00ff', '#e100ff'], accent: '#a100ff' },
    { id: 'gold', name: 'Золото', grad: ['#f7971e', '#ffd200'], accent: '#f29c1f', text: '#1c1c1e', link: '#e08a00' },
    { id: 'forest', name: 'Лес', grad: ['#3b8d27', '#8cc63f'], accent: '#4f9e2f' },
    { id: 'night', name: 'Ночь', grad: ['#232526', '#5b6470'], accent: '#4a5059', link: 'var(--text)' },
  ];
  const paletteFill = (p) => (p.swatch || (p.grad ? `linear-gradient(135deg, ${p.grad.join(', ')})` : p.accent)
    || 'var(--tg-theme-button-color, #3478f6)');

  const cloud = {
    ok: () => supports('6.9') && !!tg.CloudStorage,
    get(key) {
      return new Promise((resolve) => {
        if (!this.ok()) { resolve(null); return; }
        try { tg.CloudStorage.getItem(key, (err, value) => resolve(err ? null : value || null)); } catch (_) { resolve(null); }
      });
    },
    set(key, value) { if (this.ok()) { try { tg.CloudStorage.setItem(key, value); } catch (_) { /* не критично */ } } },
  };

  function toHex(color) {
    const m = color.match(/\d+(\.\d+)?/g);
    return m && m.length >= 3 ? `#${m.slice(0, 3).map((x) => Math.round(+x).toString(16).padStart(2, '0')).join('')}` : null;
  }

  function applyPrefs() {
    const root = document.documentElement;
    root.dataset.mode = prefs.mode;
    root.classList.toggle('tint', !!prefs.tint);
    root.classList.toggle('no-motion', !prefs.motion);
    for (const k of ['--accent', '--accent-text', '--link', '--accent-grad']) root.style.removeProperty(k);
    const p = PALETTES.find((x) => x.id === prefs.palette);
    if (p && p.accent) {
      root.style.setProperty('--accent', p.accent);
      root.style.setProperty('--accent-text', p.text || '#fff');
      root.style.setProperty('--link', p.link || p.accent);
      if (p.grad) root.style.setProperty('--accent-grad', `linear-gradient(135deg, ${p.grad.join(', ')})`);
    }
    if (!tg || !supports('6.1')) return;
    try { // шапка и фон самого Telegram — в цвет выбранной темы
      const bg = prefs.mode === 'telegram' ? 'secondary_bg_color' : toHex(getComputedStyle(document.body).backgroundColor);
      if (bg && (bg === 'secondary_bg_color' || supports('6.9'))) { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); }
      if (bg && supports('7.10') && tg.setBottomBarColor) tg.setBottomBarColor(bg);
    } catch (_) { /* старый клиент Telegram */ }
  }

  function savePrefs(patch) {
    Object.assign(prefs, patch);
    local.set('prefs', prefs);
    cloud.set('prefs', JSON.stringify(prefs));
    applyPrefs();
  }

  async function loadCloudPrefs() {
    try {
      const saved = JSON.parse((await cloud.get('prefs')) || 'null');
      if (saved && JSON.stringify({ ...prefs, ...saved }) !== JSON.stringify(prefs)) {
        Object.assign(prefs, saved);
        local.set('prefs', prefs);
        applyPrefs();
      }
    } catch (_) { /* нет облака — живём с локальными */ }
  }

  // ---------- связь с Telegram ----------
  const haptic = {
    tap: () => { if (prefs.haptics) try { tg.HapticFeedback.impactOccurred('light'); } catch (_) { /* нет в браузере */ } },
    ok: () => { if (prefs.haptics) try { tg.HapticFeedback.notificationOccurred('success'); } catch (_) { /* нет в браузере */ } },
    err: () => { if (prefs.haptics) try { tg.HapticFeedback.notificationOccurred('error'); } catch (_) { /* нет в браузере */ } },
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

  function promptSheet(title, placeholder, value, button, onSubmit, { allowEmpty = false, maxLength = 100, multiline = false } = {}) {
    const input = multiline
      ? h('textarea', { class: 'text area', placeholder, maxLength, rows: 5 })
      : h('input', { class: 'text', placeholder, value, maxLength, enterKeyHint: 'done' });
    if (multiline) input.value = value;
    const btn = h('button', { class: 'btn block' }, button);
    const submit = async () => {
      const text = input.value.trim();
      if (!text && !allowEmpty) { input.focus(); return; }
      btn.disabled = true;
      try { await onSubmit(text); closeSheet(); } catch (e) { fail(e); } finally { btn.disabled = false; }
    };
    btn.addEventListener('click', submit);
    if (!multiline) input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
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

  function openSettings() {
    if (!state.me || !state.me.connected) return;
    if (state.tab !== 'library') setTab('library');
    if (top() && top().kind === 'settings') return;
    push(settingsView);
  }

  const group = (title, ...children) => h('section', { class: 'set-group' }, title ? h('h3', {}, title) : null, ...children);

  function toggleRow(ic, title, sub, value, onChange) {
    const input = h('input', { type: 'checkbox', checked: value });
    input.addEventListener('change', () => { onChange(input.checked); haptic.tap(); });
    return h('label', { class: 'row set-row' }, h('span', { class: 'set-icon' }, icon(ic, 'sm')),
      h('div', { class: 'meta' }, h('div', { class: 'title' }, title), sub ? h('div', { class: 'sub' }, sub) : null),
      h('span', { class: 'switch' }, input, h('span')));
  }

  function segmented(options, value, onPick) {
    const box = h('div', { class: 'seg-full' });
    const draw = (current) => put(box, options.map(([v, label, sub]) => h('button', {
      class: v === current ? 'on' : '',
      onclick: async () => { if (v === current) return; haptic.tap(); draw(v); if ((await onPick(v)) === false) draw(current); },
    }, h('b', {}, label), sub ? h('span', {}, sub) : null)));
    draw(value);
    return box;
  }

  function settingsView(root, alive, entry) {
    entry.kind = 'settings';
    const me = state.me;
    const name = tgUser ? [tgUser.first_name, tgUser.last_name].filter(Boolean).join(' ') : 'Вы';

    // Аккаунт
    const account = group('Аккаунт',
      h('div', { class: 'card' },
        h('div', { class: 'account-card' }, avatarEl('lg'),
          h('div', { class: 'meta' }, h('div', { class: 'title' }, name), h('div', { class: 'sub' }, `Яндекс: ${me.login || '—'}`)),
          me.has_plus ? h('span', { class: 'badge plus' }, 'Плюс') : h('span', { class: 'badge' }, 'без Плюса')),
        h('div', { class: 'list' },
          h('button', { class: 'row set-row', onclick: () => showLogin(true) }, h('span', { class: 'set-icon' }, icon('refresh', 'sm')),
            h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Сменить аккаунт Яндекса')), icon('chevron', 'sm')),
          h('button', { class: 'row set-row danger', onclick: logout }, h('span', { class: 'set-icon' }, icon('logout', 'sm')),
            h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Отключить аккаунт'))))));

    // Токен
    let token = null;
    let shown = false;
    const tokenText = h('code', { class: 'token masked' }, '•'.repeat(24));
    const eyeBtn = h('button', { class: 'btn secondary sm' });
    const drawEye = () => put(eyeBtn, icon(shown ? 'eyeOff' : 'eye', 'sm'), shown ? 'Скрыть' : 'Показать');
    const getToken = async () => token || (token = (await api('/api/token')).token);
    eyeBtn.addEventListener('click', async () => {
      if (!shown && !(await confirmAsk('Токен даёт полный доступ к вашей Яндекс Музыке. Показать его на экране?'))) return;
      try {
        const t = await getToken();
        shown = !shown;
        tokenText.textContent = shown ? t : '•'.repeat(24);
        tokenText.classList.toggle('masked', !shown);
        drawEye();
      } catch (e) { fail(e); }
    });
    drawEye();
    const tokenBox = group('Токен Яндекс Музыки',
      h('div', { class: 'card token-card' }, tokenText,
        h('div', { class: 'token-actions' }, eyeBtn,
          h('button', { class: 'btn secondary sm', onclick: async () => { try { await copyText(await getToken()); } catch (e) { fail(e); } } },
            icon('copy', 'sm'), 'Скопировать'))),
      h('div', { class: 'set-note' }, '⚠️ С этим токеном можно управлять вашей Яндекс Музыкой. Никому его не отправляйте — '
        + 'даже тем, кто представляется поддержкой. Отозвать токен можно, выйдя на всех устройствах в Яндекс ID.'));

    // Оформление
    const swatches = h('div', { class: 'swatches' });
    const grads = h('div', { class: 'swatches' });
    const drawSwatches = () => {
      const make = (p) => h('button', { class: `swatch${prefs.palette === p.id ? ' on' : ''}`, 'aria-label': p.name,
        onclick: () => { savePrefs({ palette: p.id }); haptic.tap(); drawSwatches(); } },
      h('span', { class: 'dot', style: `background:${paletteFill(p)}` }, prefs.palette === p.id ? icon('check', 'sm') : null),
      h('span', { class: 'name' }, p.name));
      put(swatches, PALETTES.filter((p) => !p.grad).map(make));
      put(grads, PALETTES.filter((p) => p.grad).map(make));
    };
    drawSwatches();
    const look = group('Оформление',
      h('div', { class: 'card set-card' },
        h('div', { class: 'set-label' }, icon('moon', 'sm'), 'Тема'),
        segmented(MODES.map(([v, label]) => [v, label]), prefs.mode, (v) => { savePrefs({ mode: v }); }),
        h('div', { class: 'set-label' }, icon('droplet', 'sm'), 'Однотонные'), swatches,
        h('div', { class: 'set-label' }, icon('palette', 'sm'), 'Градиенты'), grads),
      h('div', { class: 'card list', style: 'margin-top:10px' },
        toggleRow('sparkle', 'Цветной фон', 'Мягкая подсветка в цвет палитры', prefs.tint, (v) => savePrefs({ tint: v }))));

    // Качество
    const qualities = (me.settings && me.settings.qualities) || [{ kbps: 320, label: 'лучшее' }];
    const qualityOptions = qualities.map((q) => [q.kbps, `${q.kbps}`, q.label]);
    const setQuality = (key) => async (v) => {
      try {
        me.settings = await api('/api/settings', { method: 'PUT', body: { [key]: v } });
        toast(`Качество: ${v} kbps`);
        return true;
      } catch (e) { fail(e); return false; }
    };
    const quality = group('Качество',
      h('div', { class: 'card set-card' },
        h('div', { class: 'set-label' }, icon('download', 'sm'), 'Скачивание и отправка в чат'),
        segmented(qualityOptions, me.settings ? me.settings.download_quality : 320, setQuality('download_quality')),
        h('div', { class: 'set-label' }, icon('headphones', 'sm'), 'Прослушивание в плеере'),
        segmented(qualityOptions, me.settings ? me.settings.stream_quality : 320, setQuality('stream_quality'))),
      h('div', { class: 'set-note' }, 'kbps — чем больше, тем лучше звук и тяжелее файл. 320 доступно с Яндекс Плюсом; '
        + 'меньшее качество экономит мобильный интернет.'));

    // Прочее
    const misc = group('Приложение',
      h('div', { class: 'card list' },
        toggleRow('vibrate', 'Вибрация', 'Лёгкий отклик на нажатия', prefs.haptics, (v) => savePrefs({ haptics: v })),
        toggleRow('sparkle', 'Анимации', 'Плавные переходы и эффекты', prefs.motion, (v) => savePrefs({ motion: v })),
        h('button', { class: 'row set-row', onclick: () => { local.set('recent', []); haptic.ok(); toast('История поиска очищена'); } },
          h('span', { class: 'set-icon' }, icon('clock', 'sm')), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Очистить историю поиска')))));

    const adminBox = isAdmin() ? group('Владелец', h('div', { class: 'card list' },
      h('button', { class: 'row set-row', onclick: openAdmin }, h('span', { class: 'set-icon' }, icon('shield', 'sm')),
        h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Админ-панель'), h('div', { class: 'sub' }, 'Пользователи, статистика, рассылка, режимы')),
        icon('chevron', 'sm')))) : null;

    root.append(
      h('div', { class: 'lib-head' }, h('div', {}, h('div', { class: 'hello' }, 'Медиатека'), h('h1', { class: 'page-title' }, 'Настройки'))),
      adminBox, account, tokenBox, look, quality, misc);
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

  // ---------- админ-панель (только для ADMIN_IDS; сервер проверяет это сам, здесь — лишь интерфейс) ----------
  const isAdmin = () => !!(state.me && state.me.is_admin);
  const isOwner = () => !!(state.me && state.me.is_owner);

  function openAdmin() {
    if (!isAdmin()) return;
    if (state.tab !== 'library') setTab('library');
    if (top() && top().kind === 'admin') return;
    push(adminView);
  }

  const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString('ru-RU'));
  function fmtAgo(ts) {
    if (!ts) return '—';
    const d = Math.floor(Date.now() / 1000 - ts);
    if (d < 120) return 'только что';
    if (d < 3600) return `${Math.floor(d / 60)} мин назад`;
    if (d < 86400) return `${Math.floor(d / 3600)} ч назад`;
    if (d < 7 * 86400) return `${Math.floor(d / 86400)} дн назад`;
    return new Date(ts * 1000).toLocaleDateString('ru-RU');
  }
  const fmtStamp = (ts) => new Date(ts * 1000).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  function fmtUptime(sec) {
    const d = Math.floor(sec / 86400); const hh = Math.floor((sec % 86400) / 3600); const mm = Math.floor((sec % 3600) / 60);
    return d ? `${d} д ${hh} ч` : hh ? `${hh} ч ${mm} мин` : `${mm} мин`;
  }
  const userName = (u) => [u.first_name, u.last_name].filter(Boolean).join(' ') || `ID ${u.id}`;

  function statTile(ic, label, value, sub, cls = '') {
    return h('div', { class: `stat ${cls}` }, h('div', { class: 'stat-label' }, icon(ic, 'sm'), label),
      h('div', { class: 'stat-value' }, value), sub ? h('div', { class: 'stat-sub' }, sub) : null);
  }

  // Столбики по дням: одна метрика за раз, подпись значения — по нажатию на столбик.
  function barChart(series, key, title) {
    const W = 336; const H = 132; const padB = 18; const padT = 14;
    const max = Math.max(1, ...series.map((d) => d[key]));
    const step = W / series.length; const bw = Math.max(4, step - 4);
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('class', 'bars');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `${title}: ${series.map((d) => `${d.day.slice(5)} — ${d[key]}`).join(', ')}`);
    const el = (tag, attrs) => { const n = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); return n; };
    svg.append(el('line', { x1: 0, x2: W, y1: H - padB + 0.5, y2: H - padB + 0.5, class: 'axis' }));
    const tip = h('div', { class: 'bar-tip' });
    const pick = (i, bar) => {
      svg.querySelectorAll('.bar').forEach((b) => b.classList.remove('on'));
      bar.classList.add('on');
      const d = series[i];
      tip.textContent = `${new Date(`${d.day}T12:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}: ${fmtNum(d[key])}`;
    };
    series.forEach((d, i) => {
      const hgt = d[key] ? Math.max(3, ((H - padB - padT) * d[key]) / max) : 0;
      const x = i * step + (step - bw) / 2; const y = H - padB - hgt;
      // Скругление только сверху: нижний край стоит на оси.
      const r = Math.min(4, bw / 2, hgt);
      const path = hgt ? `M${x},${H - padB} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${H - padB} Z` : '';
      const bar = el('path', { d: path, class: 'bar' });
      const hit = el('rect', { x: i * step, y: 0, width: step, height: H, fill: 'transparent' });
      hit.addEventListener('click', () => { haptic.tap(); pick(i, bar); });
      hit.addEventListener('mouseenter', () => pick(i, bar));
      svg.append(bar, hit);
      if (i === 0 || i === series.length - 1 || i === Math.floor(series.length / 2)) {
        const t = el('text', { x: x + bw / 2, y: H - 4, class: 'tick' });
        t.textContent = d.day.slice(8) + '.' + d.day.slice(5, 7);
        svg.append(t);
      }
    });
    const last = series.length - 1;
    pick(last, svg.querySelectorAll('.bar')[last]);
    return h('div', { class: 'chart' }, svg, tip);
  }

  function adminView(root, alive, entry) {
    entry.kind = 'admin';
    const tabs = [['overview', 'Обзор'], ['users', 'Люди'], ['broadcast', 'Рассылка'], ['settings', 'Режимы'], ['journal', 'Журнал']];
    let tab = 'overview';
    const bar = h('div', { class: 'segments adm-tabs' });
    const body = h('div', { class: 'adm-body' });
    let timer = null;
    const drawTabs = () => put(bar, tabs.map(([k, label]) => h('button', {
      class: k === tab ? 'on' : '', onclick: () => { if (k !== tab) { haptic.tap(); tab = k; drawTabs(); render(); } },
    }, label)));
    const render = () => {
      clearInterval(timer);
      put(body, h('div', { class: 'empty' }, spinner()));
      ({ overview, users, broadcast, settings, journal })[tab]().catch((e) => {
        if (!alive()) return;
        if (e.status === 404) { // права сняли, пока панель была открыта
          state.me.is_admin = false;
          put(body, emptyEl('lock', 'У вас больше нет доступа к админ-панели'));
        } else put(body, emptyEl('alert', e.message));
      });
    };
    entry.onShow = () => { if (tab === 'broadcast' || tab === 'overview') render(); };
    root.append(h('div', { class: 'lib-head' }, h('div', {},
      h('div', { class: 'hello' }, isOwner() ? 'Вы владелец бота' : 'Вы администратор'), h('h1', { class: 'page-title' }, 'Админ-панель')),
    h('span', { class: 'gear-btn adm-shield' }, icon('shield'))), bar, body);
    drawTabs();
    render();

    // ----- обзор -----
    let metric = 'active';
    async function overview() {
      const data = await api('/api/admin/overview');
      if (!alive() || tab !== 'overview') return;
      const { stats: st, server: sv, settings: cfg, broadcast: bc } = data;
      const u = st.users;
      const banners = [];
      if (cfg.maintenance) banners.push(h('div', { class: 'adm-banner warn' }, icon('alert', 'sm'), 'Включены техработы — бот отвечает только вам'));
      if (cfg.closed) banners.push(h('div', { class: 'adm-banner' }, icon('lock', 'sm'), 'Регистрация закрыта — новые пользователи не допускаются'));
      if (bc.state === 'running') banners.push(h('div', { class: 'adm-banner' }, icon('megaphone', 'sm'), `Идёт рассылка: ${bc.sent} из ${bc.total}`));
      if (sv.inline === false) banners.push(h('div', { class: 'adm-banner warn' }, icon('alert', 'sm'), 'Инлайн-режим выключен: @BotFather → /setinline и /setinlinefeedback'));
      const metrics = [['active', 'Активные', 'Активные пользователи по дням'], ['new', 'Новые', 'Новые пользователи по дням'],
        ['downloads', 'Скачивания', 'Скачанные треки по дням'], ['uploads', 'Загрузки', 'Загруженные треки по дням']];
      const chartBox = h('div');
      const drawChart = () => { const m = metrics.find((x) => x[0] === metric); put(chartBox, h('div', { class: 'chart-title' }, m[2]), barChart(st.series, metric, m[2])); };
      drawChart();
      const topList = st.top.length ? h('div', { class: 'card list' }, st.top.map((t, i) => h('button', { class: 'row', onclick: () => userSheet(t.id) },
        h('span', { class: 'rank' }, String(i + 1)), h('div', { class: 'meta' }, h('div', { class: 'title' }, t.name)),
        h('span', { class: 'num' }, fmtNum(t.downloads))))) : h('div', { class: 'set-note' }, 'За неделю никто ничего не скачивал.');
      const kv = (k, v) => h('div', { class: 'kv' }, h('span', {}, k), h('b', {}, v));
      const backupBtn = h('button', { class: 'btn secondary block', onclick: async () => {
        if (!(await confirmAsk('Отправить копию базы вам в чат с ботом? В ней входы всех пользователей — храните её надёжно.'))) return;
        backupBtn.disabled = true;
        try { const r = await api('/api/admin/backup', { method: 'POST' }); haptic.ok(); toast(`Копия (${fmtSize(r.size)}) отправлена в чат`); } catch (e) { fail(e); } finally { backupBtn.disabled = false; }
      } }, icon('save', 'sm'), 'Бэкап базы в чат');
      put(body, ...banners,
        h('div', { class: 'stat-grid' },
          statTile('user', 'Пользователи', fmtNum(u.total), `+${u.new_today} сегодня · +${u.new_week} за неделю`),
          statTile('key', 'С Яндексом', fmtNum(u.connected), u.total ? `${Math.round((100 * u.connected) / u.total)}% от всех` : ''),
          statTile('sparkle', 'Активны сегодня', fmtNum(u.active_today), `${u.active_week} за неделю · ${u.active_month} за месяц`),
          statTile('download', 'Скачано сегодня', fmtNum(st.downloads.today), `всего ${fmtNum(st.downloads.total)}`),
          statTile('upload', 'Загружено сегодня', fmtNum(st.uploads.today), `всего ${fmtNum(st.uploads.total)}`),
          statTile('alert', 'Ошибок за сутки', fmtNum(st.errors_24h), `⛔ ${u.banned} забанено · 🚫 ${u.blocked} ушли`, st.errors_24h ? 'warn' : '')),
        group('Динамика за 14 дней', h('div', { class: 'card set-card' },
          segmented(metrics.map(([k, label]) => [k, label]), metric, (v) => { metric = v; drawChart(); }), chartBox)),
        group('Больше всех скачали за неделю', topList),
        group('Сервер', h('div', { class: 'card set-card kvs' },
          kv('Работает', fmtUptime(sv.uptime)), kv('Память', sv.memory_mb ? `${sv.memory_mb} МБ` : '—'),
          kv('База', fmtSize(sv.db_bytes)), kv('Свободно на диске', `${fmtSize(sv.disk_free)} из ${fmtSize(sv.disk_total)}`),
          kv('Клиентов Яндекса в памяти', fmtNum(sv.clients)), kv('ffmpeg', sv.ffmpeg || 'не установлен'),
          kv('Python / aiogram', `${sv.python} / ${sv.aiogram}`), kv('Бот', sv.bot_username ? `@${sv.bot_username}` : '—'),
          kv('Инлайн-режим', sv.inline ? 'включён' : sv.inline === false ? 'выключен в @BotFather' : '—'),
          kv('Владельцы (.env)', String(sv.owners.length)), kv('Назначенные админы', String(sv.admins.length))),
        isOwner() ? backupBtn : null));
    }

    // ----- пользователи -----
    let query = ''; let filter = 'all';
    async function users() {
      const input = h('input', { type: 'search', placeholder: 'Имя, @username, ID или логин Яндекса', value: query, enterKeyHint: 'search' });
      const list = h('div', { class: 'card list' });
      const more = h('div');
      const count = h('div', { class: 'set-note' });
      const chips = h('div', { class: 'segments' });
      const filters = [['all', 'Все'], ['connected', 'С Яндексом'], ['admins', 'Админы'], ['banned', 'Заблокированы'], ['blocked', 'Ушли']];
      const drawChips = () => put(chips, filters.map(([k, label]) => h('button', { class: k === filter ? 'on' : '',
        onclick: () => { filter = k; drawChips(); load(0); } }, label)));
      let seq = 0;
      async function load(offset) {
        const my = ++seq;
        if (!offset) put(list, skRows(4));
        const data = await api(`/api/admin/users?q=${encodeURIComponent(query)}&status=${filter}&offset=${offset}`);
        if (!alive() || my !== seq) return;
        const rows = data.users.map(userRow);
        if (offset) list.append(...rows); else put(list, rows.length ? rows : h('div', { class: 'empty' }, 'Никого не нашлось'));
        count.textContent = `Найдено: ${fmtNum(data.total)}`;
        const shown = offset + data.users.length;
        put(more, shown < data.total ? h('button', { class: 'link-btn center', onclick: () => load(shown).catch(fail) }, 'Показать ещё') : null);
      }
      let debounce;
      input.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(() => { query = input.value.trim(); load(0).catch(fail); }, 300); });
      drawChips();
      put(body, h('div', { class: 'search-field' }, icon('search', 'sm'), input), chips, count, list, more);
      await load(0);
    }

    function userRow(u) {
      const badges = [];
      if (u.is_owner) badges.push(h('span', { class: 'badge plus' }, 'владелец'));
      else if (u.is_admin) badges.push(h('span', { class: 'badge plus' }, 'админ'));
      if (u.banned) badges.push(h('span', { class: 'badge bad' }, 'бан'));
      if (u.blocked_bot) badges.push(h('span', { class: 'badge' }, 'ушёл'));
      const hue = (u.id * 47) % 360;
      return h('button', { class: 'row', onclick: () => userSheet(u.id) },
        h('div', { class: 'avatar', style: `--a1:hsl(${hue} 70% 62%);--a2:hsl(${(hue + 40) % 360} 75% 52%)` }, userName(u).charAt(0).toUpperCase()),
        h('div', { class: 'meta' }, h('div', { class: 'title' }, userName(u), u.username ? h('span', { class: 'muted' }, ` @${u.username}`) : null),
          h('div', { class: 'sub' }, `${u.connected ? `Яндекс: ${u.yandex_login || 'подключён'}` : 'без Яндекса'} · ${fmtAgo(u.last_seen)}`)),
        ...badges);
    }

    async function userSheet(id) {
      let u;
      try { u = await api(`/api/admin/users/${id}`); } catch (e) { fail(e); return; }
      const act = (action, body, note) => async () => {
        try {
          u = await api(`/api/admin/users/${id}/${action}`, { method: 'POST', body: body || {} });
          haptic.ok(); toast(note); draw();
          if (tab === 'users') users().catch(fail);
          if (tab === 'settings') settings().catch(fail);
        } catch (e) { fail(e); }
      };
      const draw = () => {
        const limits = [u.downloads_left != null ? `скачиваний осталось ${u.downloads_left}` : null,
          u.uploads_left != null ? `загрузок осталось ${u.uploads_left}` : null].filter(Boolean).join(' · ');
        openSheet([
          h('div', { class: 'sheet-title' }, userName(u)),
          h('div', { class: 'card set-card kvs' },
            h('div', { class: 'kv' }, h('span', {}, 'Telegram ID'), h('b', {}, String(u.id))),
            u.username ? h('div', { class: 'kv' }, h('span', {}, 'Username'), h('b', {}, `@${u.username}`)) : null,
            h('div', { class: 'kv' }, h('span', {}, 'Яндекс'), h('b', {}, u.connected ? (u.yandex_login || 'подключён') : 'не подключён')),
            h('div', { class: 'kv' }, h('span', {}, 'Впервые'), h('b', {}, fmtAgo(u.first_seen))),
            h('div', { class: 'kv' }, h('span', {}, 'Был'), h('b', {}, fmtAgo(u.last_seen))),
            h('div', { class: 'kv' }, h('span', {}, 'Скачал / загрузил'), h('b', {}, `${fmtNum(u.downloads)} / ${fmtNum(u.uploads)}`)),
            limits ? h('div', { class: 'kv' }, h('span', {}, 'Сегодня'), h('b', {}, limits)) : null,
            u.is_admin ? h('div', { class: 'kv' }, h('span', {}, 'Роль'),
              h('b', {}, u.is_owner ? '👑 владелец (.env)' : `🛡 админ${u.admin_granted_at ? ` с ${new Date(u.admin_granted_at * 1000).toLocaleDateString('ru-RU')}` : ''}`)) : null,
            u.banned ? h('div', { class: 'kv bad' }, h('span', {}, 'Заблокирован'), h('b', {}, u.ban_reason || 'без причины')) : null,
            u.blocked_bot ? h('div', { class: 'kv' }, h('span', {}, 'Статус'), h('b', {}, 'заблокировал бота')) : null),
          h('button', { class: 'row', onclick: () => copyText(String(u.id)) }, icon('copy'), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Скопировать ID'))),
          h('button', { class: 'row', onclick: () => promptSheet('Сообщение пользователю', 'Текст (можно HTML: <b>, <i>, <a>)', '', 'Отправить',
            async (text) => { u = await api(`/api/admin/users/${id}/message`, { method: 'POST', body: { text } }); haptic.ok(); toast('Сообщение отправлено'); },
            { maxLength: 4000, multiline: true }) },
          icon('send'), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Написать от имени бота'))),
          h('button', { class: 'row', onclick: act('reset', null, 'Лимиты на сегодня сброшены') }, icon('refresh'),
            h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Сбросить лимиты на сегодня'))),
          isOwner() && !u.is_owner ? (u.is_admin
            ? h('button', { class: 'row danger', onclick: async () => {
              if (await confirmAsk(`Снять права админа у «${userName(u)}»? Админ-панель у него сразу пропадёт.`)) await act('revoke_admin', null, 'Права админа сняты')();
            } }, icon('shield'), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Снять права админа')))
            : h('button', { class: 'row', onclick: async () => {
              if (await confirmAsk(`Назначить «${userName(u)}» админом? Ему откроется админ-панель: пользователи, блокировки, `
                + 'рассылка, режимы и журнал. Назначать админов и делать бэкап базы сможете только вы.')) await act('grant_admin', null, 'Назначен админом')();
            } }, icon('shield'), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Сделать админом'),
              h('div', { class: 'sub' }, 'Доступ к админ-панели, кроме бэкапа и назначения админов')))) : null,
          u.connected && (!u.is_admin || isOwner() || u.id === (tgUser && tgUser.id)) ? h('button', { class: 'row danger', onclick: async () => {
            if (await confirmAsk('Отключить аккаунт Яндекса у этого пользователя? Он сможет подключить его снова.')) await act('disconnect', null, 'Яндекс отключён')();
          } }, icon('logout'), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Отключить его Яндекс'))) : null,
          u.is_admin ? null : u.banned
            ? h('button', { class: 'row', onclick: act('unban', null, 'Разблокирован') }, icon('check'), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Разблокировать')))
            : h('button', { class: 'row danger', onclick: () => promptSheet('Заблокировать?', 'Причина — например, спам (необязательно)', '', 'Заблокировать',
              async (reason) => { await act('ban', { reason }, 'Заблокирован')(); }, { allowEmpty: true, maxLength: 200 }) },
            icon('ban'), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Заблокировать'))),
        ]);
      };
      draw();
    }

    // ----- рассылка -----
    let draft = ''; let audience = 'all';
    async function broadcast() {
      const st = await api('/api/admin/broadcast');
      if (!alive() || tab !== 'broadcast') return;
      const statusBox = h('div');
      const drawStatus = (s) => {
        if (s.state === 'idle') { put(statusBox); return; }
        const titles = { running: 'Рассылка идёт', done: 'Рассылка завершена', cancelled: 'Рассылка остановлена', failed: 'Рассылка прервалась' };
        const pct = s.total ? Math.round((100 * (s.sent + s.failed + s.blocked)) / s.total) : 100;
        put(statusBox, group(titles[s.state] || 'Рассылка', h('div', { class: 'card set-card' },
          h('div', { class: 'progress' }, h('div', { style: `width:${pct}%` })),
          h('div', { class: 'kvs' },
            h('div', { class: 'kv' }, h('span', {}, 'Доставлено'), h('b', {}, `${fmtNum(s.sent)} из ${fmtNum(s.total)}`)),
            h('div', { class: 'kv' }, h('span', {}, 'Заблокировали бота'), h('b', {}, fmtNum(s.blocked))),
            h('div', { class: 'kv' }, h('span', {}, 'Ошибок'), h('b', {}, fmtNum(s.failed))),
            s.error ? h('div', { class: 'kv bad' }, h('span', {}, 'Причина'), h('b', {}, s.error)) : null),
          h('div', { class: 'set-note' }, `Текст: ${s.preview || ''}`),
          s.state === 'running' ? h('button', { class: 'btn secondary block', onclick: async () => {
            try { drawStatus(await api('/api/admin/broadcast', { method: 'DELETE' })); toast('Останавливаю…'); } catch (e) { fail(e); }
          } }, icon('x', 'sm'), 'Остановить') : null)));
      };
      drawStatus(st);
      const poll = () => { timer = setInterval(async () => {
        if (!alive() || tab !== 'broadcast') { clearInterval(timer); return; }
        try { const s = await api('/api/admin/broadcast'); drawStatus(s); if (s.state !== 'running') clearInterval(timer); } catch (_) { clearInterval(timer); }
      }, 2000); };
      if (st.state === 'running') poll();

      const text = h('textarea', { class: 'text area', rows: 6, maxLength: 4000, placeholder: 'Текст сообщения. Можно <b>жирный</b>, <i>курсив</i>, <a href="https://…">ссылку</a>' });
      text.value = draft;
      text.addEventListener('input', () => { draft = text.value; });
      const a = st.audiences;
      const aud = segmented(Object.entries(a).map(([k, v]) => [k, (k === 'all' ? 'Всем' : 'С Яндексом'), `${v.count} чел.`]), audience, (v) => { audience = v; });
      const testBtn = h('button', { class: 'btn secondary', onclick: async () => {
        if (!text.value.trim()) { text.focus(); return; }
        try { await api('/api/admin/broadcast', { method: 'POST', body: { text: text.value, test: true } }); haptic.ok(); toast('Отправил вам — проверьте, как выглядит'); } catch (e) { fail(e); }
      } }, icon('user', 'sm'), 'Себе');
      const sendBtn = h('button', { class: 'btn', onclick: async () => {
        if (!text.value.trim()) { text.focus(); return; }
        const n = a[audience].count;
        if (!(await confirmAsk(`Разослать сообщение ${n} ${plural(n, 'пользователю', 'пользователям', 'пользователям')}? Отменить уже отправленное нельзя.`))) return;
        sendBtn.disabled = true;
        try {
          drawStatus(await api('/api/admin/broadcast', { method: 'POST', body: { text: text.value, audience } }));
          draft = ''; text.value = ''; haptic.ok(); toast('Рассылка запущена'); poll();
        } catch (e) { fail(e); } finally { sendBtn.disabled = false; }
      } }, icon('megaphone', 'sm'), 'Разослать');
      put(body, statusBox,
        group('Новая рассылка', h('div', { class: 'card set-card' }, text,
          h('div', { class: 'set-label' }, icon('user', 'sm'), 'Кому'), aud,
          h('div', { class: 'btn-row' }, testBtn, sendBtn)),
        h('div', { class: 'set-note' }, 'Сначала отправьте себе и проверьте, как выглядит. Рассылка идёт в фоне '
          + '(около 20 сообщений в секунду); заблокированным и ушедшим она не отправляется.')));
    }

    // ----- режимы -----
    async function settings() {
      const [{ settings: cfg }, team] = await Promise.all([api('/api/admin/overview'), api('/api/admin/admins')]);
      if (!alive() || tab !== 'settings') return;
      const save = async (patch, note) => {
        try { Object.assign(cfg, await api('/api/admin/settings', { method: 'PUT', body: patch })); haptic.ok(); if (note) toast(note); return true; } catch (e) { fail(e); return false; }
      };
      const mText = h('textarea', { class: 'text area', rows: 2, maxLength: 500, placeholder: 'Бот на техническом обслуживании. Загляните чуть позже.' });
      mText.value = cfg.maintenance_text || '';
      mText.addEventListener('change', () => save({ maintenance_text: mText.value }, 'Текст сохранён'));
      const num = (value) => h('input', { class: 'text num-input', type: 'number', min: 0, max: 100000, inputMode: 'numeric', value: String(value || 0) });
      const dl = num(cfg.download_limit); const ul = num(cfg.upload_limit);
      const saveLimits = h('button', { class: 'btn block', onclick: () => save({ download_limit: Number(dl.value || 0), upload_limit: Number(ul.value || 0) }, 'Лимиты сохранены') },
        icon('check', 'sm'), 'Сохранить лимиты');
      put(body,
        group('Доступ', h('div', { class: 'card list' },
          toggleRow('alert', 'Техработы', 'Бот и приложение отвечают только админам', cfg.maintenance, (v) => save({ maintenance: v }, v ? 'Техработы включены' : 'Техработы выключены')),
          toggleRow('lock', 'Закрыть регистрацию', cfg.closed ? `Закрыта с ${fmtStamp(cfg.closed_since)} — новые не допускаются` : 'Новые пользователи не смогут пользоваться ботом',
            cfg.closed, (v) => save({ closed: v }, v ? 'Регистрация закрыта' : 'Регистрация открыта'))),
        h('div', { class: 'card set-card', style: 'margin-top:10px' }, h('div', { class: 'set-label' }, icon('edit', 'sm'), 'Текст на время техработ'), mText)),
        group('Лимиты на человека в сутки', h('div', { class: 'card set-card' },
          h('label', { class: 'field-row' }, h('span', {}, 'Скачиваний треков'), dl),
          h('label', { class: 'field-row' }, h('span', {}, 'Загрузок файлов'), ul), saveLimits),
        h('div', { class: 'set-note' }, '0 — без ограничений. Сутки считаются по Москве. На админов лимиты не действуют; '
          + 'сбросить лимит конкретному человеку можно в его карточке.')),
        group('Администраторы', h('div', { class: 'card list' }, team.admins.map((a) => h('button', { class: 'row', onclick: () => userSheet(a.id) },
          h('span', { class: 'set-icon' }, icon('shield', 'sm')),
          h('div', { class: 'meta' }, h('div', { class: 'title' }, a.name),
            h('div', { class: 'sub' }, a.owner ? 'владелец · задан в .env' : `админ с ${new Date(a.granted_at * 1000).toLocaleDateString('ru-RU')}`)),
          a.owner ? h('span', { class: 'badge plus' }, 'владелец') : icon('chevron', 'sm')))),
        h('div', { class: 'set-note' }, team.can_manage
          ? 'Чтобы назначить админа, откройте человека на вкладке «Люди» → «Сделать админом». Снять права — там же. '
            + 'Владельцы задаются только в .env на сервере.'
          : 'Назначать и снимать админов может только владелец бота.')));
    }

    // ----- журнал -----
    let journalKind = 'audit'; let errQuery = '';
    async function journal() {
      const box = h('div');
      const seg = segmented([['audit', 'Действия'], ['errors', 'Ошибки']], journalKind, (v) => { journalKind = v; load(); });
      async function load() {
        put(box, h('div', { class: 'empty' }, spinner()));
        if (journalKind === 'audit') {
          const { entries } = await api('/api/admin/audit');
          if (!alive()) return;
          put(box, entries.length ? h('div', { class: 'card list' }, entries.map((e) => h('div', { class: `row log-row${e.action === 'denied' ? ' bad' : ''}` },
            h('div', { class: 'meta' }, h('div', { class: 'title' }, `${e.actor_name || e.actor}: ${e.label}${e.target && e.target !== e.actor ? ` ${e.target_name}` : ''}`),
              h('div', { class: 'sub' }, fmtStamp(e.at) + (e.details ? ` · ${e.details}` : '')))))) : emptyEl('list', 'Журнал пуст'));
        } else {
          const input = h('input', { type: 'search', placeholder: 'Код ошибки из сообщения, например a1b2c3', value: errQuery });
          const list = h('div');
          const draw = async () => {
            const { entries } = await api(`/api/admin/errors?q=${encodeURIComponent(errQuery)}`);
            if (!alive()) return;
            put(list, entries.length ? entries.map((e) => {
              const [first, ...rest] = e.message.split('\n');
              return h('details', { class: `card log ${e.level.toLowerCase()}` },
                h('summary', {}, h('b', {}, `${fmtStamp(e.at)} · ${e.level}`), h('span', {}, first)),
                rest.length ? h('pre', {}, rest.join('\n')) : null);
            }) : emptyEl('check', errQuery ? 'Ничего не нашлось' : 'Ошибок нет'));
          };
          let t;
          input.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { errQuery = input.value.trim(); draw().catch(fail); }, 300); });
          put(box, h('div', { class: 'search-field' }, icon('search', 'sm'), input),
            h('div', { class: 'set-note' }, 'Последние 300 предупреждений и ошибок с момента запуска бота.'), list);
          await draw();
        }
      }
      put(body, seg, box);
      await load();
    }
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
    const gear = h('button', { class: 'gear-btn', 'aria-label': 'Настройки', onclick: () => { haptic.tap(); openSettings(); } }, icon('gear'));
    const shield = isAdmin() ? h('button', { class: 'gear-btn', 'aria-label': 'Админ-панель', onclick: () => { haptic.tap(); openAdmin(); } }, icon('shield')) : null;
    const chip = h('button', { class: 'chip', onclick: openSettings }, icon('user', 'sm'), state.me.login || 'Аккаунт Яндекса',
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
        h('div', { class: 'head-btns' }, shield, gear)),
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
          info.cover ? h('div', { class: 'hero-bg' }, h('div', { style: `background-image:${cssUrl(info.cover)}` })) : '',
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

  // ---------- теги аудиофайла (MP3 ID3v2 и FLAC) — читаем прямо на телефоне ----------
  const MAX_TAG_BYTES = 16 << 20;

  async function readBytes(file, from, length) {
    return new Uint8Array(await file.slice(from, from + length).arrayBuffer());
  }

  const syncsafe = (b, i) => ((b[i] & 0x7f) << 21) | ((b[i + 1] & 0x7f) << 14) | ((b[i + 2] & 0x7f) << 7) | (b[i + 3] & 0x7f);
  const be32 = (b, i) => ((b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]) >>> 0;
  const le32 = (b, i) => (b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)) >>> 0;

  function decodeLatin(bytes) {
    // В русских MP3 «латиница» часто на самом деле cp1251: если все высокие байты — кириллица, читаем так.
    const high = bytes.filter((c) => c >= 0x80);
    if (high.length && high.every((c) => c >= 0xc0 || c === 0xa8 || c === 0xb8)) {
      const fixed = new TextDecoder('windows-1251').decode(bytes);
      if (!/[A-Za-z][А-яЁё]|[А-яЁё][A-Za-z]/.test(fixed)) return fixed; // «Beyoncé» — настоящий latin-1
    }
    return new TextDecoder('latin1').decode(bytes);
  }

  function decodeText(enc, bytes) {
    let text;
    if (enc === 1 || enc === 2) {
      let le = enc === 1;
      if (bytes[0] === 0xff && bytes[1] === 0xfe) { le = true; bytes = bytes.subarray(2); }
      else if (bytes[0] === 0xfe && bytes[1] === 0xff) { le = false; bytes = bytes.subarray(2); }
      text = new TextDecoder(le ? 'utf-16le' : 'utf-16be').decode(bytes);
    } else {
      text = enc === 3 ? new TextDecoder('utf-8').decode(bytes) : decodeLatin(bytes);
    }
    return text.split('\0')[0].trim();
  }

  function textEnd(bytes, from, enc) {
    // Конец строки с нулём: для UTF-16 — два нулевых байта на чётной позиции.
    if (enc === 1 || enc === 2) {
      for (let i = from; i + 1 < bytes.length; i += 2) if (!bytes[i] && !bytes[i + 1]) return i;
      return bytes.length;
    }
    const i = bytes.indexOf(0, from);
    return i < 0 ? bytes.length : i;
  }

  function parseApic(body, v2) {
    const enc = body[0];
    let pos = 1;
    let mime = 'image/jpeg';
    if (v2) { mime = String.fromCharCode(...body.subarray(1, 4)).toLowerCase() === 'png' ? 'image/png' : 'image/jpeg'; pos = 4; }
    else {
      const end = body.indexOf(0, 1);
      mime = new TextDecoder('latin1').decode(body.subarray(1, end)) || mime;
      pos = end + 1;
    }
    pos += 1; // тип картинки
    const end = textEnd(body, pos, enc);
    pos = end + (enc === 1 || enc === 2 ? 2 : 1);
    return pos < body.length ? new Blob([body.slice(pos)], { type: mime.includes('/') ? mime : `image/${mime}` }) : null;
  }

  async function parseId3(file, head) {
    const ver = head[3];
    const total = Math.min(syncsafe(head, 6) + 10, MAX_TAG_BYTES);
    const buf = await readBytes(file, 0, total);
    const v2 = ver === 2;
    const hdr = v2 ? 6 : 10;
    let pos = 10;
    if (head[5] & 0x40) pos += ver === 4 ? syncsafe(buf, 10) : be32(buf, 10) + 4; // расширенный заголовок
    const keys = { TIT2: 'title', TT2: 'title', TPE1: 'artist', TP1: 'artist', TALB: 'album', TAL: 'album',
      TDRC: 'year', TYER: 'year', TYE: 'year' };
    const out = {};
    while (pos + hdr <= buf.length) {
      const id = String.fromCharCode(...buf.subarray(pos, pos + (v2 ? 3 : 4)));
      if (!/^[A-Z0-9]{3,4}$/.test(id)) break;
      const size = v2 ? (buf[pos + 3] << 16) | (buf[pos + 4] << 8) | buf[pos + 5]
        : ver === 4 ? syncsafe(buf, pos + 4) : be32(buf, pos + 4);
      if (size <= 0) break;
      const body = buf.subarray(pos + hdr, pos + hdr + size);
      pos += hdr + size;
      if (keys[id] && !out[keys[id]]) out[keys[id]] = decodeText(body[0], body.subarray(1));
      else if ((id === 'APIC' || id === 'PIC') && !out.cover) out.cover = parseApic(body, v2);
    }
    return out;
  }

  async function parseFlac(file) {
    const out = {};
    let pos = 4;
    for (let n = 0; n < 64 && pos < MAX_TAG_BYTES; n += 1) {
      const h4 = await readBytes(file, pos, 4);
      if (h4.length < 4) break;
      const type = h4[0] & 0x7f;
      const len = (h4[1] << 16) | (h4[2] << 8) | h4[3];
      if (type === 4 || (type === 6 && !out.cover)) {
        const b = await readBytes(file, pos + 4, len);
        if (type === 4) {
          let p = 4 + le32(b, 0);
          const count = le32(b, p); p += 4;
          for (let i = 0; i < count && p + 4 <= b.length; i += 1) {
            const l = le32(b, p); p += 4;
            const entry = new TextDecoder('utf-8').decode(b.subarray(p, p + l)); p += l;
            const eq = entry.indexOf('=');
            const key = { TITLE: 'title', ARTIST: 'artist', ALBUM: 'album', DATE: 'year', YEAR: 'year' }[entry.slice(0, eq).toUpperCase()];
            if (key && !out[key]) out[key] = entry.slice(eq + 1).trim();
          }
        } else {
          let p = 4;
          const mimeLen = be32(b, p); p += 4;
          const mime = new TextDecoder('latin1').decode(b.subarray(p, p + mimeLen)); p += mimeLen;
          p += 4 + be32(b, p) + 16; // описание, размеры и глубина цвета
          const dataLen = be32(b, p); p += 4;
          out.cover = new Blob([b.slice(p, p + dataLen)], { type: mime || 'image/jpeg' });
        }
      }
      pos += 4 + len;
      if (h4[0] & 0x80) break; // последний блок
    }
    return out;
  }

  async function readTags(file) {
    try {
      const head = await readBytes(file, 0, 10);
      let tags = null;
      if (head[0] === 0x49 && head[1] === 0x44 && head[2] === 0x33) tags = await parseId3(file, head);
      else if (head[0] === 0x66 && head[1] === 0x4c && head[2] === 0x61 && head[3] === 0x43) tags = await parseFlac(file);
      if (tags && tags.year) tags.year = (tags.year.match(/\d{4}/) || [''])[0];
      return tags;
    } catch (_) {
      return null; // не смогли — сервер всё равно возьмёт теги из файла
    }
  }

  // Картинка для обложки: уменьшаем до 1000 px и пережимаем в JPEG прямо на телефоне.
  async function resizeImage(file, max = 1000) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((resolve, reject) => {
        const i = new Image();
        i.onload = () => resolve(i);
        i.onerror = () => reject(new Error('Не удалось открыть картинку'));
        i.src = url;
      });
      const scale = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = h('canvas', { width: Math.round(img.naturalWidth * scale), height: Math.round(img.naturalHeight * scale) });
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
      if (!blob) throw new Error('Не удалось подготовить обложку');
      return blob;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  const blobUrls = new WeakMap();
  function blobUrl(blob) {
    if (!blobUrls.has(blob)) blobUrls.set(blob, URL.createObjectURL(blob));
    return blobUrls.get(blob);
  }

  // ---------- экран: загрузка ----------
  const up = { files: [], kind: null, running: false };
  const ACCEPT = 'audio/*,.mp3,.flac,.m4a,.aac,.ogg,.oga,.opus,.wav,.wma,.aiff,.alac,.ape';
  const FIELDS = [['title', 'Название'], ['artist', 'Исполнитель'], ['album', 'Альбом'], ['year', 'Год']];

  // Что окажется в треке: правка пользователя → тег файла → догадка по имени файла.
  function effective(f) {
    const tags = f.tags || {};
    const pick = (k) => (k in f.edit ? f.edit[k] : tags[k]) || '';
    return {
      title: pick('title') || f.guess.title,
      artist: pick('artist') || f.guess.artist,
      album: pick('album'),
      year: pick('year'),
      cover: f.removeCover ? null : (f.cover || tags.cover || null),
    };
  }

  const isEdited = (f) => Object.keys(f.edit).length > 0 || !!f.cover || f.removeCover;

  function trackEditor(f, onSave) {
    const e = effective(f);
    const inputs = {};
    let cover = f.cover;
    let removeCover = f.removeCover;
    const fileCover = f.tags && f.tags.cover;
    const coverBox = h('div', { class: 'ed-cover' });
    const picker = h('input', { type: 'file', accept: 'image/*', hidden: true });
    const coverBtns = h('div', { class: 'ed-cover-actions' });

    function drawCover() {
      const blob = removeCover ? null : (cover || fileCover);
      put(coverBox, blob ? h('img', { class: 'cover', src: blobUrl(blob), alt: '' }) : h('div', { class: 'cover' }, icon('music')));
      put(coverBtns,
        h('label', { class: 'btn secondary' }, icon('edit', 'sm'), blob ? 'Сменить обложку' : 'Добавить обложку', picker),
        blob ? h('button', { class: 'btn secondary danger', onclick: () => { cover = null; removeCover = true; drawCover(); } },
          icon('trash', 'sm'), 'Убрать') : null,
        !blob && (removeCover || cover) && fileCover
          ? h('button', { class: 'link-btn', onclick: () => { cover = null; removeCover = false; drawCover(); } }, 'Вернуть из файла') : null);
    }

    picker.addEventListener('change', async () => {
      const file = picker.files[0];
      picker.value = '';
      if (!file) return;
      try {
        cover = await resizeImage(file);
        removeCover = false;
        drawCover();
        haptic.tap();
      } catch (err) { fail(err); }
    });

    const fields = FIELDS.map(([key, label]) => {
      inputs[key] = h('input', {
        class: 'text', value: e[key] || '', maxLength: key === 'year' ? 4 : 200, enterKeyHint: 'next',
        inputMode: key === 'year' ? 'numeric' : 'text', placeholder: key === 'year' ? '2024' : label,
      });
      return h('label', { class: `ed-field ${key}` }, h('span', {}, label), inputs[key]);
    });

    function save() {
      const year = inputs.year.value.trim();
      if (year && !/^\d{4}$/.test(year)) { toast('Год — четыре цифры, например 2024'); inputs.year.focus(); return; }
      const base = f.tags || {};
      const fallback = { title: f.guess.title, artist: f.guess.artist };
      const edit = {};
      for (const [key] of FIELDS) {
        const value = inputs[key].value.trim();
        if (value !== (base[key] || fallback[key] || '')) edit[key] = value;
      }
      Object.assign(f, { edit, cover, removeCover });
      haptic.ok();
      closeSheet();
      onSave();
    }

    drawCover();
    openSheet([
      h('div', { class: 'sheet-title' }, 'Данные трека'),
      h('div', { class: 'ed-file' }, icon('music', 'sm'), f.file.name),
      h('div', { class: 'ed-top' }, coverBox, coverBtns),
      h('div', { class: 'ed-fields' }, fields),
      h('button', { class: 'btn block big', onclick: save }, icon('check', 'sm'), 'Готово'),
      h('div', { class: 'sheet-note' }, 'Пустые поля возьмём из тегов файла. Трек встанет в начало плейлиста, как только Яндекс его обработает.'),
    ]);
  }

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
      h('div', { class: 'hint' }, 'Нажмите на файл, чтобы поменять название, исполнителя, альбом, год или обложку. '
        + 'Загруженные треки встают в начало плейлиста.'
        + (state.me.can_convert ? ' Не-MP3 сервер сам переведёт в MP3 320 kbps.' : '')),
      listHead, list, uploadBar);

    input.addEventListener('change', () => { addFiles(input.files); input.value = ''; });
    for (const ev of ['dragenter', 'dragover']) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); });
    for (const ev of ['dragleave', 'drop']) dz.addEventListener(ev, () => dz.classList.remove('drag'));
    dz.addEventListener('drop', (e) => { e.preventDefault(); addFiles(e.dataTransfer.files); });

    function addFiles(fileList) {
      for (const file of fileList) {
        if (file.size > maxMb * 1048576) { toast(`${file.name}: больше ${maxMb} МБ`); continue; }
        const f = { file, guess: guessFromName(file.name), tags: null, edit: {}, cover: null, removeCover: false,
          status: 'pending', progress: 0, message: '' };
        f.tagsReady = readTags(file).then((tags) => { f.tags = tags; if (f.redraw) f.redraw(); });
        up.files.push(f);
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
          h('div', { class: 'meta' }, h('div', {}, 'По умолчанию и для чата'),
            h('div', { class: 'sub' }, 'Файлы, присланные боту, можно будет загрузить сюда одной кнопкой')),
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
      const coverBox = h('div', { class: 'file-cover' });
      const title = h('div', { class: 'title' });
      const sub = h('div', { class: 'sub' });
      const bar = h('div');
      const progress = h('div', { class: 'progress' }, bar);
      const status = h('div', { class: 'status' });
      const edit = h('button', { class: 'icon-btn', 'aria-label': 'Изменить данные трека' }, icon('edit', 'sm'));
      const remove = h('button', { class: 'icon-btn', 'aria-label': 'Убрать' });
      const openEditor = () => {
        if (f.status === 'uploading' || f.status === 'done') return;
        f.tagsReady.then(() => trackEditor(f, () => f.redraw()));
      };
      edit.addEventListener('click', (e) => { e.stopPropagation(); openEditor(); });
      remove.addEventListener('click', (e) => { e.stopPropagation(); up.files = up.files.filter((x) => x !== f); drawFiles(); });
      f.el = h('div', { class: 'file', role: 'button', onclick: openEditor },
        h('div', { class: 'file-head' }, coverBox, h('div', { class: 'meta' }, title, sub), edit, remove),
        progress, status);
      f.redraw = () => {
        const e = effective(f);
        const busy = f.status === 'uploading';
        const locked = busy || f.status === 'done';
        put(coverBox, e.cover ? h('img', { class: 'cover', src: blobUrl(e.cover), alt: '' }) : h('div', { class: 'cover' }, icon('music')));
        put(title, h('span', { class: 'name' }, e.title || f.file.name), isEdited(f) ? h('span', { class: 'edited' }, 'изменено') : null);
        sub.textContent = [e.artist, e.album, fmtSize(f.file.size)].filter(Boolean).join(' · ');
        bar.style.width = `${Math.round(f.progress * 100)}%`;
        progress.hidden = f.status === 'pending';
        status.textContent = f.message;
        status.hidden = !f.message;
        status.className = `status${f.status === 'done' ? ' ok' : f.status === 'error' ? ' err' : ''}`;
        f.el.classList.toggle('done', f.status === 'done');
        f.el.classList.toggle('locked', locked);
        edit.hidden = locked;
        remove.hidden = busy;
        put(remove, icon(f.status === 'done' ? 'x' : 'trash', 'sm'));
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
        toast(`Загружено: ${filesWord(ok)}. Через минуту-другую Яндекс обработает треки, и они встанут в начало плейлиста`, 5000);
        loadLibrary().catch(() => {});
      }
    }

    function uploadOne(f) {
      return new Promise((resolve) => {
        const form = new FormData();
        const meta = { ...f.edit };
        if (f.removeCover && !f.cover) meta.remove_cover = true;
        form.append('kind', String(up.kind));
        form.append('meta', JSON.stringify(meta));
        form.append('fallback_artist', f.guess.artist);
        form.append('fallback_title', f.guess.title);
        if (f.cover) form.append('cover', f.cover, 'cover.jpg');
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
    now.bg.style.backgroundImage = t.cover ? cssUrl(bigCover(t.cover)) : 'none';
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
      else if (e.code === 'banned') gate('Доступ закрыт', e.message);
      else if (e.code === 'maintenance') gate('Техническое обслуживание', e.message, 'Проверить снова', enterApp);
      else if (e.code === 'closed') gate('Бот закрыт для новых пользователей', e.message);
      else gate('Не удалось открыть', e.message, 'Повторить', enterApp);
      return;
    }
    const wantsAdmin = new URLSearchParams(location.search).get('admin') === '1' && isAdmin();
    if (!state.me.connected) {
      if (wantsAdmin) { // админке Яндекс не нужен
        resetNav();
        state.tab = 'library';
        stacks.library.push(mountPage(adminView));
        show(top(), 0);
        $('#tabs').hidden = true;
        document.body.classList.add('no-tabs');
        return;
      }
      showLogin();
      return;
    }
    resetNav();
    state.libraryLoaded = false;
    $('#tabs').hidden = false;
    document.body.classList.remove('no-tabs');
    setTab('library');
    if (wantsAdmin) openAdmin();
  }

  // fxTunnel показывает страницу-предупреждение при заходе на поддомен и после «Продолжить» помнит
  // согласие 12 часов. Это наше собственное приложение — продлеваем согласие на год.
  function extendTunnelConsent() {
    const m = location.hostname.match(/^([a-z0-9-]+)\.fxtun\.(dev|ru)$/);
    if (m) document.cookie = `_fxt_consent_${m[1]}=1; Path=/; Max-Age=31536000; SameSite=Lax; Secure`;
  }

  function boot() {
    extendTunnelConsent();
    applyPrefs();
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
        if (supports('7.7')) tg.disableVerticalSwipes();
        if (supports('6.1')) tg.BackButton.onClick(onBack);
        if (supports('7.0')) { tg.SettingsButton.onClick(openSettings); tg.SettingsButton.show(); }
      } catch (_) { /* старый клиент Telegram */ }
      applyPrefs();
      loadCloudPrefs();
      if (tg.onEvent) tg.onEvent('themeChanged', applyPrefs);
    }

    if (!initData) {
      gate('Откройте через Telegram', 'Это мини-приложение работает внутри бота: нажмите кнопку «Медиатека» в чате с ним.');
      return;
    }
    enterApp();
  }

  boot();
})();

/* Мини-приложение «Моя музыка»: медиатека, поиск, плеер, скачивание и загрузка треков. */
(() => {
  'use strict';

  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || '';
  const $ = (sel) => document.querySelector(sel);
  const supports = (v) => !!(tg && tg.isVersionAtLeast && tg.isVersionAtLeast(v));

  // ---------- иконки (24×24, линейные) ----------
  const ICONS = {
    library: '<path d="M4 4v16M8 8v12M12 6v14"/><path d="m16 6 4 14"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21.2l8.8-8.8a5.5 5.5 0 0 0 0-7.8z"/>',
    heartFill: ['<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21.2l8.8-8.8a5.5 5.5 0 0 0 0-7.8z"/>', true],
    more: ['<circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/>', true],
    play: ['<path d="M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1 1 0 0 0 0-1.7l-12-7.5A1 1 0 0 0 7 4.5z"/>', true],
    pause: ['<rect x="6" y="4" width="4" height="16" rx="1.2"/><rect x="14" y="4" width="4" height="16" rx="1.2"/>', true],
    next: ['<path d="M5 5.3v13.4a.8.8 0 0 0 1.2.7L16 13v5.5a1 1 0 0 0 2 0v-13a1 1 0 0 0-2 0V11L6.2 4.6a.8.8 0 0 0-1.2.7z"/>', true],
    prev: ['<path d="M19 5.3v13.4a.8.8 0 0 1-1.2.7L8 13v5.5a1 1 0 0 1-2 0v-13a1 1 0 0 1 2 0V11l9.8-6.4a.8.8 0 0 1 1.2.7z"/>', true],
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
  };

  function icon(name, cls = '') {
    const def = ICONS[name];
    const [paths, filled] = Array.isArray(def) ? def : [def, false];
    const t = document.createElement('template');
    t.innerHTML = `<svg class="i${filled ? ' fill' : ''} ${cls}" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
    return t.content.firstChild;
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
      const img = h('img', { class: `cover ${cls}`, src: url, loading: 'lazy', alt: '' });
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

  const fail = (e) => { haptic.err(); toast(e.message || String(e)); };

  // ---------- состояние ----------
  const state = {
    me: null,
    playlists: [],
    liked: new Set(),
    libraryLoaded: false,
    tab: 'library',
    stack: [],
  };

  async function loadLibrary() {
    const data = await api('/api/library');
    state.playlists = data.playlists;
    state.liked = new Set(data.liked_ids);
    state.libraryLoaded = true;
  }

  // ---------- навигация ----------
  const view = $('#view');
  let renderSeq = 0;

  function render() {
    const seq = ++renderSeq;
    const alive = () => seq === renderSeq;
    view.replaceChildren();
    window.scrollTo(0, 0);
    state.stack[state.stack.length - 1](view, alive);
    updateBackButton();
    document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === state.tab));
    markPlaying();
  }

  function push(viewFn) { state.stack.push(viewFn); render(); }
  function pop() { if (state.stack.length > 1) { state.stack.pop(); render(); } }
  function setTab(tab) { state.tab = tab; state.stack = [ROOTS[tab]]; render(); }

  function updateBackButton() {
    if (!tg || !supports('6.1')) return;
    if (state.stack.length > 1 || sheetOpen()) tg.BackButton.show();
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

  function openSheet(...children) {
    if (onSheetClose) { const fn = onSheetClose; onSheetClose = null; fn(); }
    sheet.replaceChildren(h('div', { class: 'grip' }), ...children);
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
    openSheet(h('div', { class: 'sheet-title' }, title), input, btn);
    setTimeout(() => input.focus(), 250);
  }

  // ---------- действия с треками ----------
  function isLiked(id) { return state.liked.has(id); }

  function likeButton(t) {
    const btn = h('button', { class: `icon-btn${isLiked(t.id) ? ' liked' : ''}`, 'data-like': t.id, 'aria-label': 'Нравится' },
      icon(isLiked(t.id) ? 'heartFill' : 'heart'));
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggleLike(t); });
    return btn;
  }

  function refreshLikes(id) {
    document.querySelectorAll('[data-like]').forEach((b) => {
      if (b.dataset.like !== id) return;
      b.classList.toggle('liked', isLiked(id));
      b.replaceChildren(icon(isLiked(id) ? 'heartFill' : 'heart'));
    });
  }

  async function toggleLike(t) {
    const was = isLiked(t.id);
    was ? state.liked.delete(t.id) : state.liked.add(t.id);
    refreshLikes(t.id);
    haptic.tap();
    try {
      await api(`/api/likes/${encodeURIComponent(t.id)}`, { method: was ? 'DELETE' : 'POST' });
      toast(was ? 'Убрано из «Мне нравится»' : 'Добавлено в «Мне нравится»');
    } catch (e) {
      was ? state.liked.add(t.id) : state.liked.delete(t.id);
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

  function ownPlaylists() { return state.playlists; }

  function addToPlaylistSheet(t) {
    const add = async (p) => {
      try {
        await api(`/api/playlists/${p.kind}/tracks`, { method: 'POST', body: { track_id: t.id } });
        p.count += 1;
        haptic.ok();
        toast(`Добавлено в «${p.title}»`);
      } catch (e) { fail(e); }
    };
    openSheet(
      h('div', { class: 'sheet-title' }, 'Добавить в плейлист'),
      h('div', { class: 'card list' },
        h('button', { class: 'row', onclick: () => createPlaylistSheet(add) },
          h('div', { class: 'cover' }, icon('plus')), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Новый плейлист'))),
        ownPlaylists().map((p) => h('button', { class: 'row', onclick: () => { closeSheet(); add(p); } },
          coverEl(p.cover),
          h('div', { class: 'meta' }, h('div', { class: 'title' }, p.title), h('div', { class: 'sub' }, tracksWord(p.count)))))),
    );
  }

  function createPlaylistSheet(then) {
    promptSheet('Новый плейлист', 'Название', '', 'Создать', async (title) => {
      const p = await api('/api/playlists', { method: 'POST', body: { title } });
      state.playlists.unshift(p);
      haptic.ok();
      toast(`Плейлист «${p.title}» создан`);
      if (then) setTimeout(() => then(p), 50);
      else if (state.tab === 'library' && state.stack.length === 1) render();
    });
  }

  function trackMenu(t, ctx, row) {
    openSheet(
      h('div', { class: 'sheet-track' }, coverEl(t.cover, 'lg'),
        h('div', { class: 'meta' }, h('div', { class: 'title' }, t.title), h('div', { class: 'sub' }, t.artists))),
      h('div', { class: 'card list' },
        menuRow('download', 'Скачать на телефон', () => downloadTrack(t)),
        menuRow('send', 'Отправить в чат', () => sendTrack(t)),
        menuRow('plus', 'Добавить в плейлист', () => addToPlaylistSheet(t)),
        t.album_id && menuRow('disc', 'Открыть альбом', () => push(sourceView('alb', t.album_id))),
        t.artist_id && menuRow('user', 'Открыть исполнителя', () => push(sourceView('art', t.artist_id))),
        ctx && ctx.ownKind != null && menuRow('trash', 'Удалить из плейлиста', () => removeFromPlaylist(t, ctx, row), 'danger')),
    );
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
      coverEl(t.cover),
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

  // ---------- экран: медиатека ----------
  function libraryView(root, alive) {
    const acc = h('div', { class: 'account' }, icon('user', 'sm'), state.me.login || 'Аккаунт Яндекса',
      state.me.has_plus ? h('span', { class: 'badge plus' }, 'Плюс') : h('span', { class: 'badge' }, 'без Плюса'));
    const likesSub = h('div', { class: 's' }, '…');
    const likes = h('button', { class: 'likes-tile', onclick: () => push(sourceView('likes', '')) },
      h('div', { class: 'heart' }, icon('heartFill')),
      h('div', {}, h('div', { class: 't' }, 'Мне нравится'), likesSub));
    const list = h('div', { class: 'card list' }, spinner());

    root.append(
      h('div', { class: 'page-title' }, 'Медиатека'), acc, likes,
      h('div', { class: 'section-head' }, h('h2', {}, 'Мои плейлисты'),
        h('button', { class: 'link-btn', onclick: () => createPlaylistSheet() }, icon('plus', 'sm'), 'Создать')),
      list);

    const draw = () => {
      likesSub.textContent = tracksWord(state.liked.size);
      if (!state.playlists.length) {
        list.replaceChildren(emptyEl('list', 'Плейлистов пока нет — создайте первый'));
        return;
      }
      list.replaceChildren(...state.playlists.map((p) => h('button', { class: 'row', onclick: () => push(sourceView('pl', p.ref)) },
        coverEl(p.cover, 'lg', 'list'),
        h('div', { class: 'meta' },
          h('div', { class: 'title' }, p.title, p.kind === state.me.upload_target ? h('span', { class: 'pin', title: 'Сюда загружаются файлы' }, icon('pin', 'sm')) : null),
          h('div', { class: 'sub' }, tracksWord(p.count))),
        icon('chevron', 'sm'))));
    };
    if (state.libraryLoaded) draw();
    loadLibrary().then(() => alive() && draw()).catch(fail);
  }

  // ---------- экран: список треков (плейлист, альбом, артист, лайки) ----------
  function sourceView(src, ref) {
    return (root, alive) => {
      const hero = h('div', { class: 'hero' }, spinner());
      const list = h('div', { class: 'card list' });
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
        const sub = [info.subtitle, tracksWord(src === 'likes' ? state.liked.size : info.total)]
          .filter(Boolean).join(' · ');
        const play = h('button', { class: 'btn', onclick: () => playQueue(tracks, 0) }, icon('play', 'sm'), 'Слушать');
        const send = h('button', { class: 'btn secondary', onclick: () => sendAll() }, icon('send', 'sm'), 'Всё в чат');
        const actions = h('div', { class: 'actions' }, play, send,
          info.own_kind != null ? h('button', { class: 'btn secondary round', 'aria-label': 'Ещё', onclick: () => playlistMenu() }, icon('more')) : null);
        hero.replaceChildren(
          info.cover ? coverEl(info.cover, shape, fallback) : h('div', { class: `cover ${shape}`, style: src === 'likes' ? 'background:linear-gradient(135deg,#ff5f6d,#c21d6b)' : null }, icon(fallback)),
          h('h1', {}, info.title), h('div', { class: 'sub' }, sub), info.total ? actions : null);
      }

      async function more() {
        if (loading || done || !alive()) return;
        loading = true;
        sentinel.replaceChildren(tracks.length ? spinner() : '');
        try {
          const q = new URLSearchParams({ ref, offset: tracks.length, limit: 50 });
          const data = await api(`/api/source/${src}?${q}`);
          if (!alive()) return;
          if (!info) {
            info = data;
            ctx.ownKind = data.own_kind;
            drawHero();
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
          if (!info) hero.replaceChildren(emptyEl('x', e.message));
          else fail(e);
        } finally {
          loading = false;
          sentinel.replaceChildren();
        }
        if (!done && alive() && sentinel.getBoundingClientRect().top < window.innerHeight + 400) more();
      }

      async function sendAll() {
        try {
          const r = await api(`/api/source/${src}/send?${new URLSearchParams({ ref: info.ref || ref })}`, { method: 'POST' });
          if (r.started) { haptic.ok(); toast('Отправляю все треки в чат с ботом — прогресс там же', 4000); }
          else toast('Уже идёт отправка другого списка — остановите её в чате или дождитесь');
        } catch (e) { fail(e); }
      }

      function playlistMenu() {
        const kind = info.own_kind;
        const isTarget = state.me.upload_target === kind;
        openSheet(
          h('div', { class: 'sheet-title' }, info.title),
          h('div', { class: 'card list' },
            menuRow('pin', isTarget ? 'Не загружать сюда файлы из чата' : 'Загружать сюда файлы из чата', () => setTarget(isTarget ? null : kind)),
            menuRow('edit', 'Переименовать', () => promptSheet('Переименовать плейлист', 'Название', info.title, 'Сохранить', async (title) => {
              await api(`/api/playlists/${kind}`, { method: 'PATCH', body: { title } });
              info.title = title;
              const p = state.playlists.find((x) => x.kind === kind);
              if (p) p.title = title;
              drawHero();
              toast('Переименовано');
            })),
            menuRow('trash', 'Удалить плейлист', async () => {
              if (!(await confirmAsk(`Удалить плейлист «${info.title}»? Это нельзя отменить.`))) return;
              try {
                await api(`/api/playlists/${kind}`, { method: 'DELETE' });
                state.playlists = state.playlists.filter((x) => x.kind !== kind);
                if (state.me.upload_target === kind) state.me.upload_target = null;
                toast('Плейлист удалён');
                pop();
              } catch (e) { fail(e); }
            }, 'danger')),
        );
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

  function searchView(root, alive) {
    const input = h('input', { type: 'search', placeholder: 'Треки, альбомы, артисты', value: search.q, enterKeyHint: 'search', autocomplete: 'off' });
    const segs = h('div', { class: 'segments' });
    const results = h('div');
    let timer;
    let seq = 0;

    const drawSegs = () => segs.replaceChildren(...TYPES.map(([type, label]) => h('button', {
      class: type === search.type ? 'on' : '',
      onclick: () => { search.type = type; drawSegs(); run(); },
    }, label)));

    async function run() {
      const my = ++seq;
      const q = search.q.trim();
      if (!q) { search.items = []; search.ran = false; draw(); return; }
      results.replaceChildren(spinner());
      try {
        const data = await api(`/api/search?${new URLSearchParams({ q, type: search.type })}`);
        if (my !== seq || !alive()) return;
        Object.assign(search, { items: data.items, corrected: data.corrected, shown: data.type, ran: true });
        draw();
      } catch (e) {
        if (alive()) results.replaceChildren(emptyEl('x', e.message));
      }
    }

    function draw() {
      if (!search.ran) {
        results.replaceChildren(emptyEl('search', 'Найдите трек, альбом, артиста или плейлист'));
        return;
      }
      if (!search.items.length) {
        results.replaceChildren(emptyEl('search', 'Ничего не нашлось'));
        return;
      }
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
      results.replaceChildren(
        search.corrected ? h('div', { class: 'corrected' }, 'Показаны результаты для «', search.corrected, '»') : '', list);
      markPlaying();
    }

    input.addEventListener('input', () => { search.q = input.value; clearTimeout(timer); timer = setTimeout(run, 350); });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(timer); input.blur(); run(); } });
    root.append(h('div', { class: 'search-bar' }, h('div', { class: 'search-field' }, icon('search', 'sm'), input), segs), results);
    drawSegs();
    draw();
    if (!search.q) setTimeout(() => alive() && input.focus(), 300);
  }

  // ---------- экран: загрузка ----------
  const up = { files: [], kind: null, running: false };
  const ACCEPT = 'audio/*,.mp3,.flac,.m4a,.aac,.ogg,.oga,.opus,.wav,.wma,.aiff,.alac,.ape';

  function uploadView(root, alive) {
    const targetBox = h('div', { class: 'card list' }, spinner());
    const input = h('input', { type: 'file', multiple: true, accept: ACCEPT, hidden: true });
    const maxMb = state.me.max_upload_mb;
    const dz = h('label', { class: 'dropzone' },
      icon('upload'), h('b', {}, 'Выбрать аудиофайлы'),
      h('span', {}, `MP3, FLAC, M4A, WAV… до ${maxMb} МБ каждый`), input);
    const list = h('div', { class: 'card list' });
    const clear = h('button', { class: 'link-btn', onclick: () => { up.files = up.files.filter((f) => f.status !== 'done'); drawFiles(); } },
      'Убрать загруженные');
    const listHead = h('div', { class: 'section-head' }, h('h2', {}, 'Файлы'), clear);
    const button = h('button', { class: 'btn block', onclick: runUploads });
    const uploadBar = h('div', { class: 'upload-bar' }, button);

    root.append(
      h('div', { class: 'page-title' }, 'Загрузка'),
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

    function drawTarget() {
      if (!state.playlists.length) {
        targetBox.replaceChildren(h('button', { class: 'target', onclick: () => createPlaylistSheet((p) => { up.kind = p.kind; drawTarget(); drawButton(); }) },
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
      targetBox.replaceChildren(
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
      openSheet(
        h('div', { class: 'sheet-title' }, 'Куда загружать'),
        h('div', { class: 'card list' },
          h('button', { class: 'row', onclick: () => createPlaylistSheet((p) => { up.kind = p.kind; drawTarget(); drawButton(); }) },
            h('div', { class: 'cover' }, icon('plus')), h('div', { class: 'meta' }, h('div', { class: 'title' }, 'Новый плейлист'))),
          state.playlists.map((p) => h('button', { class: 'row', onclick: () => { closeSheet(); up.kind = p.kind; drawTarget(); drawButton(); } },
            coverEl(p.cover, '', 'list'),
            h('div', { class: 'meta' }, h('div', { class: 'title' }, p.title), h('div', { class: 'sub' }, tracksWord(p.count))),
            p.kind === up.kind ? icon('pin', 'sm') : null))));
    }

    function fileCard(f) {
      const locked = f.status === 'uploading' || f.status === 'done';
      const artist = h('input', { placeholder: f.guess.artist || 'Исполнитель', value: f.artist, disabled: locked, 'aria-label': 'Исполнитель' });
      const title = h('input', { placeholder: f.guess.title || 'Название', value: f.title, disabled: locked, 'aria-label': 'Название' });
      artist.addEventListener('input', () => { f.artist = artist.value; });
      title.addEventListener('input', () => { f.title = title.value; });
      const fill = h('div');
      const status = h('div', { class: 'status' });
      const remove = h('button', { class: 'icon-btn', 'aria-label': 'Убрать', hidden: f.status === 'uploading' },
        icon(f.status === 'done' ? 'x' : 'trash', 'sm'));
      remove.addEventListener('click', () => { up.files = up.files.filter((x) => x !== f); drawFiles(); });
      f.redraw = () => {
        fill.style.width = `${Math.round(f.progress * 100)}%`;
        status.textContent = f.message;
        status.className = `status${f.status === 'done' ? ' ok' : f.status === 'error' ? ' err' : ''}`;
        const busy = f.status === 'uploading';
        remove.hidden = busy;
        artist.disabled = title.disabled = busy || f.status === 'done';
      };
      f.redraw();
      f.el = h('div', { class: 'file' },
        h('div', { class: 'file-head' }, icon('music', 'sm'), h('div', { class: 'name' }, f.file.name),
          h('div', { class: 'size' }, fmtSize(f.file.size)), remove),
        h('div', { class: 'fields' }, artist, title),
        f.status !== 'pending' ? h('div', { class: 'progress' }, fill) : null,
        status);
      return f.el;
    }

    // Перерисовываем только карточку этого файла, чтобы не сбить ввод в соседних полях.
    function redrawCard(f) {
      const old = f.el;
      if (old && old.parentNode) old.replaceWith(fileCard(f));
      drawButton();
    }

    function drawFiles() {
      list.hidden = listHead.hidden = !up.files.length;
      list.replaceChildren(...up.files.map(fileCard));
      drawButton();
    }

    function drawButton() {
      const todo = up.files.filter((f) => f.status === 'pending' || f.status === 'error').length;
      uploadBar.hidden = !todo && !up.running;
      clear.hidden = up.running || !up.files.some((f) => f.status === 'done');
      button.disabled = up.running || !todo || up.kind == null;
      button.replaceChildren(up.running ? h('div', { class: 'spinner inline' }) : icon('upload', 'sm'),
        up.running ? 'Загружаю…' : `Загрузить ${filesWord(todo)}`);
    }

    async function runUploads() {
      up.running = true;
      drawButton();
      let ok = 0;
      for (const f of up.files.filter((x) => x.status === 'pending' || x.status === 'error')) {
        if (await uploadOne(f)) ok += 1;
      }
      up.running = false;
      if (alive()) drawButton();
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
        redrawCard(f);
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
          redrawCard(f);
          resolve(good);
        };
        xhr.onerror = () => { Object.assign(f, { status: 'error', message: 'Нет связи с сервером' }); redrawCard(f); resolve(false); };
        xhr.send(form);
      });
    }

    drawFiles();
    const ready = () => { if (alive()) { drawTarget(); drawButton(); } };
    if (state.libraryLoaded) ready();
    loadLibrary().then(ready).catch(fail);
  }

  const ROOTS = { library: libraryView, search: searchView, upload: uploadView };

  // ---------- плеер ----------
  const audio = $('#audio');
  const playerBar = $('#player');
  const player = { queue: [], index: -1 };
  let now = null; // элементы открытого полноэкранного плеера

  const current = () => player.queue[player.index];

  function playQueue(queue, index) {
    const t = queue[index];
    if (!t) return;
    if (!t.available) { toast('Трек недоступен'); return; }
    player.queue = queue.slice();
    player.index = index;
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

  function step(delta) {
    let i = player.index + delta;
    while (i >= 0 && i < player.queue.length && !player.queue[i].available) i += delta;
    if (i < 0 || i >= player.queue.length) return false;
    player.index = i;
    start();
    return true;
  }

  function toggle() {
    if (!current()) return;
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  }

  function markPlaying() {
    const t = current();
    document.querySelectorAll('.row[data-id]').forEach((r) => r.classList.toggle('playing', !!t && r.dataset.id === t.id));
  }

  function drawBar() {
    const t = current();
    playerBar.hidden = !t;
    document.body.classList.toggle('has-player', !!t);
    if (!t) return;
    playerBar.querySelector('.cover').replaceWith(coverEl(t.cover));
    playerBar.querySelector('.title').textContent = t.title;
    playerBar.querySelector('.sub').textContent = t.artists;
    playerBar.querySelector('[data-act=toggle]').replaceChildren(icon(audio.paused ? 'play' : 'pause'));
    playerBar.querySelector('[data-act=next]').replaceChildren(icon('next'));
  }

  function drawProgress() {
    const d = audio.duration || (current() && current().duration) || 0;
    playerBar.querySelector('.bar').style.width = d ? `${(audio.currentTime / d) * 100}%` : '0';
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
    now.cover.replaceWith(now.cover = coverEl(bigCover(t.cover)));
    now.title.textContent = t.title;
    now.sub.textContent = t.artists;
    now.play.replaceChildren(icon(audio.paused ? 'play' : 'pause'));
    now.like.replaceWith(now.like = likeButton(t));
    drawProgress();
  }

  function openNowPlaying() {
    const t = current();
    if (!t) return;
    const seek = h('input', { type: 'range', class: 'seek', min: 0, max: Math.max(1, t.duration), step: 1, value: 0 });
    const els = {
      cover: coverEl(bigCover(t.cover)),
      title: h('h2'), sub: h('div', { class: 'sub' }),
      seek, cur: h('span'), total: h('span'),
      play: h('button', { class: 'play', 'aria-label': 'Пауза', onclick: toggle }),
      like: h('span'),
      dragging: false,
    };
    seek.addEventListener('input', () => { els.dragging = true; els.cur.textContent = fmtTime(seek.value); });
    seek.addEventListener('change', () => { audio.currentTime = Number(seek.value); els.dragging = false; });
    const trackNow = () => current();
    openSheet(h('div', { class: 'now' },
      els.cover, els.title, els.sub, seek,
      h('div', { class: 'times' }, els.cur, els.total),
      h('div', { class: 'controls' },
        h('button', { class: 'icon-btn', 'aria-label': 'Предыдущий', onclick: () => (audio.currentTime > 5 ? (audio.currentTime = 0) : step(-1)) }, icon('prev')),
        els.play,
        h('button', { class: 'icon-btn', 'aria-label': 'Следующий', onclick: () => step(1) }, icon('next'))),
      h('div', { class: 'now-actions' },
        els.like,
        h('button', { class: 'icon-btn', 'aria-label': 'Скачать', onclick: () => downloadTrack(trackNow()) }, icon('download')),
        h('button', { class: 'icon-btn', 'aria-label': 'В чат', onclick: () => sendTrack(trackNow()) }, icon('send')),
        h('button', { class: 'icon-btn', 'aria-label': 'Ещё', onclick: () => trackMenu(trackNow(), null, null) }, icon('more')))));
    now = els;
    onSheetClose = () => { now = null; };
    drawNow();
  }

  playerBar.addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]');
    if (act) { e.stopPropagation(); if (act.dataset.act === 'toggle') toggle(); else step(1); return; }
    openNowPlaying();
  });
  audio.addEventListener('timeupdate', drawProgress);
  audio.addEventListener('loadedmetadata', drawProgress);
  for (const ev of ['play', 'pause']) audio.addEventListener(ev, () => { drawBar(); if (now) drawNow(); });
  audio.addEventListener('ended', () => { if (!step(1)) drawBar(); });
  audio.addEventListener('error', () => { if (audio.src && current()) toast('Не удалось загрузить трек'); });
  if ('mediaSession' in navigator) {
    const handlers = { play: () => audio.play(), pause: () => audio.pause(), previoustrack: () => step(-1), nexttrack: () => step(1) };
    for (const [action, fn] of Object.entries(handlers)) {
      try { navigator.mediaSession.setActionHandler(action, fn); } catch (_) { /* не поддерживается */ }
    }
  }

  // ---------- запуск ----------
  function gate(title, text) {
    view.replaceChildren(h('div', { class: 'gate' }, icon('music'), h('b', {}, title), h('div', {}, text)));
    $('#tabs').hidden = true;
  }

  async function boot() {
    const labels = { library: ['library', 'Медиатека'], search: ['search', 'Поиск'], upload: ['upload', 'Загрузка'] };
    document.querySelectorAll('#tabs button').forEach((b) => {
      const [ic, label] = labels[b.dataset.tab];
      b.replaceChildren(icon(ic), label);
      b.addEventListener('click', () => {
        haptic.tap();
        if (b.dataset.tab === state.tab && state.stack.length === 1) return;
        setTab(b.dataset.tab);
      });
    });

    if (tg) {
      tg.ready();
      tg.expand();
      if (initData) document.documentElement.classList.add('tg');
      try {
        if (supports('6.1')) { tg.setHeaderColor('secondary_bg_color'); tg.setBackgroundColor('secondary_bg_color'); }
        if (supports('7.7')) tg.disableVerticalSwipes();
        if (supports('6.1')) tg.BackButton.onClick(onBack);
      } catch (_) { /* старый клиент Telegram */ }
    }

    if (!initData) {
      gate('Откройте через Telegram', 'Это мини-приложение работает внутри бота: нажмите кнопку «Медиатека» в чате с ним.');
      return;
    }
    view.replaceChildren(spinner());
    try {
      state.me = await api('/api/me');
    } catch (e) {
      gate(e.status === 403 ? 'Нет доступа' : 'Не удалось открыть', e.message);
      return;
    }
    setTab('library');
  }

  boot();
})();

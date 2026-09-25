/* Данные трека на телефоне: разбор имени файла, старые русские теги в cp1251, правка поверх тегов.
   Те же правила на сервере — bot/audio.py (parse_caption, guess_from_name, decode_latin1, merge_meta);
   обе реализации проверяются на одних примерах: tests/fixtures/meta_cases.json. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TrackMeta = api;
}(typeof self !== 'undefined' ? self : this, () => {
  'use strict';

  const CAPTION_SPLIT = /\s+[-–—]\s+/;

  // «Исполнитель - Название» -> {artist, title}; делится по первому тире в окружении пробелов.
  function parseCaption(caption) {
    const text = (caption || '').trim();
    const m = CAPTION_SPLIT.exec(text);
    if (!m) return null;
    const artist = text.slice(0, m.index).trim();
    const title = text.slice(m.index + m[0].length).trim();
    return artist && title ? { artist, title } : null;
  }

  // Имя файла без тегов: «Кино_-_Кукушка.mp3» -> {artist: 'Кино', title: 'Кукушка'}.
  function guessFromName(name) {
    const stem = String(name || '').replace(/\.[^.]+$/, '').replace(/_/g, ' ').trim();
    return parseCaption(stem) || { artist: null, title: stem || null };
  }

  // ISO-8859-1 побайтно: TextDecoder('latin1') в браузерах — это windows-1252, он портит байты 0x80–0x9F.
  function latin1(bytes) {
    let out = '';
    for (let i = 0; i < bytes.length; i += 8192) out += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    return out;
  }

  const isLetter = (c) => c >= 0xc0 || c === 0xa8 || c === 0xb8; // кириллица и Ёё в cp1251
  const PUNCT = new Set([0x85, 0x91, 0x92, 0x93, 0x94, 0x96, 0x97, 0xab, 0xb9, 0xbb]); // … ‘ ’ “ ” – — « № »

  // Текст «latin-1» из ID3. Старые русские MP3 пишут так cp1251: если все высокие байты — кириллица (и
  // типографские знаки cp1251), читаем как cp1251, кроме случаев вроде «Beyoncé» (дал бы «Beyoncщ»).
  function decodeLatin1(bytes) {
    const high = Array.prototype.filter.call(bytes, (c) => c >= 0x80);
    if (high.some(isLetter) && high.every((c) => isLetter(c) || PUNCT.has(c))) {
      const fixed = new TextDecoder('windows-1251').decode(bytes);
      if (!/[A-Za-z][А-яЁё]|[А-яЁё][A-Za-z]/.test(fixed)) return fixed;
    }
    return latin1(bytes);
  }

  // Правка важнее тега из файла (ключ есть в edit — поле правили, '' — очистить); для названия и
  // исполнителя, если пусто и там и там, — запасной вариант из имени файла.
  function mergeMeta(edit, tags, fallback) {
    const e = edit || {};
    const t = tags || {};
    const f = fallback || {};
    const pick = (k) => {
      if (k in e && e[k] != null) return String(e[k]).trim() || null;
      return t[k] || null;
    };
    return {
      title: pick('title') || f.title || null,
      artist: pick('artist') || f.artist || null,
      album: pick('album'),
      year: pick('year'),
    };
  }

  return { parseCaption, guessFromName, decodeLatin1, mergeMeta };
}));

// Проверка bot/web/static/meta.js на общих примерах (те же, что для bot/audio.py). Запуск: node tests/js/check_meta.cjs
const path = require('path');
const fs = require('fs');
const meta = require(path.join(__dirname, '../../bot/web/static/meta.js'));
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, '../fixtures/meta_cases.json'), 'utf8'));

const failures = [];
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
for (const c of cases.guess) {
  const got = meta.guessFromName(c.name);
  const want = { artist: c.artist, title: c.title };
  if (!same(got, want)) failures.push({ guess: c.name, got, want });
}
for (const c of cases.latin1) {
  const got = meta.decodeLatin1(new Uint8Array(Buffer.from(c.hex, 'hex')));
  if (got !== c.text) failures.push({ latin1: c.hex, got, want: c.text });
}
for (const c of cases.merge) {
  const got = meta.mergeMeta(c.edit, c.tags, c.fallback);
  if (!same(got, c.expect)) failures.push({ merge: c, got });
}
if (failures.length) {
  console.log(JSON.stringify(failures, null, 1));
  process.exit(1);
}
console.log(`ok: ${cases.guess.length + cases.latin1.length + cases.merge.length} примеров`);

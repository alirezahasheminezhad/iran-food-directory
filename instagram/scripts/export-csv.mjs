import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA_DIR = path.join(ROOT, 'data');
const MAIN = path.join(DATA_DIR, 'instagram-pages.json');
const DDG = path.join(DATA_DIR, 'instagram-pages-ddg.json');
const BT = path.join(DATA_DIR, 'instagram-pages-bt.json');
const T118 = path.join(DATA_DIR, 'instagram-pages-t118.json');
const CSV = path.join(DATA_DIR, 'instagram-pages.csv');

function load(f) { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return []; } }
const byHandle = new Map();
for (const r of [...load(MAIN), ...load(DDG), ...load(BT), ...load(T118)]) {
  const prev = byHandle.get(r.handle);
  if (prev) {
    prev.phones = [...new Set([...(prev.phones || []), ...(r.phones || [])])];
    if (!prev.bio && r.bio) prev.bio = r.bio;
    if (!prev.followers && r.followers) prev.followers = r.followers;
    prev.cities = [...new Set([...(prev.cities || []), ...(r.cities || [])])];
    prev.categories = [...new Set([...(prev.categories || []), ...(r.categories || [])])];
  } else byHandle.set(r.handle, { ...r });
}
const rows = [...byHandle.values()].sort((a, b) => (b.followers || 0) - (a.followers || 0));
const badMobile = /^09[68]\d{8}$/;
function cleanName(n) {
  return String(n || '').split(/\s*\(@/)[0].replace(/\s*•\s*Instagram.*$/i, '').replace(/\s*Instagram.*$/i, '').replace(/[\u200e\u200f]/g, '').trim();
}
for (const r of rows) {
  r.phones = (r.phones || []).filter((p) => /^0\d{10}$/.test(p) && !badMobile.test(p));
  r.name = cleanName(r.name);
}
function esc(v) {
  const s = String(v ?? '').replace(/"/g, '""');
  return /[",\n\r]/.test(s) ? `"${s}"` : s;
}
const header = ['handle', 'name', 'url', 'phones', 'followers', 'cities', 'categories', 'bio', 'source', 'collected_at'];
const lines = [header.join(',')];
for (const r of rows) {
  lines.push([
    r.handle, cleanName(r.name), r.url,
    (r.phones || []).join('; '), r.followers || '',
    (r.cities || []).join(' / '), (r.categories || []).join(' / '),
    (r.bio || '').replace(/\s+/g, ' ').trim(), r.source || '', r.collected_at || '',
  ].map(esc).join(','));
}
fs.writeFileSync(CSV, '\uFEFF' + lines.join('\r\n'), 'utf8');
fs.writeFileSync(MAIN, JSON.stringify(rows, null, 1));
const wp = rows.filter((r) => r.phones && r.phones.length).length;
console.log(`CSV written: ${CSV}`);
console.log(`total pages=${rows.length}, with-phone=${wp}`);
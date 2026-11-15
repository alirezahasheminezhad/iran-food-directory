import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA_DIR = path.join(ROOT, 'data');
const OUT_FILE = path.join(DATA_DIR, 'instagram-pages.json');

const faDigits = '۰۱۲۳۴۵۶۷۸۹';
const arDigits = '٠١٢٣٤٥٦٧٨٩';
function normDigits(s) {
  return String(s).replace(/[۰-۹]/g, (d) => faDigits.indexOf(d)).replace(/[٠-٩]/g, (d) => arDigits.indexOf(d));
}
function extractPhones(text) {
  const t = normDigits(text);
  const found = new Set();
  for (let tok of t.split(/[\s"',;:|]+/)) {
    tok = tok.replace(/[().\-]/g, '');
    if (!/^\+?\d{9,15}$/.test(tok)) continue;
    let run = tok;
    if (run.startsWith('+98')) run = '0' + run.slice(3);
    else if (run.startsWith('0098')) run = '0' + run.slice(4);
    else if (run.startsWith('98') && run.length === 12) run = '0' + run.slice(2);
    if (/^09\d{9}$/.test(run)) found.add(run);
    else if (/^0[1-8]\d{9}$/.test(run)) found.add(run);
    else if (/^9\d{9}$/.test(run)) found.add('0' + run);
  }
  return [...found];
}
function extractFollowers(text) {
  const m = normDigits(text).match(/([\d,.]+)\s*([KkMm]?)\s*Followers/i);
  if (!m) return '';
  let n = parseFloat(m[1].replace(/,/g, ''));
  if (/k/i.test(m[2])) n *= 1e3;
  if (/m/i.test(m[2])) n *= 1e6;
  return Math.round(n);
}
function cleanName(name, handle) {
  let n = String(name || '');
  n = n.split('(@')[0];
  n = n.replace(/\(@[A-Za-z0-9_.]+\)\s*•?\s*Instagram.*$/i, '');
  n = n.replace(/\s*•\s*Instagram.*$/i, '');
  n = n.replace(/Instagram.*$/i, '');
  return n.replace(/[\u200e\u200f]/g, '').trim();
}

function loadExisting() {
  try { return JSON.parse(fs.readFileSync(OUT_FILE, 'utf8')); } catch { return []; }
}

// input file: JSON array of {handle, name?, url?, bio?, phones?, followers?, city, category}
const inFile = process.argv[2];
if (!inFile) { console.error('usage: node ingest.mjs <batch.json>'); process.exit(1); }
const batch = JSON.parse(fs.readFileSync(inFile, 'utf8'));
const existing = loadExisting();
const byHandle = new Map(existing.map((r) => [r.handle, r]));
let added = 0, withPhone = 0;
for (const r of batch) {
  const handle = String(r.handle || '').toLowerCase().trim();
  if (!handle || ['p', 'reel', 'reels', 'explore', 'accounts', 'stories', 'tv', 'hashtag', 'direct'].includes(handle)) continue;
  const text = `${r.name || ''} ${r.bio || ''}`;
  const phones = [...new Set([...(r.phones || []), ...extractPhones(text)])];
  const followers = r.followers || extractFollowers(r.bio || '') || '';
  const city = r.city || '';
  const cat = r.category || '';
  const prev = byHandle.get(handle);
  if (prev) {
    prev.phones = [...new Set([...(prev.phones || []), ...phones])];
    if (!prev.bio && r.bio) prev.bio = r.bio;
    if (!prev.followers && followers) prev.followers = followers;
    if (city && !prev.cities.includes(city)) prev.cities.push(city);
    if (cat && !prev.categories.includes(cat)) prev.categories.push(cat);
    if (r.name && (!prev.name || prev.name.length < cleanName(r.name, handle).length)) prev.name = cleanName(r.name, handle);
  } else {
    byHandle.set(handle, {
      handle,
      name: cleanName(r.name, handle),
      url: r.url || `https://www.instagram.com/${handle}/`,
      bio: r.bio || '',
      phones,
      followers,
      cities: city ? [city] : [],
      categories: cat ? [cat] : [],
      source: 'instagram (via web search)',
      collected_at: new Date().toISOString().slice(0, 10),
    });
    added++;
    if (phones.length) withPhone++;
  }
}
fs.writeFileSync(OUT_FILE, JSON.stringify([...byHandle.values()], null, 1));
console.log(`ingested ${batch.length} -> +${added} new (+${withPhone} with phone), total ${byHandle.size}`);
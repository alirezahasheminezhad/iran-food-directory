import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA_DIR = path.join(ROOT, 'data');
const OUT_FILE = path.join(DATA_DIR, 'instagram-pages-t118.json');
const VISITED_FILE = path.join(DATA_DIR, 't118-visited.json');
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const KEYWORDS = [
  { kw: 'عطاری', label: 'ادویه و عطاری' },
  { kw: 'ادویه', label: 'ادویه و عطاری' },
  { kw: 'ادویه جات', label: 'ادویه و عطاری' },
  { kw: 'زعفران', label: 'ادویه و عطاری' },
  { kw: 'خشکبار', label: 'ادویه و عطاری' },
  { kw: 'پخش مواد غذایی', label: 'پخش عمده مواد غذایی' },
  { kw: 'پخش عمده مواد غذایی', label: 'پخش عمده مواد غذایی' },
  { kw: 'بنکداری', label: 'پخش عمده مواد غذایی' },
  { kw: 'عمده فروشی مواد غذایی', label: 'پخش عمده مواد غذایی' },
];

const faDigits = '۰۱۲۳۴۵۶۷۸۹';
const arDigits = '٠١٢٣٤٥٦٧٨٩';
function normDigits(s) { return String(s).replace(/[۰-۹]/g, (d) => faDigits.indexOf(d)).replace(/[٠-٩]/g, (d) => arDigits.indexOf(d)); }
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
function handleFromUrl(url) {
  const m = String(url).match(/instagram\.com\/([A-Za-z0-9_.]+)/i);
  if (!m) return null;
  const h = m[1].toLowerCase();
  if (['p', 'reel', 'reels', 'explore', 'accounts', 'stories', 'tv', 'hashtag', 'direct', 'top118'].includes(h)) return null;
  return h;
}
function decode(s) { return String(s).replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/&nbsp;/g, ' ').replace(/[\u200e\u200f]/g, '').trim(); }

async function get(url, tries = 3) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(url, { headers: { 'User-Agent': UA, 'Accept-Language': 'fa,en' } });
      if (res.status === 429 || res.status >= 500) { await sleep(8000 * (i + 1)); continue; }
      if (!res.ok) return null;
      return await res.text();
    } catch { await sleep(4000 * (i + 1)); }
  }
  return null;
}
function loadJson(f, fallback) { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return fallback; } }

const args = process.argv.slice(2);
const PERSIST = process.env.IG_PERSIST === '1';
const START = Date.now();
const MAX_HOURS = parseFloat(process.env.IG_MAX_HOURS || '6');
const MAX_PAGE = parseInt((args.find((a) => a.startsWith('--pages=')) || '').split('=')[1] || '8', 10);

const byHandle = new Map(loadJson(OUT_FILE, []).map((r) => [r.handle, r]));
const visited = new Set(loadJson(VISITED_FILE, []));
let detailFetched = 0, newCount = 0, phoneCount = 0;
function save() {
  fs.writeFileSync(OUT_FILE, JSON.stringify([...byHandle.values()], null, 1));
  fs.writeFileSync(VISITED_FILE, JSON.stringify([...visited]));
}

async function processDetail(url, label) {
  if (visited.has(url)) return;
  visited.add(url);
  const html = await get(url);
  detailFetched++;
  if (!html) return;
  const igUrl = (html.match(/instagram\.com\/([A-Za-z0-9_.]+)/i) || [])[0] || '';
  const handle = handleFromUrl(igUrl);
  if (!handle) return;
  const title = decode((html.match(/<title>([^<]*)<\/title>/) || [])[1] || '');
  const name = decode((html.match(/<h1[^>]*>([^<]+)<\/h1>/) || [])[1] || title.split('،')[0].split('|')[0]);
  let city = '';
  if (title.includes('،')) city = decode(title.split('،')[1].split('|')[0]);
  const text = html.replace(/<script[\s\S]*?<\/script>/g, ' ').replace(/<style[\s\S]*?<\/style>/g, ' ').replace(/<[^>]+>/g, ' ');
  const phones = extractPhones(text);
  const prev = byHandle.get(handle);
  if (prev) {
    prev.phones = [...new Set([...(prev.phones || []), ...phones])];
    if (city && !prev.cities.includes(city)) prev.cities.push(city);
    if (!prev.categories.includes(label)) prev.categories.push(label);
  } else {
    byHandle.set(handle, {
      handle, name, url: `https://www.instagram.com/${handle}/`, bio: '', phones, followers: '',
      cities: city ? [city] : [], categories: [label],
      source: 'instagram (via top118)', collected_at: new Date().toISOString().slice(0, 10),
    });
    newCount++;
    if (phones.length) phoneCount++;
  }
}

const timeUp = () => PERSIST && (Date.now() - START) > MAX_HOURS * 3600 * 1000;
let pass = 0;
outer:
while (true) {
  pass++;
  for (const { kw, label } of KEYWORDS) {
    const seen = new Set();
    for (let page = 1; page <= MAX_PAGE; page++) {
      if (timeUp()) break outer;
      const url = `https://www.top118.ir/jobs/?search=${encodeURIComponent(kw)}&page=${page}`;
      const html = await get(url);
      if (!html) break;
      const urls = [...new Set([...html.matchAll(/\/jobs\/view\/(\d+)\/([^"'/]+)\//g)].map((m) => `/jobs/view/${m[1]}/${m[2]}/`))];
      const fresh = urls.filter((u) => !seen.has(u));
      fresh.forEach((u) => seen.add(u));
      if (fresh.length === 0) break;
      for (const u of fresh) {
        await processDetail('https://www.top118.ir' + encodeURI(u), label);
        if (detailFetched % 25 === 0) save();
        await sleep(1000);
      }
      console.log(`[p${pass}] ${kw} page${page}: ${fresh.length} biz, total IG=${byHandle.size} (+${newCount})`);
      save();
      await sleep(1000);
    }
  }
  if (!PERSIST || timeUp()) break;
  console.log(`pass ${pass} done, total IG=${byHandle.size}; next pass in 60s`);
  await sleep(60000);
}
save();
console.log(`DONE. passes=${pass}, detailFetched=${detailFetched}, total IG=${byHandle.size}, new=${newCount}, new-with-phone=${phoneCount}`);
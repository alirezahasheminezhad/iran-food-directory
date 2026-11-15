import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA_DIR = path.join(ROOT, 'data');
const OUT_FILE = path.join(DATA_DIR, 'instagram-pages-bt.json');
const VISITED_FILE = path.join(DATA_DIR, 'bt-visited.json');
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CATEGORIES = [
  { slug: 'عطاری', label: 'ادویه و عطاری' },
  { slug: 'ادویه-فروشی', label: 'ادویه و عطاری' },
  { slug: 'پخش-مواد-غذایی', label: 'پخش عمده مواد غذایی' },
  { slug: 'بنکداری', label: 'پخش عمده مواد غذایی' },
  { slug: 'مواد-غذایی', label: 'پخش عمده مواد غذایی' },
];
const CITIES = ['تهران', 'مشهد', 'اصفهان', 'شیراز', 'تبریز', 'کرج', 'اهواز', 'قم', 'کرمانشاه', 'ارومیه', 'رشت', 'زاهدان', 'همدان', 'کرمان', 'یزد', 'اردبیل', 'بندرعباس', 'قزوین', 'سنندج', 'خرم آباد', 'گرگان', 'ساری', 'بجنورد', 'بیرجند', 'بوشهر', 'زنجان', 'سمنان', 'ایلام', 'یاسوج', 'شهرکرد', 'اراک'];

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
  if (['p', 'reel', 'reels', 'explore', 'accounts', 'stories', 'tv', 'hashtag', 'direct', 'behtarino_com'].includes(h)) return null;
  return h;
}
function cleanName(n) {
  return String(n || '').split(' + ')[0].split(' | ')[0].replace(/شماره تلفن.*$/i, '').replace(/[\u200e\u200f]/g, '').trim();
}
function rscPayload(html) {
  const chunks = [...html.matchAll(/self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)/g)].map((m) => m[1]);
  let p = chunks.join('');
  try { p = JSON.parse('"' + p + '"'); } catch {}
  return p;
}

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
const MAX_HOURS = parseFloat(process.env.IG_MAX_HOURS || '10');
const cityFilter = args.filter((a) => !a.startsWith('--'));
const cities = cityFilter.length ? cityFilter : CITIES;
const MAX_PAGE = parseInt((args.find((a) => a.startsWith('--pages=')) || '').split('=')[1] || '5', 10);

const byHandle = new Map(loadJson(OUT_FILE, []).map((r) => [r.handle, r]));
const visited = new Set(loadJson(VISITED_FILE, []));
let bizFetched = 0, newCount = 0, phoneCount = 0;

function save() {
  fs.writeFileSync(OUT_FILE, JSON.stringify([...byHandle.values()], null, 1));
  fs.writeFileSync(VISITED_FILE, JSON.stringify([...visited]));
}

async function processBusiness(bizUrl, city, catLabel) {
  if (visited.has(bizUrl)) return;
  visited.add(bizUrl);
  const html = await get('https://behtarino.com' + encodeURI(bizUrl));
  bizFetched++;
  if (!html) return;
  const p = rscPayload(html);
  const igUrl = (p.match(/"instagramUrl"\s*:\s*"([^"]+)"/) || [])[1] || '';
  const handle = handleFromUrl(igUrl);
  if (!handle) return;
  const ogName = (html.match(/<meta property="og:title" content="([^"]+)"/) || [])[1] || '';
  const phones = extractPhones(p);
  const prev = byHandle.get(handle);
  if (prev) {
    prev.phones = [...new Set([...(prev.phones || []), ...phones])];
    if (!prev.cities.includes(city)) prev.cities.push(city);
    if (!prev.categories.includes(catLabel)) prev.categories.push(catLabel);
  } else {
    byHandle.set(handle, {
      handle, name: cleanName(ogName), url: `https://www.instagram.com/${handle}/`,
      bio: '', phones, followers: '', cities: [city], categories: [catLabel],
      source: 'instagram (via behtarino)', collected_at: new Date().toISOString().slice(0, 10),
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
  for (const city of cities) {
    for (const cat of CATEGORIES) {
      const seenBiz = new Set();
      for (let page = 1; page <= MAX_PAGE; page++) {
        if (timeUp()) { console.log(`runtime cap reached`); break outer; }
        const url = `https://behtarino.com/r/${encodeURIComponent(cat.slug)}/${encodeURIComponent(city)}?page=${page}`;
        const html = await get(url);
        if (!html) break;
        const p = rscPayload(html);
        const bizUrls = [...new Set([...p.matchAll(/\/p\/([a-z0-9]+~[^"\\\/]+)/g)].map((m) => '/p/' + m[1]))];
        const fresh = bizUrls.filter((u) => !seenBiz.has(u));
        fresh.forEach((u) => seenBiz.add(u));
        if (fresh.length === 0) break;
        for (const b of fresh) {
          await processBusiness(b, city, cat.label);
          if (bizFetched % 25 === 0) save();
          await sleep(1000);
        }
        console.log(`[p${pass}] ${city}/${cat.slug} page${page}: ${fresh.length} biz, total IG=${byHandle.size} (+${newCount})`);
        save();
        await sleep(1200);
      }
    }
  }
  if (!PERSIST || timeUp()) break;
  console.log(`pass ${pass} done, total IG=${byHandle.size}; next pass in 60s`);
  await sleep(60000);
}
save();
console.log(`DONE. passes=${pass}, bizFetched=${bizFetched}, total IG pages=${byHandle.size}, new=${newCount}, new-with-phone=${phoneCount}`);
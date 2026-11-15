import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA_DIR = path.join(ROOT, 'data');
const OUT_FILE = path.join(DATA_DIR, process.env.IG_OUT || 'instagram-pages-ddg.json');
const SEED_FILE = path.join(DATA_DIR, 'instagram-pages.json');
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';

const SPICE_KEYWORDS = [
  'ادویه عمده', 'عمده فروشی ادویه', 'ادویه فروشی', 'ادویه جات',
  'عطاری عمده', 'پخش ادویه', 'ادویه و چاشنی', 'زعفران عمده', 'خشکبار و ادویه',
];
const WHOLESALE_KEYWORDS = [
  'پخش عمده مواد غذایی', 'پخش مواد غذایی', 'بنکداری', 'عمده فروشی مواد غذایی',
  'بنکدار مواد غذایی', 'پخش مویرگی مواد غذایی', 'عمده فروشی حبوبات', 'پخش برنج عمده',
];

const CITIES = [
  'تهران', 'مشهد', 'اصفهان', 'شیراز', 'تبریز', 'کرج', 'اهواز', 'قم',
  'کرمانشاه', 'ارومیه', 'رشت', 'زاهدان', 'همدان', 'کرمان', 'یزد', 'اردبیل',
  'بندرعباس', 'قزوین', 'سنندج', 'خرم آباد', 'گرگان', 'ساری', 'بجنورد', 'بیرجند',
  'بوشهر', 'زنجان', 'سمنان', 'ایلام', 'یاسوج', 'شهرکرد', 'اراک',
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const faDigits = '۰۱۲۳۴۵۶۷۸۹';
const arDigits = '٠١٢٣٤٥٦٧٨٩';
function normDigits(s) {
  return s.replace(/[۰-۹]/g, (d) => faDigits.indexOf(d))
          .replace(/[٠-٩]/g, (d) => arDigits.indexOf(d));
}
function decodeEntities(s) {
  return s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
          .replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/&nbsp;/g, ' ');
}
function stripTags(s) { return decodeEntities(s.replace(/<[^>]+>/g, '')).replace(/[\u200e\u200f]/g, '').trim(); }

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
function handleFromUrl(url) {
  const m = url.match(/instagram\.com\/([A-Za-z0-9_.]+)\/?/i);
  if (!m) return null;
  const h = m[1].toLowerCase();
  if (['p', 'reel', 'reels', 'explore', 'accounts', 'stories', 'tv', 'hashtag', 'direct'].includes(h)) return null;
  return h;
}
function nameFromTitle(title) {
  return title.replace(/\(@[A-Za-z0-9_.]+\)\s*•?\s*Instagram.*$/i, '')
              .replace(/\s*•\s*Instagram.*$/i, '').trim();
}

function parseResults(html) {
  const titles = [...html.matchAll(/class="result__a"\s+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)];
  const snippets = [...html.matchAll(/class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g)];
  const out = [];
  titles.forEach((t, i) => {
    const href = decodeEntities(t[1]);
    const uddg = href.match(/uddg=([^&]+)/);
    const realUrl = uddg ? decodeURIComponent(uddg[1]) : href;
    const handle = handleFromUrl(realUrl);
    if (!handle) return;
    const title = stripTags(t[2]);
    const snippet = snippets[i] ? stripTags(snippets[i][1]) : '';
    out.push({
      handle, url: `https://www.instagram.com/${handle}/`,
      name: nameFromTitle(title), bio: snippet,
      phones: extractPhones(title + ' ' + snippet),
      followers: extractFollowers(snippet),
    });
  });
  return out;
}

async function search(query) {
  const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
  const res = await fetch(url, { headers: { 'User-Agent': UA, 'Accept-Language': 'fa,en' } });
  const html = await res.text();
  if (/anomaly|captcha|unusual traffic/i.test(html)) throw new Error('CAPTCHA');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return parseResults(html);
}

function loadExisting() {
  try { return JSON.parse(fs.readFileSync(SEED_FILE, 'utf8')); } catch { return []; }
}

const args = process.argv.slice(2);
const PERSIST = process.env.IG_PERSIST === '1';
const START = Date.now();
const MAX_HOURS = parseFloat(process.env.IG_MAX_HOURS || '8');
const cityFilter = args.filter((a) => !a.startsWith('--'));
const maxQueries = parseInt((args.find((a) => a.startsWith('--max=')) || '').split('=')[1] || '0', 10);
const keywords = [...SPICE_KEYWORDS, ...WHOLESALE_KEYWORDS];
const cities = cityFilter.length ? cityFilter : CITIES;

const existing = loadExisting();
const byHandle = new Map(existing.map((r) => [r.handle, r]));
let queries = 0, newCount = 0, phoneCount = 0, consecCaptcha = 0, backoff = 60000;

const timeUp = () => PERSIST && (Date.now() - START) > MAX_HOURS * 3600 * 1000;

async function searchRetry(query) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try { return await search(query); }
    catch (e) {
      if (e.message === 'CAPTCHA') throw e;
      if (attempt < 2) { await sleep(5000 * (attempt + 1)); continue; }
      throw e;
    }
  }
}

let pass = 0;
outer:
while (true) {
  pass++;
  for (const city of cities) {
    for (const kw of keywords) {
      if (maxQueries && queries >= maxQueries) break outer;
      if (timeUp()) { console.log(`  runtime cap ${MAX_HOURS}h reached — stopping. total=${byHandle.size}`); break outer; }
      const cat = SPICE_KEYWORDS.includes(kw) ? 'ادویه و عطاری' : 'پخش عمده مواد غذایی';
      const query = `site:instagram.com ${kw} ${city}`;
      let results = [];
      try { results = await searchRetry(query); consecCaptcha = 0; backoff = 60000; }
      catch (e) {
        if (e.message === 'CAPTCHA') {
          consecCaptcha++;
          if (!PERSIST && consecCaptcha >= 6) { console.log(`  6 consecutive captchas — stopping to avoid hammering. total=${byHandle.size}`); break outer; }
          console.log(`  captcha #${consecCaptcha} at query ${queries}, backing off ${backoff / 1000}s`);
          await sleep(backoff); backoff = Math.min(backoff * 2, PERSIST ? 900000 : 600000);
          try { results = await searchRetry(query); consecCaptcha = 0; backoff = 60000; } catch { results = []; }
        }
        else console.log(`  [${city}/${kw}] ${e.message}`);
      }
      queries++;
      let added = 0;
      for (const r of results) {
        const prev = byHandle.get(r.handle);
        if (prev) {
          prev.phones = [...new Set([...(prev.phones || []), ...r.phones])];
          if (!prev.bio && r.bio) prev.bio = r.bio;
          if (!prev.followers && r.followers) prev.followers = r.followers;
          if (!prev.cities.includes(city)) prev.cities.push(city);
          if (!prev.categories.includes(cat)) prev.categories.push(cat);
        } else {
          byHandle.set(r.handle, {
            handle: r.handle, name: r.name, url: r.url, bio: r.bio,
            phones: r.phones, followers: r.followers,
            cities: [city], categories: [cat],
            source: 'instagram (via duckduckgo)', collected_at: new Date().toISOString().slice(0, 10),
          });
          added++; newCount++;
          if (r.phones.length) phoneCount++;
        }
      }
      console.log(`[p${pass} q${queries}] ${city} / ${kw}: ${results.length} results, +${added} new (total ${byHandle.size})`);
      fs.writeFileSync(OUT_FILE, JSON.stringify([...byHandle.values()], null, 1));
      await sleep(20000);
    }
  }
  if (!PERSIST || timeUp()) break;
  console.log(`pass ${pass} complete, total=${byHandle.size}; next pass in 60s`);
  await sleep(60000);
}
console.log(`DONE. passes=${pass}, queries=${queries}, total pages=${byHandle.size}, new=${newCount}, new-with-phone=${phoneCount}`);
// Cloudflare Pages Function: GET /api/draws -> 最新开奖 JSON
// （双色球 100 / 大乐透 100 / 快乐8 50 / 福彩3D 50 / 七乐彩 50 / 七星彩 50 / 排列三 50 / 排列五 50 期）
// 数据源：data.17500.cn 公开 txt；边缘缓存 30 分钟；上游失败返回 502，页面自动回退到页内数据。
// txt 行格式：期号 日期 号码…（其余字段忽略，只取每行首段）；数字彩（3d/pl3/pl5/7xc 前 6 位）按位保序、可重复。
const SOURCES = [
  { key: 'ssq', url: 'http://data.17500.cn/ssq_asc.txt', count: 100, main: 6, mainMax: 33, sub: 1, subMax: 16 },
  { key: 'dlt', url: 'http://data.17500.cn/dlt_asc.txt', count: 100, main: 5, mainMax: 35, sub: 2, subMax: 12 },
  { key: 'kl8', url: 'http://data.17500.cn/kl8_asc.txt', count: 50, main: 20, mainMax: 80, sub: 0, subMax: 0 },
  { key: '3d', url: 'http://data.17500.cn/3d_asc.txt', count: 50, main: 3, mainMin: 0, mainMax: 9, sub: 0, subMax: 0, ordered: true, unique: false },
  { key: '7lc', url: 'http://data.17500.cn/7lc_asc.txt', count: 50, main: 7, mainMax: 30, sub: 1, subMax: 30, subExcl: true },
  { key: '7xc', url: 'http://data.17500.cn/7xc_asc.txt', count: 50, main: 6, mainMin: 0, mainMax: 9, sub: 1, subMin: 0, subMax: 14, ordered: true, unique: false },
  { key: 'pl3', url: 'http://data.17500.cn/pl3_asc.txt', count: 50, main: 3, mainMin: 0, mainMax: 9, sub: 0, subMax: 0, ordered: true, unique: false },
  { key: 'pl5', url: 'http://data.17500.cn/pl5_asc.txt', count: 50, main: 5, mainMin: 0, mainMax: 9, sub: 0, subMax: 0, ordered: true, unique: false },
];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const CACHE_TTL_MS = 30 * 60 * 1000;

function parseDraws(text, spec) {
  const mainMin = spec.mainMin !== undefined ? spec.mainMin : 1;
  const subMin = spec.subMin !== undefined ? spec.subMin : 1;
  const byIssue = new Map();
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim().split(/[\s,]+/);
    if (t.length < 3 || !DATE_RE.test(t[1])) continue;
    const need = spec.main + spec.sub;
    const nums = [];
    let ok = true;
    for (let i = 0; i < need; i++) {
      const n = parseInt(t[2 + i], 10);
      if (!Number.isInteger(n)) { ok = false; break; }
      nums.push(n);
    }
    if (!ok) continue;
    const main = nums.slice(0, spec.main);
    if (!spec.ordered) main.sort((a, b) => a - b);
    if (main.some(n => n < mainMin || n > spec.mainMax)) continue;
    if (spec.unique !== false && new Set(main).size !== main.length) continue;
    const sub = nums.slice(spec.main);
    if (spec.sub === 1) {
      if (sub[0] < subMin || sub[0] > spec.subMax) continue;
      if (spec.subExcl && main.indexOf(sub[0]) >= 0) continue;
      byIssue.set(t[0], [t[0], t[1], main, sub[0]]);
    } else if (spec.sub === 2) {
      const s = sub.slice().sort((a, b) => a - b);
      if (s.some(n => n < subMin || n > spec.subMax) || new Set(s).size !== s.length) continue;
      byIssue.set(t[0], [t[0], t[1], main, s]);
    } else {
      byIssue.set(t[0], [t[0], t[1], main]);
    }
  }
  const rows = [...byIssue.values()];
  rows.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return rows.slice(-spec.count);
}

async function fetchText(url) {
  let res = null;
  try { res = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } }); } catch (e) { res = null; }
  if (res && res.ok) return await res.text();
  const httpsUrl = url.replace('http://', 'https://');
  if (httpsUrl !== url) {
    res = await fetch(httpsUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (res.ok) return await res.text();
  }
  throw new Error('fetch failed: ' + url);
}

export async function onRequest(context) {
  const cacheKey = new Request(context.request.url, { method: 'GET' });
  try {
    const cached = await caches.default.match(cacheKey);
    if (cached) {
      const data = await cached.clone().json();
      if (data.updatedAt && Date.now() - Date.parse(data.updatedAt) < CACHE_TTL_MS) return cached;
    }
  } catch (e) {}
  try {
    const out = {};
    for (const spec of SOURCES) {
      const rows = parseDraws(await fetchText(spec.url), spec);
      if (rows.length < spec.count) throw new Error(spec.key + ' only ' + rows.length + ' draws');
      out[spec.key] = rows;
    }
    out.updatedAt = new Date().toISOString();
    const resp = new Response(JSON.stringify(out), { headers: { 'Content-Type': 'application/json' } });
    try { await caches.default.put(cacheKey, resp.clone()); } catch (e) {}
    return resp;
  } catch (e) {
    return new Response(JSON.stringify({ error: String((e && e.message) || e) }), { status: 502, headers: { 'Content-Type': 'application/json' } });
  }
}

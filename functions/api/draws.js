// Cloudflare Pages Function: GET /api/draws -> 最新开奖 JSON（双色球 100 / 大乐透 100 / 快乐8 50 期）
// 数据源：data.17500.cn 公开 txt；边缘缓存 30 分钟；上游失败返回 502，页面自动回退到页内数据。
const SOURCES = [
  { key: 'ssq', url: 'http://data.17500.cn/ssq_asc.txt', count: 100, main: 6, mainMax: 33, sub: 1, subMax: 16 },
  { key: 'dlt', url: 'http://data.17500.cn/dlt_asc.txt', count: 100, main: 5, mainMax: 35, sub: 2, subMax: 12 },
  { key: 'kl8', url: 'http://data.17500.cn/kl8_asc.txt', count: 50, main: 20, mainMax: 80, sub: 0, subMax: 0 },
];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const CACHE_TTL_MS = 30 * 60 * 1000;

function parseDraws(text, spec) {
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
    const main = nums.slice(0, spec.main).sort((a, b) => a - b);
    if (main.some(n => n < 1 || n > spec.mainMax)) continue;
    if (new Set(main).size !== main.length) continue;
    const sub = nums.slice(spec.main);
    if (spec.sub === 1) {
      if (sub[0] < 1 || sub[0] > spec.subMax) continue;
      byIssue.set(t[0], [t[0], t[1], main, sub[0]]);
    } else if (spec.sub === 2) {
      const s = sub.slice().sort((a, b) => a - b);
      if (s.some(n => n < 1 || n > spec.subMax) || new Set(s).size !== s.length) continue;
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

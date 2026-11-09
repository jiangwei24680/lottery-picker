// Cloudflare Pages Function: GET /api/draws -> 最新开奖 JSON
// （双色球 100 / 大乐透 100 / 快乐8 50 / 福彩3D 50 / 七乐彩 50 / 七星彩 50 / 排列三 50 / 排列五 50 期）
// 数据源：data.17500.cn 公开 txt；边缘缓存 30 分钟；上游失败返回 502，页面自动回退到页内数据。
// txt 行格式：期号 日期 号码…（其余字段忽略，只取每行首段）；数字彩（3d/pl3/pl5/7xc 前 6 位）按位保序、可重复。
// 奖金列（2026-11-09 核实）：号码后附带 [注数, 单注奖金] 对子；prizeStart 为对子在整行 token（期号0/日期1/号码2…）里的起始下标。
// ssq 17 起 7 对（一～六等 + 派奖六等奖翻番）；dlt 18 起 12 对（九个基本奖级 + 三个追加），单期占两行须先拼行；
// 7lc 12 起 7 对；7xc 11 起 6 对；pl3 6 起 3 对（直选/组三/组六）；pl5 8 起 1 对；3d 11 起 3 对（直选/组三/组六，注数恒为 0 只取金额）。
// kl8 二期（单期跨多行、金额带小数，暂不解析）。
const SOURCES = [
  { key: 'ssq', url: 'http://data.17500.cn/ssq_asc.txt', count: 100, main: 6, mainMax: 33, sub: 1, subMax: 16, prizeStart: 17, prizeCount: 7 },
  { key: 'dlt', url: 'http://data.17500.cn/dlt_asc.txt', count: 100, main: 5, mainMax: 35, sub: 2, subMax: 12, prizeStart: 18, prizeCount: 12 },
  { key: 'kl8', url: 'http://data.17500.cn/kl8_asc.txt', count: 50, main: 20, mainMax: 80, sub: 0, subMax: 0 },
  { key: '3d', url: 'http://data.17500.cn/3d_asc.txt', count: 50, main: 3, mainMin: 0, mainMax: 9, sub: 0, subMax: 0, ordered: true, unique: false, prizeStart: 11, prizeCount: 3 },
  { key: '7lc', url: 'http://data.17500.cn/7lc_asc.txt', count: 50, main: 7, mainMax: 30, sub: 1, subMax: 30, subExcl: true, prizeStart: 12, prizeCount: 7 },
  { key: '7xc', url: 'http://data.17500.cn/7xc_asc.txt', count: 50, main: 6, mainMin: 0, mainMax: 9, sub: 1, subMin: 0, subMax: 14, ordered: true, unique: false, prizeStart: 11, prizeCount: 6 },
  { key: 'pl3', url: 'http://data.17500.cn/pl3_asc.txt', count: 50, main: 3, mainMin: 0, mainMax: 9, sub: 0, subMax: 0, ordered: true, unique: false, prizeStart: 6, prizeCount: 3 },
  { key: 'pl5', url: 'http://data.17500.cn/pl5_asc.txt', count: 50, main: 5, mainMin: 0, mainMax: 9, sub: 0, subMax: 0, ordered: true, unique: false, prizeStart: 8, prizeCount: 1 },
];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const CACHE_TTL_MS = 30 * 60 * 1000;

function splitRecords(text) {
  // 按期号把物理行拼成逻辑行：以“期号 日期”开头的行是新一期，其余非空行是上一期的续行（大乐透单期占两行，快乐8 约四行）。
  const records = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim().split(/[\s,]+/);
    if (!t[0]) continue;
    if (t.length >= 2 && DATE_RE.test(t[1])) records.push(line.trim());
    else if (records.length) records[records.length - 1] += ' ' + line.trim();
  }
  return records;
}

function parsePrizes(t, spec) {
  // 从整行 token 取奖金对子 [注数, 单注奖金] × N；token 不足或解析失败返回 null（该期只有号码、无奖金数据）。
  if (!spec.prizeStart || !spec.prizeCount) return null;
  if (t.length < spec.prizeStart + 2 * spec.prizeCount) return null;
  const pairs = [];
  for (let i = 0; i < spec.prizeCount; i++) {
    const c = parseInt(t[spec.prizeStart + 2 * i], 10);
    const a = parseFloat(t[spec.prizeStart + 2 * i + 1]);
    if (!Number.isInteger(c) || c < 0 || !Number.isFinite(a) || a < 0) return null;
    pairs.push([c, a]);
  }
  return pairs;
}

function parseDraws(text, spec) {
  const mainMin = spec.mainMin !== undefined ? spec.mainMin : 1;
  const subMin = spec.subMin !== undefined ? spec.subMin : 1;
  const byIssue = new Map();
  for (const line of splitRecords(text)) {
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
    let row;
    if (spec.sub === 1) {
      if (sub[0] < subMin || sub[0] > spec.subMax) continue;
      if (spec.subExcl && main.indexOf(sub[0]) >= 0) continue;
      row = [t[0], t[1], main, sub[0]];
    } else if (spec.sub === 2) {
      const s = sub.slice().sort((a, b) => a - b);
      if (s.some(n => n < subMin || n > spec.subMax) || new Set(s).size !== s.length) continue;
      row = [t[0], t[1], main, s];
    } else {
      row = [t[0], t[1], main];
    }
    const prizes = parsePrizes(t, spec);
    if (prizes) row.push(prizes);
    byIssue.set(t[0], row);
  }
  const rows = [...byIssue.values()];
  rows.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return rows.slice(-spec.count);
}

async function fetchText(url) {
  // 429/5xx/网络异常时重试 3 次（2s/5s 短退避）；每次先 http、失败再试 https
  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    for (const u of [url, url.replace('http://', 'https://')]) {
      try {
        const res = await fetch(u, { headers: { 'User-Agent': 'Mozilla/5.0' } });
        if (res.ok) return await res.text();
        lastErr = new Error('HTTP ' + res.status + ': ' + u);
        if (res.status === 429 || res.status >= 500) break;
        throw lastErr;
      } catch (e) {
        lastErr = e;
      }
    }
    if (attempt === 2) break;
    await new Promise(r => setTimeout(r, attempt === 0 ? 2000 : 5000));
  }
  throw lastErr || new Error('fetch failed: ' + url);
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

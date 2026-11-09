#!/usr/bin/env python3
"""每晚定时刷新 lottery-picker 页内开奖数据（兜底层；打开页面实时更新走 functions/api/draws.js）。

数据源：data.17500.cn 公开 txt（与 index.html 页脚一致）。
只替换 index.html 里 __LOTTERY_DATA_START__/__LOTTERY_DATA_END__ 之间的 DATA 块
和 <span id="cutoff"> 的截至文字，其余内容一字不动；数据校验不过直接退出不写文件。
数字彩（3d/pl3/pl5/7xc 前 6 位）按位保序、可重复；3d 只取开奖号 3 位，试机号忽略。
仅用标准库。用法：python3 scripts/update_draws.py [index.html 路径，默认仓库根目录的 index.html]
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KEYS = ('ssq', 'dlt', 'kl8', '3d', '7lc', '7xc', 'pl3', 'pl5')
LABELS = {'ssq': '双色球', 'dlt': '大乐透', 'kl8': '快乐8', '3d': '福彩3D',
          '7lc': '七乐彩', '7xc': '七星彩', 'pl3': '排列三', 'pl5': '排列五'}
# 奖金列（2026-11-09 核实）：号码后附带 [注数, 单注奖金] 对子；prize_start 为对子在整行 token（期号0/日期1/号码2…）里的起始下标。
# ssq 17 起 7 对（一～六等 + 派奖六等奖翻番）；dlt 18 起 12 对（九个基本奖级 + 三个追加），单期占两行须先拼行；
# 7lc 12 起 7 对；7xc 11 起 6 对；pl3 6 起 3 对（直选/组三/组六）；pl5 8 起 1 对；3d 11 起 3 对（直选/组三/组六，注数恒为 0 只取金额）。
# kl8 二期（单期跨多行、金额带小数，暂不解析）。
SPECS = {
    'ssq': {'url': 'http://data.17500.cn/ssq_asc.txt', 'count': 100, 'main': 6, 'main_max': 33, 'sub': 1, 'sub_max': 16, 'prize_start': 17, 'prize_count': 7},
    'dlt': {'url': 'http://data.17500.cn/dlt_asc.txt', 'count': 100, 'main': 5, 'main_max': 35, 'sub': 2, 'sub_max': 12, 'prize_start': 18, 'prize_count': 12},
    'kl8': {'url': 'http://data.17500.cn/kl8_asc.txt', 'count': 50, 'main': 20, 'main_max': 80, 'sub': 0, 'sub_max': 0, 'prize_start': None, 'prize_count': 0},
    '3d': {'url': 'http://data.17500.cn/3d_asc.txt', 'count': 50, 'main': 3, 'main_min': 0, 'main_max': 9, 'sub': 0, 'sub_max': 0, 'ordered': True, 'unique': False, 'prize_start': 11, 'prize_count': 3},
    '7lc': {'url': 'http://data.17500.cn/7lc_asc.txt', 'count': 50, 'main': 7, 'main_max': 30, 'sub': 1, 'sub_max': 30, 'sub_excl': True, 'prize_start': 12, 'prize_count': 7},
    '7xc': {'url': 'http://data.17500.cn/7xc_asc.txt', 'count': 50, 'main': 6, 'main_min': 0, 'main_max': 9, 'sub': 1, 'sub_min': 0, 'sub_max': 14, 'ordered': True, 'unique': False, 'prize_start': 11, 'prize_count': 6},
    'pl3': {'url': 'http://data.17500.cn/pl3_asc.txt', 'count': 50, 'main': 3, 'main_min': 0, 'main_max': 9, 'sub': 0, 'sub_max': 0, 'ordered': True, 'unique': False, 'prize_start': 6, 'prize_count': 3},
    'pl5': {'url': 'http://data.17500.cn/pl5_asc.txt', 'count': 50, 'main': 5, 'main_min': 0, 'main_max': 9, 'sub': 0, 'sub_max': 0, 'ordered': True, 'unique': False, 'prize_start': 8, 'prize_count': 1},
}
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def fetch_text(url):
    """拉一个 txt；429/5xx/网络异常时重试最多 5 次。
    优先用 Retry-After，否则退避 60s→120s→180s→300s；每次先 http、失败再试 https。"""
    last_exc = None
    for attempt in range(5):
        for try_url in (url, url.replace('http://', 'https://', 1)):
            try:
                req = urllib.request.Request(try_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.read().decode('utf-8', errors='replace')
            except urllib.error.HTTPError as e:
                last_exc = e
                if e.code in (429, 500, 502, 503, 504):
                    break  # 可重试的状态码，先退避再重拉
                raise
            except Exception as e:
                last_exc = e
        if attempt == 4:
            break
        wait = 60 * (attempt + 1) if attempt < 3 else 300
        if isinstance(last_exc, urllib.error.HTTPError):
            ra = last_exc.headers.get('Retry-After')
            if ra:
                try:
                    wait = max(int(ra), 1)
                except ValueError:
                    pass
        print(f'fetch {url} 失败（{last_exc}），{wait}s 后第 {attempt + 2} 次重试', flush=True)
        time.sleep(wait)
    raise last_exc or RuntimeError('fetch failed: ' + url)


def split_records(text):
    """把 txt 按期号切成逻辑行：以“期号 日期”开头的行是新一期，其余非空行是上一期的续行
    （大乐透单期占两行，快乐8 占约四行；续行直接拼到上一期后面再解析）。"""
    records = []
    for raw in text.splitlines():
        t = re.split(r'[\s,]+', raw.strip())
        if not t or not t[0]:
            continue
        if len(t) >= 2 and DATE_RE.match(t[1]):
            records.append(raw.strip())
        elif records:
            records[-1] = records[-1] + ' ' + raw.strip()
    return records


def parse_prizes(t, spec):
    """从整行 token 取奖金对子 [注数, 单注奖金] × N；token 不足或解析失败返回 None（该期只有号码、无奖金数据）。"""
    start = spec.get('prize_start')
    count = spec.get('prize_count') or 0
    if not start or not count:
        return None
    if len(t) < start + 2 * count:
        return None
    pairs = []
    for i in range(count):
        try:
            c = int(t[start + 2 * i])
            a = float(t[start + 2 * i + 1])
        except (ValueError, IndexError):
            return None
        if c < 0 or a < 0:
            return None
        pairs.append([c, int(a) if a == int(a) else a])
    return pairs


def parse_draws(text, spec):
    """txt 行格式：期号 日期 号码… 奖金对子…；先按期号拼行（大乐透两行），再取号码，行尾追加奖金对子。"""
    main_min = spec.get('main_min', 1)
    sub_min = spec.get('sub_min', 1)
    by_issue = {}
    for line in split_records(text):
        t = re.split(r'[\s,]+', line.strip())
        if len(t) < 3 or not DATE_RE.match(t[1]):
            continue
        need = spec['main'] + spec['sub']
        nums = []
        ok = True
        for i in range(need):
            try:
                nums.append(int(t[2 + i]))
            except (ValueError, IndexError):
                ok = False
                break
        if not ok:
            continue
        main = nums[:spec['main']]
        if not spec.get('ordered'):
            main = sorted(main)
        if any(n < main_min or n > spec['main_max'] for n in main):
            continue
        if spec.get('unique', True) and len(set(main)) != len(main):
            continue
        sub = nums[spec['main']:]
        if spec['sub'] == 1:
            if not (sub_min <= sub[0] <= spec['sub_max']):
                continue
            if spec.get('sub_excl') and sub[0] in main:
                continue
            row = [t[0], t[1], main, sub[0]]
        elif spec['sub'] == 2:
            s = sorted(sub)
            if any(n < sub_min or n > spec['sub_max'] for n in s) or len(set(s)) != len(s):
                continue
            row = [t[0], t[1], main, s]
        else:
            row = [t[0], t[1], main]
        prizes = parse_prizes(t, spec)
        if prizes is not None:
            row.append(prizes)
        by_issue[t[0]] = row
    rows = [by_issue[k] for k in sorted(by_issue)]
    return rows[-spec['count']:]


def validate_rows(rows, spec, key):
    main_min = spec.get('main_min', 1)
    sub_min = spec.get('sub_min', 1)
    assert len(rows) == spec['count'], f'{key}: 期数 {len(rows)} != {spec["count"]}'
    prev = ''
    for r in rows:
        assert r[0] > prev, f'{key}: 期号乱序 {r[0]}'
        prev = r[0]
        assert DATE_RE.match(r[1]), f'{key}: 日期格式错 {r}'
        main = r[2]
        assert len(main) == spec['main']
        if not spec.get('ordered'):
            assert main == sorted(main), f'{key}: 主号未排序 {r}'
        if spec.get('unique', True):
            assert len(set(main)) == len(main), f'{key}: 主号重复 {r}'
        assert all(main_min <= n <= spec['main_max'] for n in main), f'{key}: 号码越界 {r}'
        if spec['sub'] == 1:
            assert isinstance(r[3], int) and sub_min <= r[3] <= spec['sub_max'], f'{key}: 特别号错 {r}'
            if spec.get('sub_excl'):
                assert r[3] not in main, f'{key}: 特别号与主号重复 {r}'
        elif spec['sub'] == 2:
            assert len(r[3]) == 2 and r[3] == sorted(r[3]) and all(sub_min <= n <= spec['sub_max'] for n in r[3]), f'{key}: 后区错 {r}'
        # 奖金（若有）：行尾一串 [注数, 单注奖金] 对子，对数与彩种一致；缺失不报错（该期只算无奖金数据）
        if spec.get('prize_count'):
            pz_idx = 4 if spec['sub'] else 3
            if len(r) > pz_idx:
                pz = r[pz_idx]
                assert isinstance(pz, list) and len(pz) == spec['prize_count'], f'{key}: 奖金对数错 {r}'
                for pair in pz:
                    assert isinstance(pair, list) and len(pair) == 2, f'{key}: 奖金对错 {r}'
                    c, a = pair
                    assert isinstance(c, int) and c >= 0 and isinstance(a, (int, float)) and a >= 0, f'{key}: 奖金对错 {r}'


def render_data_block(rows_by_key):
    parts = []
    for key in KEYS:
        parts.append(f'"{key}":' + json.dumps(rows_by_key[key], separators=(',', ':')))
    return 'const DATA = {\n' + ',\n'.join(parts) + '\n}'


def cutoff_text(rows_by_key):
    bits = []
    for key in KEYS:
        last = rows_by_key[key][-1]
        bits.append(f'{LABELS[key]}截至 {last[0]} 期（{last[1]}）')
    return '· '.join(bits)


def refresh_html(html, rows_by_key):
    m = re.search(r'(// __LOTTERY_DATA_START__\n)(.*?)(\n// __LOTTERY_DATA_END__)', html, re.S)
    if not m:
        raise ValueError('index.html 里找不到 __LOTTERY_DATA_START__/END 标记')
    html = html[:m.start(2)] + render_data_block(rows_by_key) + html[m.end(2):]
    new_cutoff = '<span id="cutoff">' + cutoff_text(rows_by_key) + '</span>'
    html, n = re.subn(r'<span id="cutoff">.*?</span>', new_cutoff, html, count=1, flags=re.S)
    if n != 1:
        raise ValueError('index.html 里找不到 <span id="cutoff">')
    return html


def main():
    html_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'index.html'
    rows_by_key = {}
    for i, (key, spec) in enumerate(SPECS.items()):
        if i:
            time.sleep(90)  # 彩种间停 90 秒，别连着拉触发 17500 限流
        rows = parse_draws(fetch_text(spec['url']), spec)
        validate_rows(rows, spec, key)
        rows_by_key[key] = rows
    html = html_path.read_text(encoding='utf-8')
    new_html = refresh_html(html, rows_by_key)
    if new_html == html:
        print('数据无变化，不写文件')
        return 0
    html_path.write_text(new_html, encoding='utf-8')
    for key in KEYS:
        last = rows_by_key[key][-1]
        print(f'{key}: {len(rows_by_key[key])} 期，最新 {last[0]}（{last[1]}）')
    print(f'已更新 {html_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

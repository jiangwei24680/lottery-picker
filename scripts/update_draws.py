#!/usr/bin/env python3
"""每晚定时刷新 lottery-picker 页内开奖数据（兜底层；打开页面实时更新走 functions/api/draws.js）。

数据源：data.17500.cn 公开 txt（与 index.html 页脚一致）。
只替换 index.html 里 __LOTTERY_DATA_START__/__LOTTERY_DATA_END__ 之间的 DATA 块
和 <span id="cutoff"> 的截至文字，其余内容一字不动；数据校验不过直接退出不写文件。
仅用标准库。用法：python3 scripts/update_draws.py [index.html 路径，默认仓库根目录的 index.html]
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

SPECS = {
    'ssq': {'url': 'http://data.17500.cn/ssq_asc.txt', 'count': 100, 'main': 6, 'main_max': 33, 'sub': 1, 'sub_max': 16},
    'dlt': {'url': 'http://data.17500.cn/dlt_asc.txt', 'count': 100, 'main': 5, 'main_max': 35, 'sub': 2, 'sub_max': 12},
    'kl8': {'url': 'http://data.17500.cn/kl8_asc.txt', 'count': 50, 'main': 20, 'main_max': 80, 'sub': 0, 'sub_max': 0},
}
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def fetch_text(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception:
        https_url = url.replace('http://', 'https://', 1)
        if https_url != url:
            req = urllib.request.Request(https_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode('utf-8', errors='replace')
        raise


def parse_draws(text, spec):
    """txt 行格式：期号 日期 号码…（其余字段忽略）；只取每行首段的期号/日期/号码。"""
    by_issue = {}
    for line in text.splitlines():
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
        main = sorted(nums[:spec['main']])
        if any(n < 1 or n > spec['main_max'] for n in main) or len(set(main)) != len(main):
            continue
        sub = nums[spec['main']:]
        if spec['sub'] == 1:
            if not (1 <= sub[0] <= spec['sub_max']):
                continue
            by_issue[t[0]] = [t[0], t[1], main, sub[0]]
        elif spec['sub'] == 2:
            s = sorted(sub)
            if any(n < 1 or n > spec['sub_max'] for n in s) or len(set(s)) != len(s):
                continue
            by_issue[t[0]] = [t[0], t[1], main, s]
        else:
            by_issue[t[0]] = [t[0], t[1], main]
    rows = [by_issue[k] for k in sorted(by_issue)]
    return rows[-spec['count']:]


def validate_rows(rows, spec, key):
    assert len(rows) == spec['count'], f'{key}: 期数 {len(rows)} != {spec["count"]}'
    prev = ''
    for r in rows:
        assert r[0] > prev, f'{key}: 期号乱序 {r[0]}'
        prev = r[0]
        assert DATE_RE.match(r[1]), f'{key}: 日期格式错 {r}'
        main = r[2]
        assert len(main) == spec['main'] and main == sorted(main) and len(set(main)) == len(main)
        assert all(1 <= n <= spec['main_max'] for n in main), f'{key}: 号码越界 {r}'
        if spec['sub'] == 1:
            assert isinstance(r[3], int) and 1 <= r[3] <= spec['sub_max'], f'{key}: 蓝球错 {r}'
        elif spec['sub'] == 2:
            assert len(r[3]) == 2 and r[3] == sorted(r[3]) and all(1 <= n <= spec['sub_max'] for n in r[3]), f'{key}: 后区错 {r}'


def render_data_block(rows_by_key):
    parts = []
    for key in ('ssq', 'dlt', 'kl8'):
        parts.append(f'"{key}":' + json.dumps(rows_by_key[key], separators=(',', ':')))
    return 'const DATA = {\n' + ',\n'.join(parts) + '\n}'


def cutoff_text(rows_by_key):
    s = rows_by_key['ssq'][-1]
    d = rows_by_key['dlt'][-1]
    k = rows_by_key['kl8'][-1]
    return (f'双色球截至 {s[0]} 期（{s[1]}）· 大乐透截至 {d[0]} 期（{d[1]}）'
            f'· 快乐8 截至 {k[0]} 期（{k[1]}）')


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
    for key, spec in SPECS.items():
        rows = parse_draws(fetch_text(spec['url']), spec)
        validate_rows(rows, spec, key)
        rows_by_key[key] = rows
    html = html_path.read_text(encoding='utf-8')
    new_html = refresh_html(html, rows_by_key)
    if new_html == html:
        print('数据无变化，不写文件')
        return 0
    html_path.write_text(new_html, encoding='utf-8')
    for key in ('ssq', 'dlt', 'kl8'):
        last = rows_by_key[key][-1]
        print(f'{key}: {len(rows_by_key[key])} 期，最新 {last[0]}（{last[1]}）')
    print(f'已更新 {html_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

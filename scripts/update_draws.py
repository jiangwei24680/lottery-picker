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
import urllib.request
from pathlib import Path

KEYS = ('ssq', 'dlt', 'kl8', '3d', '7lc', '7xc', 'pl3', 'pl5')
LABELS = {'ssq': '双色球', 'dlt': '大乐透', 'kl8': '快乐8', '3d': '福彩3D',
          '7lc': '七乐彩', '7xc': '七星彩', 'pl3': '排列三', 'pl5': '排列五'}
SPECS = {
    'ssq': {'url': 'http://data.17500.cn/ssq_asc.txt', 'count': 100, 'main': 6, 'main_max': 33, 'sub': 1, 'sub_max': 16},
    'dlt': {'url': 'http://data.17500.cn/dlt_asc.txt', 'count': 100, 'main': 5, 'main_max': 35, 'sub': 2, 'sub_max': 12},
    'kl8': {'url': 'http://data.17500.cn/kl8_asc.txt', 'count': 50, 'main': 20, 'main_max': 80, 'sub': 0, 'sub_max': 0},
    '3d': {'url': 'http://data.17500.cn/3d_asc.txt', 'count': 50, 'main': 3, 'main_min': 0, 'main_max': 9, 'sub': 0, 'sub_max': 0, 'ordered': True, 'unique': False},
    '7lc': {'url': 'http://data.17500.cn/7lc_asc.txt', 'count': 50, 'main': 7, 'main_max': 30, 'sub': 1, 'sub_max': 30, 'sub_excl': True},
    '7xc': {'url': 'http://data.17500.cn/7xc_asc.txt', 'count': 50, 'main': 6, 'main_min': 0, 'main_max': 9, 'sub': 1, 'sub_min': 0, 'sub_max': 14, 'ordered': True, 'unique': False},
    'pl3': {'url': 'http://data.17500.cn/pl3_asc.txt', 'count': 50, 'main': 3, 'main_min': 0, 'main_max': 9, 'sub': 0, 'sub_max': 0, 'ordered': True, 'unique': False},
    'pl5': {'url': 'http://data.17500.cn/pl5_asc.txt', 'count': 50, 'main': 5, 'main_min': 0, 'main_max': 9, 'sub': 0, 'sub_max': 0, 'ordered': True, 'unique': False},
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
    main_min = spec.get('main_min', 1)
    sub_min = spec.get('sub_min', 1)
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
            by_issue[t[0]] = [t[0], t[1], main, sub[0]]
        elif spec['sub'] == 2:
            s = sorted(sub)
            if any(n < sub_min or n > spec['sub_max'] for n in s) or len(set(s)) != len(s):
                continue
            by_issue[t[0]] = [t[0], t[1], main, s]
        else:
            by_issue[t[0]] = [t[0], t[1], main]
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
    for key in KEYS:
        last = rows_by_key[key][-1]
        print(f'{key}: {len(rows_by_key[key])} 期，最新 {last[0]}（{last[1]}）')
    print(f'已更新 {html_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

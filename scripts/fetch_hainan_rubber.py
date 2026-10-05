# -*- coding: utf-8 -*-
"""
fetch_hainan_rubber.py —— 抓取海南省天然橡胶【收购价】历史

数据源：海南省发展和改革委员会 > 价格信息 > 价格监测报告 > 价格监测数据公布
        > 槟榔与橡胶价格行情   （海南省价格监测中心 发布）
        https://plan.hainan.gov.cn/sfgw/110201/list3.shtml

专栏结构（实测）：
    - 列表分页 list3.shtml / list3_2.shtml ... list3_50.shtml，每页 20 条，共 980 条；
    - 其中标题含"天然橡胶"的 537 条，覆盖 2020-06-29 ~ 2026-09-14；
    - 正文是一张 18 市县 + 全省合计 的表，字段：
          序号 | 市县 | 收购均价(元/公斤) | 环比涨跌幅(%) | 同比涨跌幅(%) | 备注
    - 单位是【元/公斤】且【按干含量折算为干胶】——与期货(元/吨)差 1000 倍，
      换算：收购价(元/吨) = 收购价(元/公斤) × 1000。

解析上的坑（都已处理）：
    1) 政府 CMS 会在标题和表头里插入空格，例如 "合 计 全 省"，
       必须用 \\s* 容忍，否则全省行永远匹配不到；
    2) 缺失值用 "——" 而不是数字；
    3) 部分条目是【月度汇总表】而非日度快照，粒度不同，需要单独打标；
    4) 早期（2020~2021）部分市县尚未开割，价格为 0.00。

输出：
    data/raw/{日期}_{标题哈希}.html   原始 HTML（缓存，重解析不必重爬）
    data/收购价_全省.csv              日期, 收购均价_元每公斤, 环比, 同比, 标题, 粒度
    data/收购价_分市县.csv            日期, 市县, 收购均价, 环比, 同比

⚠ 关于分市县的说明（2026-09）：
    本研究口径只要【全省均价】，分市县表只在"重算全省均价"与"审计构成效应"
    时用得到。因此 **分市县表默认不落盘** —— 需要时由 rubber_source.py
    直接从 data/raw/ 原文解析到内存，避免 data/ 里长期堆一份易被误用的派生表。
    加 --write-city 可强制写出（仅用于复现历史产物）。

用法：
    python fetch_hainan_rubber.py              # 增量：只爬没有缓存的
    python fetch_hainan_rubber.py --refetch    # 强制重爬
    python fetch_hainan_rubber.py --write-city # 额外写出分市县表（默认不写）
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")
INDEX_FILE = os.path.join(DATA, "_article_index.json")

LIST_URL = "https://plan.hainan.gov.cn/sfgw/110201/list3{}.shtml"
MAX_PAGES = 50

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 列表项： <li><em>2026-09-14</em><span>•</span><a href="...">标题</a></li>
RE_LIST_ITEM = re.compile(
    r'<li><em>(20\d\d-\d\d-\d\d)</em><span>•</span>'
    r'<a href="([^"]+)"[^>]*>([^<]+)</a>')

# 全省合计行：标题里会被插空格 -> "合 计 全 省 11.00 -3.51 9.89"
# 数值可能是 "——"（缺）、负数、或 0.00
_NUM = r"(-?\d+(?:\.\d+)?|——|—|--)"
RE_TOTAL = re.compile(r"合\s*计\s*全\s*省\s*" + _NUM + r"\s+" + _NUM + r"\s+" + _NUM)

# 分市县行： 序号 市县名 均价 环比 同比
RE_CITY = re.compile(
    r"(?:^|\s)(\d{1,2})\s+"
    r"(海口市|三亚市|儋州市|琼海市|文昌市|万宁市|东方市|五指山市|乐东县|"
    r"澄迈县|临高县|定安县|屯昌县|陵水县|昌江县|保亭县|琼中县|白沙县)\s+"
    + _NUM + r"\s+" + _NUM + r"\s+" + _NUM)


def fetch(url: str, tries: int = 3, timeout: int = 25) -> str:
    """带重试的 GET，统一转 UTF-8。"""
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.encoding = "utf-8"
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"抓取失败 {url} -> {last}")


def to_text(html: str) -> str:
    """HTML -> 压平的单行纯文本（保留表内字段间的分隔）。"""
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&ensp;|&nbsp;|&#160;", " ", t)
    return re.sub(r"\s+", " ", t)


def num(s: str):
    """把 '——' / '-3.51' / '11.00' 解析成 float 或 None。"""
    if s is None:
        return None
    s = s.strip()
    if s in ("——", "—", "--", "-", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def crawl_index(refetch: bool = False) -> dict:
    """爬取专栏全部分页，建立 文章URL -> (日期, 标题) 索引。"""
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(INDEX_FILE) and not refetch:
        with open(INDEX_FILE, encoding="utf-8") as f:
            idx = json.load(f)
        print(f"  索引已存在，共 {len(idx)} 条（--refetch 可重建）")
        return idx

    idx = {}
    for p in range(1, MAX_PAGES + 1):
        url = LIST_URL.format("" if p == 1 else f"_{p}")
        try:
            html = fetch(url)
        except RuntimeError as e:
            print(f"  第 {p} 页失败，停止分页：{e}")
            break
        hits = RE_LIST_ITEM.findall(html)
        if not hits:
            print(f"  第 {p} 页无列表项，分页结束")
            break
        for d, href, title in hits:
            if "天然橡胶" in title:
                idx[href] = [d, title.strip()]
        if p % 10 == 0:
            print(f"  已扫 {p} 页，累计橡胶条目 {len(idx)}")
        time.sleep(0.25)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    print(f"  索引完成：{len(idx)} 条")
    return idx


def parse_article(html: str):
    """从正文 HTML 解析出 (全省均价, 环比, 同比, [分市县行])。"""
    t = to_text(html)
    m = RE_TOTAL.search(t)
    total = (num(m.group(1)), num(m.group(2)), num(m.group(3))) if m else (None, None, None)
    cities = [(c[1], num(c[2]), num(c[3]), num(c[4])) for c in RE_CITY.findall(t)]
    return total, cities


def granularity(title: str) -> str:
    """区分日度快照与月度汇总——两者粒度不同，混在一起会污染统计。"""
    if "汇总" in title or re.search(r"年\s*\d{1,2}\s*月\s*份", title):
        return "月度汇总"
    return "日度"


def slug(href: str) -> str:
    name = href.rstrip("/").split("/")[-1].replace(".shtml", "")
    return hashlib.md5(name.encode()).hexdigest()[:10]


def main() -> None:
    ap = argparse.ArgumentParser(description="抓取海南省天然橡胶收购价历史")
    ap.add_argument("--refetch", action="store_true", help="强制重新爬取列表与正文")
    ap.add_argument("--write-city", action="store_true",
                    help="额外写出分市县表（默认不写，见文件头说明）")
    args = ap.parse_args()

    os.makedirs(RAW, exist_ok=True)
    print("[1/3] 建立文章索引 …")
    idx = crawl_index(refetch=args.refetch)

    print(f"[2/3] 抓取正文（共 {len(idx)} 篇）…")
    rows, city_rows, bad = [], [], []
    seen_title = set()          # 站点重复挂载的公示表，按标题判重（见下方注释）
    for i, (href, (d, title)) in enumerate(sorted(idx.items(), key=lambda kv: kv[1][0]), 1):
        # 站点偶尔把同一份公示表重复挂两条（列表页两条不同 URL、正文完全一致），
        # 例如 2020-08-13 / 2020-08-27。标题里含数据日，是判重的可靠键；
        # 列表日期不能用来判重 —— 重复项的列表日期也相同，而不同标题的条目
        # 有时共享同一列表日（同日发布多期），按日期去重会误删。
        if title in seen_title:
            continue
        cache = os.path.join(RAW, f"{d}_{slug(href)}.html")
        if os.path.exists(cache) and not args.refetch:
            html = open(cache, encoding="utf-8").read()
        else:
            try:
                html = fetch("https://plan.hainan.gov.cn" + href)
                with open(cache, "w", encoding="utf-8") as f:
                    f.write(html)
            except RuntimeError as e:
                bad.append((d, title, str(e)))
                continue
            time.sleep(0.25)

        (px, chg, yoy), cities = parse_article(html)
        if px is None:
            bad.append((d, title, "未解析到全省合计行"))
            continue
        seen_title.add(title)
        rows.append([d, px, chg, yoy, title, granularity(title)])
        for name, c_px, c_chg, c_yoy in cities:
            city_rows.append([d, name, c_px, c_chg, c_yoy])
        if i % 100 == 0:
            print(f"  {i}/{len(idx)} …")

    print("[3/3] 写出 CSV …")
    with open(os.path.join(DATA, "收购价_全省.csv"), "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["日期", "收购均价_元每公斤", "环比_百分比", "同比_百分比", "标题", "粒度"])
        w.writerows(rows)

    # 分市县表默认不落盘（见文件头说明）—— 需要时 rubber_source.py 直接从
    # data/raw/ 原文解析，没必要在 data/ 长期留一份易被误用的派生表。
    if args.write_city:
        with open(os.path.join(DATA, "收购价_分市县.csv"), "w", encoding="utf-8-sig",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(["日期", "市县", "收购均价_元每公斤", "环比_百分比", "同比_百分比"])
            w.writerows(city_rows)

    if bad:
        with open(os.path.join(DATA, "抓取失败清单.csv"), "w", encoding="utf-8-sig",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(["日期", "标题", "原因"])
            w.writerows(bad)

    daily = [r for r in rows if r[5] == "日度"]
    monthly = [r for r in rows if r[5] == "月度汇总"]
    print()
    print("=" * 62)
    print(f"成功解析    : {len(rows)} 篇（失败 {len(bad)}）")
    print(f"  日度快照  : {len(daily)} 篇  {daily[0][0] if daily else '-'} ~ {daily[-1][0] if daily else '-'}")
    print(f"  月度汇总  : {len(monthly)} 篇")
    print(f"分市县记录  : {len(city_rows)} 行"
          + ("" if args.write_city else "（未落盘，--write-city 可写出）"))
    if bad:
        print(f"失败清单    : data/抓取失败清单.csv")


if __name__ == "__main__":
    main()

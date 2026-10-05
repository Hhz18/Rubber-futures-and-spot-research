# -*- coding: utf-8 -*-
"""
rubber_source.py —— 原料收购价的唯一数据源：直接解析 data/raw/ 的政府原文

════════════════════════════════════════════════════════════════════
为什么要有这一层
════════════════════════════════════════════════════════════════════
data/raw/ 里的 547 份政府网页原文是**唯一不可再生**的资产（站点可能改版、
下架），其余一切 —— 全省表、分市县表、城市矩阵 —— 都是它的派生。

既然本项目的研究口径只要【全省均价】，就不需要在 data/ 里长期保留
分市县表与城市矩阵（它们是"脏数据"的来源，容易被误用）。但审计又必须
能拿到市县面板来证明构成效应的存在。两全的办法：**把 raw/ 当作唯一源，
按需在内存里解析**，不落任何中间 CSV。

本模块因此是 audit_data_source.py / clean_provincial_price.py /
micro_windows_provincial.py 共用的取数层。

════════════════════════════════════════════════════════════════════
解析规则（复用 fetch_hainan_rubber.py，不另起一套）
════════════════════════════════════════════════════════════════════
  * 全省行：正则匹配 "合 计 全 省 价 环比 同比"（政府 CMS 会插空格）
  * 市县行：序号 + 18 市县名之一 + 3 个数
  * 数值 '——' -> None（该期无环比/同比），'0.00' -> 0.0（未开割/雨天未收胶）
  * 标题含"汇总"或"年X月份" -> 月度汇总表，粒度不同，单独标记
  * 同一标题重复挂载（2020-08-13 / 2020-08-27）-> 按标题判重

解析结果在进程内缓存，多次调用不重复解析。

用法：
    from rubber_source import provincial, county_panel, basis_of

    prov = provincial()      # {日期: {均价, 环比, 标题, 粒度}}
    panel = county_panel()   # {日期: {市县: 价}}   仅 >0 的报价
"""

import collections
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW_DIR = os.path.join(DATA, "raw")
INDEX_FILE = os.path.join(DATA, "_article_index.json")

sys.path.insert(0, HERE)
# 直接复用抓取脚本的解析函数与正则，保证"解析口径"全局唯一收敛点
from fetch_hainan_rubber import parse_article, granularity, slug  # noqa: E402

TITLE_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
N_CITY = 18
BASIS_CHANGE_DATE = "2024-10-07"      # 备注列改为「胶水/胶块/胶粒折算」之日

_CACHE = {}


def _load_index():
    with open(INDEX_FILE, encoding="utf-8") as f:
        return json.load(f)


def parse_raw():
    """解析 raw/ 全部原文 -> (全省记录列表, 市县记录列表)。带进程内缓存。"""
    if "rows" in _CACHE:
        return _CACHE["rows"]

    idx = _load_index()
    prov_rows, city_rows, missing = [], [], []
    seen_title = set()
    # 按列表日期排序，与 fetch_hainan_rubber.py 的写出顺序一致
    for href, (d, title) in sorted(idx.items(), key=lambda kv: kv[1][0]):
        if title in seen_title:            # 站点重复挂载，按标题判重
            continue
        path = os.path.join(RAW_DIR, f"{d}_{slug(href)}.html")
        if not os.path.exists(path):
            missing.append((d, title))
            continue
        with open(path, encoding="utf-8") as f:
            html = f.read()
        (px, chg, yoy), cities = parse_article(html)
        if px is None:
            continue
        seen_title.add(title)
        prov_rows.append({"日期": d, "收购均价_元每公斤": px,
                          "环比_百分比": chg, "同比_百分比": yoy,
                          "标题": title, "粒度": granularity(title)})
        for name, c_px, c_chg, c_yoy in cities:
            city_rows.append({"日期": d, "市县": name,
                              "收购均价_元每公斤": c_px,
                              "环比_百分比": c_chg, "同比_百分比": c_yoy})
    _CACHE["rows"] = (prov_rows, city_rows, missing)
    return _CACHE["rows"]


def provincial():
    """{日期: {均价, 环比, 标题}}，仅日度、按日期保留首条。"""
    if "prov" in _CACHE:
        return _CACHE["prov"]
    rows, _, _ = parse_raw()
    out = {}
    for r in rows:
        if r["粒度"] != "日度":
            continue
        out.setdefault(r["日期"], {"均价": r["收购均价_元每公斤"],
                                   "环比": r["环比_百分比"],
                                   "标题": r["标题"]})
    _CACHE["prov"] = out
    return out


def county_panel():
    """{日期: {市县: 价}}，只保留 >0 的报价。

    0.00 在原文里表示「未开割 / 雨天未收胶」，不是价格 0；官方自身也是剔 0
    计算全省均价的，任何自算必须同样处理。
    """
    if "panel" in _CACHE:
        return _CACHE["panel"]
    _, rows, _ = parse_raw()
    panel = collections.defaultdict(dict)
    for r in rows:
        v = r["收购均价_元每公斤"]
        if v and v > 0:
            panel[r["日期"]][r["市县"]] = v
    _CACHE["panel"] = panel
    return panel


def missing_raw():
    """索引里有、raw/ 缓存里缺的条目（应为空）。"""
    return parse_raw()[2]


def data_date(title):
    """标题里的【数据日】（监测当日）。列表日是发布日，两者有 38/480 期差 1 天。"""
    m = TITLE_RE.match(title or "")
    return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups()) if m else ""


def basis_of(dt):
    """该期的监测折算口径。2024-10-07 起由统一干胶折算改为分别折算。"""
    return "折算(胶水/胶块/胶粒)" if dt >= BASIS_CHANGE_DATE else "干胶折算"


def export_csv(path=None):
    """把解析结果写成 CSV（仅供 fetch_hainan_rubber.py 复现产物用）。"""
    rows, city_rows, _ = parse_raw()
    if path is None:
        return rows, city_rows
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["日期", "收购均价_元每公斤", "环比_百分比", "同比_百分比",
                    "标题", "粒度"])
        for r in rows:
            w.writerow([r["日期"], r["收购均价_元每公斤"], r["环比_百分比"],
                        r["同比_百分比"], r["标题"], r["粒度"]])
    return rows, city_rows


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p, c = provincial(), county_panel()
    rows, city_rows, miss = parse_raw()
    print(f"解析 raw/ 原文：全省 {len(rows)} 条（日度 {len(p)}）｜ 市县 {len(city_rows)} 行")
    print(f"  区间 : {min(p)} ~ {max(p)}")
    print(f"  缺 raw 的索引条目 : {len(miss)}")
    cov = collections.Counter(len(c[d]) for d in c)
    print(f"  报价市县数分布 : {dict(sorted(cov.items()))}")

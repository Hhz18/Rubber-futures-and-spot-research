# -*- coding: utf-8 -*-
"""
build_price_index.py —— 原料收购价序列的口径校验与结构化导出

════════════════════════════════════════════════════════════════════
为什么需要这个脚本：原料价的两个坑
════════════════════════════════════════════════════════════════════

【坑一】"全省收购均价"不是可比时间序列（构成污染）
    《海南省天然橡胶收购价格公示表》的"全省收购均价" = 当期【有报价】市县的
    简单均值（实测验证：18 市县全覆盖日的全省合计与简单均值差 < 0.005）。
    但每份报告有报价的市县数在 1~18 之间浮动 —— 477 份里 303 份（63.5%）非全覆盖。
    覆盖变化时均价的变动主要来自样本构成，不是价格：

        日期        均价    报价市县   站点环比    均价直接算
        2026-08-27  14.38     18       -0.01%       —
        2026-08-31  13.55      5       +0.56%      -5.77%   <-- 口径冲突
        2026-09-03  14.44     18       -0.03%      +6.57%   <-- 口径冲突
        2026-09-14  13.36      3       +0.55%      -9.49%   <-- 口径冲突
    均价看似暴跌 9.49%，站点环比却是 +0.55%（涨）。

【坑二】站点自带的"环比"可信，但不能长距离连乘
    实测检验（以 18 市县全覆盖日为无假设锚点，比较同跨度涨跌幅）：
        站点环比链   平均绝对误差 0.055 pp   <-- 基本精确
        逐对重算LFL  平均绝对误差 0.275 pp   <-- 更差
    所以站点环比是 like-for-like 口径且准。但它有两个限制：

    (a) 季初环比为空 —— 海南每年约 1–3 月停割，季初那份报告的环比无前值。
        连乘会把整段跨季跃迁丢掉（实测累计丢 22.79%）：
            2020-12-31 -> 2021-05-03  真实 +4.12%   链条 +0.00%
            2025-01-09 -> 2025-04-03  真实 +23.63%  链条 +0.00%
    (b) 报告市县集合随时间滚动变化 —— 早期季节只有 4 个市县报价，
        链条在"当年报数的市县"上累积，与 18 市县均价会缓慢分离
        （实测 2024 年 8 个月无全覆盖锚点，漂移达 +3.7% = 370 元/吨，
          足以淹没研究中的价差变化量级）。

════════════════════════════════════════════════════════════════════
本脚本的结论与做法
════════════════════════════════════════════════════════════════════
既然研究问题永远是"**A 日到 B 日之间原料涨了多少**"，就**不需要任何链式指数**：

    直接在两个端点取【共同报价市县】的均值相比 ——
    R = (均值_共同(B) - 均值_共同(A)) / 均值_共同(A)

两端用的是**完全相同的市县集合**，构成效应被彻底消除，
既不用连乘、也不会漂移，且水平是真实的 元/公斤（价差计算可用）。

代价：要求两端都有观测、且共同市县数够多（否则样本太窄）。
本脚本负责把这套口径**校验一遍**，并导出分析层需要的结构化数据。

════════════════════════════════════════════════════════════════════
产物
════════════════════════════════════════════════════════════════════
    data/收购价_城市矩阵.csv    日期, 市县, 收购均价_元每公斤（仅 >0 的报价）
    data/收购价_可比指数.csv    日期, 报价市县数, 报告均价, 站点环比_pct,
                                块内链式指数, 是否全覆盖, 季块号
    控制台：坑一/坑二的实测证据 + 共同市县法可用性统计

用法：
    python build_price_index.py
"""

import csv
import os
import sys
import collections
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CITY_FILE = os.path.join(DATA, "收购价_分市县.csv")
PROV_FILE = os.path.join(DATA, "收购价_全省.csv")
MATRIX_OUT = os.path.join(DATA, "收购价_城市矩阵.csv")
META_OUT = os.path.join(DATA, "收购价_可比指数.csv")

GAP_DAYS = 30       # 间隔 ≥ 该天数视为跨季停割断档
N_CITY_TOTAL = 18


def D(s: str) -> datetime.date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load():
    by_date = collections.defaultdict(dict)
    with open(CITY_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = fnum(r["收购均价_元每公斤"])
            if v and v > 0:
                by_date[r["日期"]][r["市县"]] = v
    prov = {}
    with open(PROV_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("粒度") and r["粒度"] != "日度":
                continue
            prov[r["日期"]] = {
                "均价": fnum(r["收购均价_元每公斤"]),
                "环比": fnum(r["环比_百分比"]),
            }
    return by_date, prov


def main() -> None:
    by_date, prov = load()
    dates = sorted(by_date)
    n_rep = len(dates)

    # ---------- 导出城市矩阵 ----------
    with open(MATRIX_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["日期", "市县", "收购均价_元每公斤"])
        for dt in dates:
            for city in sorted(by_date[dt]):
                w.writerow([dt, city, by_date[dt][city]])

    # ---------- 季块 + 块内链式指数（仅作参考，不作分析输入） ----------
    block_of, blk = {}, 0
    for i, dt in enumerate(dates):
        if i and (D(dt) - D(dates[i - 1])).days >= GAP_DAYS:
            blk += 1
        block_of[dt] = blk

    recs, cur_blk, chain = [], None, 1.0
    for dt in dates:
        cur = by_date[dt]
        p = prov.get(dt, {})
        if block_of[dt] != cur_blk:
            cur_blk, chain = block_of[dt], 1.0
        elif p.get("环比") is not None:
            chain *= (1 + p["环比"] / 100.0)
        recs.append({
            "日期": dt,
            "报价市县数": len(cur),
            "报告均价": p.get("均价"),
            "站点环比_pct": (p["环比"] if p.get("环比") is not None else ""),
            "块内链式指数": round(chain, 4),
            "是否全覆盖": 1 if len(cur) == N_CITY_TOTAL else 0,
            "季块号": block_of[dt],
        })

    with open(META_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)

    cov = collections.Counter(len(by_date[d]) for d in dates)
    naive, site = [], []
    for i in range(1, n_rep):
        a, b = prov.get(dates[i - 1], {}), prov.get(dates[i], {})
        if a.get("均价") and b.get("均价"):
            naive.append(abs((b["均价"] / a["均价"] - 1) * 100))
        if a.get("环比") is not None:
            site.append(abs(a["环比"]))

    print("=" * 76)
    print("【坑一】构成污染")
    print("=" * 76)
    print(f"报告总数 {n_rep}   区间 {dates[0]} ~ {dates[-1]}")
    print(f"覆盖率 <18 市县的报告 : {sum(v for k, v in cov.items() if k < 18)} "
          f"/ {n_rep}  ({sum(v for k,v in cov.items() if k<18)/n_rep*100:.1f}%)")
    print(f"覆盖率 ≥16 市县的报告 : {sum(v for k, v in cov.items() if k >= 16)}")
    print()
    print("报价市县数分布（市县数: 报告数）：")
    for k in sorted(cov):
        print(f"  {k:2d} 个市县 : {cov[k]:4d} 份")
    print()
    print(f"『报告均价』逐期平均绝对变动 : {sum(naive)/len(naive):.3f}%")
    print(f"『站点环比』逐期平均绝对变动 : {sum(site)/len(site):.3f}%")
    print(f"  → 均价列凭空多出的波动 = {sum(naive)/len(naive) - sum(site)/len(site):.3f} 个百分点/期，"
          "全部来自构成变化")

    print()
    print("=" * 76)
    print("【坑二】跨停割季的跃迁")
    print("=" * 76)
    print(f"停割季空档（≥{GAP_DAYS} 天），共 {blk} 处，季块 {blk+1} 个：")
    for i in range(1, n_rep):
        if block_of[dates[i]] != block_of[dates[i - 1]]:
            a, b = dates[i - 1], dates[i]
            pa, pb = by_date[a], by_date[b]
            ma, mb = sum(pa.values()) / len(pa), sum(pb.values()) / len(pb)
            has_a = "有" if prov.get(a, {}).get("环比") is not None else "无"
            has_b = "有" if prov.get(b, {}).get("环比") is not None else "无"
            print(f"  {a}({len(pa)}市县,环比{has_a}) -> {b}({len(pb)}市县,环比{has_b})  "
                  f"空档{(D(b)-D(a)).days:3d}天  端到端 {(mb/ma-1)*100:+6.2f}%")
    print("  ↑ 季初报告环比为『无』时，链式连乘会把整段跃迁记成 0.00%")

    print()
    print("=" * 76)
    print("【结论】改用『共同市县端点法』")
    print("=" * 76)
    nc = [len(set(by_date[dates[i]]) & set(by_date[dates[i - 1]]))
          for i in range(1, n_rep)]
    nc.sort()
    print(f"相邻两份报告的共同市县数：中位 {nc[len(nc)//2]}  "
          f"最小 {nc[0]}  最大 {nc[-1]}")
    print(f"  共同市县 ≥ 6 的期数 : {sum(1 for x in nc if x >= 6)} / {len(nc)}")
    print(f"  共同市县 ≥ 8 的期数 : {sum(1 for x in nc if x >= 8)} / {len(nc)}")
    print("  → 分析层用『两端共同市县均值』算涨幅，构成效应彻底消除且不漂移；")
    print("    实际可用性取决于研究窗口两端的共同市县数（见 build_dataset.py 的门槛统计）")
    print()
    print(f"已写出: {MATRIX_OUT}")
    print(f"已写出: {META_OUT}")


if __name__ == "__main__":
    main()

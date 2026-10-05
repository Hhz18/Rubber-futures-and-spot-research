# -*- coding: utf-8 -*-
"""
clean_provincial_price.py —— 海南天然橡胶收购价【全省均价】清洗与可比化重建

════════════════════════════════════════════════════════════════════
为什么不能直接用官方发布的"全省收购均价"
════════════════════════════════════════════════════════════════════
官方公示表的"合计 全省" = 当期【有报价】市县的简单均值（已逐格验证，
18 市县全覆盖日的官方值与 18 市县算术均值差 < 0.005 元/公斤）。

但每份公示表有报价的市县数在 1 ~ 18 之间浮动（477 份里 303 份非全覆盖，
63.5%）。报价集合一变，"全省均价"的变动里就混进了【样本构成】的变动，
而不是价格变动。实测逐期环比中的构成效应：

    中位 0.31pp ｜ 均值 1.06pp ｜ 最大 18.53pp
    26% 的期数构成效应 > 1pp，10% > 3pp

极端例子（官方值 vs 两端共同市县的真实值）：

    2022-05-19  官方 +18.70%   真实 +0.17%   构成 +18.53pp  (5 -> 11 个市县)
    2022-05-16  官方 -13.57%   真实 -0.21%   构成 -13.36pp  (10 -> 5 个市县)
    2026-09-14  官方  -9.49%   真实 +0.55%   构成 -10.04pp  (18 -> 3 个市县)
    2025-01-09 -> 2025-04-03  官方 +23.63%  可比 +0.31%  差 23.32pp (1 -> 4 个市县)

════════════════════════════════════════════════════════════════════
本脚本的口径：【LOCF 固定篮子全省均价】
════════════════════════════════════════════════════════════════════
不做分市县分析，只要一列"全省均价"，那就把篮子【固定成全部 18 个市县】，
某个市县当日未报价（雨天未收胶 / 未开割 / 停割季）就沿用它最近一次的报价
（last-observation-carried-forward）。然后对 18 个值取算术平均：

    全省均价_可比(t) = mean( 18 个市县各自在 t 日或之前最近一次的报价 )

这样：
  * 是真正的全省口径（18 个市县一个不少），不是"当期恰好报了几个"；
  * 篮子固定，构成效应从定义上消失，逐期变动只反映价格；
  * 在 18 市县全覆盖日，与官方发布均价偏差 0.0026 元/公斤（几乎重合），
    所以【水平】仍是真实的 元/公斤，可用于价差计算。

代价与边界条件：停割季/连续雨天时，篮子里的值会是若干天前的旧价，序列被
平滑。极端个例 —— 海口市 2024-10 之后长时间停报，2026-04 重开割时篮子里
仍挂着它 564 天前的旧价（10.50），会把篮子均价压低。为此：
  * 输出 `距上次报价天数_最大` 与 `篮子市县数`，供分析层设阈值剔除；
  * 可选 `--carry-cap N`：滞后超过 N 天的市县从篮子中剔除（篮子变小）。
    实测 cap=90 会裁掉 156 期的至少一个市县；18 市县全覆盖日的偏差不变
    （仍是 0.0026），说明裁剪只在低覆盖期起作用，不损害水平可比性。
    默认不裁剪（保持篮子恒为 18），因为"篮子变小"本身又会引入构成变化。

════════════════════════════════════════════════════════════════════
同时保留、并标注的三处原始数据特征
════════════════════════════════════════════════════════════════════
1) 官方发布均价原样保留（列 `官方发布均价`），便于对照与复核；
2) 2024-10-07 起备注列由空/未开割 改为「胶水折算 / 胶块折算 / 胶粒折算」，
   实测该日 LOCF 篮子跳 +0.201 元/公斤（处历史 77 分位），是监测口径变更，
   用 `折算口径` 列标出，跨该日的长窗口需注意；
3) 列表日(发布日) 与 标题日(数据日) 有 40/479 期相差 1 天，两列都输出。

════════════════════════════════════════════════════════════════════
产物
════════════════════════════════════════════════════════════════════
    data/收购价_全省_清洗版.csv
        日期, 数据日, 全省均价_可比, 官方发布均价, 报价市县数,
        折算口径, 距上次报价天数_最大, 停割季, 环比_可比_pct, 环比_官方_pct
    控制台：清洗前后对照 + 构成效应量化 + 跨季断档对照

用法：
    python clean_provincial_price.py
"""

import collections
import csv
import os
import re
import statistics
import sys
import argparse
from datetime import date as _date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT_FILE = os.path.join(DATA, "收购价_全省_清洗版.csv")

sys.path.insert(0, HERE)
from rubber_source import (  # noqa: E402
    provincial, county_panel, data_date, basis_of,
    N_CITY, BASIS_CHANGE_DATE,
)

SEASON_GAP_DAYS = 30      # 相邻两期间隔 ≥ 该天数，视为跨停割季


def d(s):
    return _date.fromisoformat(s[:10])


def build_locf(panel, dates, carry_cap=None):
    """LOCF 固定篮子：{日期: (篮子均价, 篮子大小, 最大滞后天数)}。

    篮子大小从第一个有报价的市县开始增长，之后固定为 18 —— 首期只有 15 个
    市县有报价，没有历史可沿用，那期篮子就是 15。

    carry_cap 不为 None 时，滞后超过该天数的市县从篮子中剔除（篮子变小），
    用于排除"停报很久的市县把旧价一直挂在篮子里"的极端情形。
    """
    last = {}          # 市县 -> (价, 报价日)
    out = {}
    for dt in dates:
        for c, v in panel.get(dt, {}).items():
            last[c] = (v, dt)
        if not last:
            out[dt] = (None, 0, None)
            continue
        lags = {c: (d(dt) - d(od)).days for c, (_, od) in last.items()}
        keep = [c for c, g in lags.items() if carry_cap is None or g <= carry_cap]
        if not keep:
            out[dt] = (None, 0, max(lags.values()))
            continue
        vals = [last[c][0] for c in keep]
        out[dt] = (sum(vals) / len(vals), len(keep), max(lags[c] for c in keep))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="海南天然橡胶收购价全省均价清洗（LOCF 固定篮子可比口径）")
    ap.add_argument("--carry-cap", type=int, default=None,
                    help="篮子内报价滞后超过该天数的市县剔除，默认不剔除（篮子恒为 18）")
    args = ap.parse_args()

    panel = county_panel()
    prov = provincial()
    dates = sorted(prov)

    # ---------- 停割季分块：相邻两期间隔 ≥30 天即为一次跨季断档 ----------
    block, blk = {}, 0
    for i, dt in enumerate(dates):
        if i and (d(dt) - d(dates[i - 1])).days >= SEASON_GAP_DAYS:
            blk += 1
        block[dt] = blk
    # 季初 = 紧接在一次断档之后的那一期（海南每年约 1~3 月停割）
    season_start = {dt: (1 if i and block[dt] != block[dates[i - 1]] else 0)
                    for i, dt in enumerate(dates)}

    locf = build_locf(panel, dates, args.carry_cap)

    # ---------- 组装输出 ----------
    recs, prev_cmp = [], None
    for dt in dates:
        cmp_px, n_basket, max_lag = locf[dt]
        off = prov[dt]["均价"]
        recs.append({
            "日期": dt,
            "数据日": data_date(prov[dt]["标题"]),
            "全省均价_可比": round(cmp_px, 4) if cmp_px is not None else "",
            "官方发布均价": off if off is not None else "",
            "报价市县数": len(panel.get(dt, {})),
            "篮子市县数": n_basket,
            "折算口径": basis_of(dt),
            "距上次报价天数_最大": max_lag,
            "停割季": season_start[dt],
            "环比_可比_pct": (round((cmp_px / prev_cmp - 1) * 100, 4)
                              if prev_cmp else ""),
            "环比_官方_pct": prov[dt]["环比"] if prov[dt]["环比"] is not None else "",
        })
        prev_cmp = cmp_px

    with open(OUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)

    # ═══════════════ 控制台报告 ═══════════════
    print("=" * 84)
    print("一、清洗结果")
    print("=" * 84)
    print(f"  输入 : {len(prov)} 期（已按日期去重，原表含 2 条站点重复发布）")
    print(f"  输出 : {OUT_FILE}")
    print(f"  区间 : {dates[0]} ~ {dates[-1]}")
    cov = collections.Counter(len(panel.get(dt, {})) for dt in dates)
    print(f"  当期报价市县数 <18 的期数 : {sum(v for k, v in cov.items() if k < N_CITY)}"
          f" / {len(dates)}  ({sum(v for k,v in cov.items() if k<N_CITY)/len(dates)*100:.1f}%)")
    print(f"  篮子固定为 18 市县的期数 : {sum(1 for dt in dates if locf[dt][1] == N_CITY)}")

    print()
    print("=" * 84)
    print("二、LOCF 可比均价 与 官方发布均价 的一致性（只在 18 市县全覆盖日比，此时官方值就是算术均值）")
    print("=" * 84)
    full = [dt for dt in dates if len(panel.get(dt, {})) == N_CITY]
    err = [abs(locf[dt][0] - prov[dt]["均价"]) for dt in full]
    print(f"  全覆盖日 {len(full)} 期：平均偏差 {statistics.mean(err):.4f}  "
          f"最大 {max(err):.4f} 元/公斤")
    print("  → 篮子口径没有改变【水平】，只改变了【可比性】。")

    print()
    print("=" * 84)
    print("三、构成效应量化：官方环比 vs LOCF 环比")
    print("=" * 84)
    print("  （官方环比是 like-for-like 口径，18 市县全覆盖时误差仅 0.032pp，可作准绳）")
    deltas = []
    for a, b in zip(dates, dates[1:]):
        if locf[a][0] and locf[b][0]:
            deltas.append(abs((locf[b][0] / locf[a][0] - 1) * 100))
    print(f"  LOCF 可比均价 逐期绝对变动 : 中位 {statistics.median(deltas):.3f}%  "
          f"均值 {statistics.mean(deltas):.3f}%")
    raw = [abs((prov[b]["均价"] / prov[a]["均价"] - 1) * 100)
           for a, b in zip(dates, dates[1:])]
    print(f"  官方发布均价 逐期绝对变动 : 中位 {statistics.median(raw):.3f}%  "
          f"均值 {statistics.mean(raw):.3f}%")
    print(f"  → 官方均价列凭空多出 {statistics.mean(raw) - statistics.mean(deltas):.3f} "
          "个百分点/期的虚假波动，全部来自报价集合变化。")

    print()
    print("=" * 84)
    print("四、跨停割季断档对照（这些区间的官方涨幅不可用）")
    print("=" * 84)
    print(f"  {'区间':<26}{'空档':>6}{'官方涨幅':>11}{'可比涨幅':>11}{'差':>10}")
    for a, b in zip(dates, dates[1:]):
        gap = (d(b) - d(a)).days
        if gap < SEASON_GAP_DAYS:
            continue
        o = (prov[b]["均价"] / prov[a]["均价"] - 1) * 100
        c = (locf[b][0] / locf[a][0] - 1) * 100
        print(f"  {a} -> {b:<12}{gap:>6}{o:>+10.2f}%{c:>+10.2f}%{c - o:>+9.2f}pp")

    print()
    print("=" * 84)
    print("五、需随数据一起引用的口径标记")
    print("=" * 84)
    i = next((k for k, dt in enumerate(dates) if dt >= BASIS_CHANGE_DATE), None)
    if i:
        a, b = dates[i - 1], dates[i]
        print(f"  1) 监测口径变更日 : {BASIS_CHANGE_DATE}"
              f"（备注列由空/未开割 改为 胶水/胶块/胶粒折算）")
        print(f"     变更点 LOCF 篮子: {locf[a][0]:.3f} -> {locf[b][0]:.3f}"
              f"  ({locf[b][0] - locf[a][0]:+.3f} 元/公斤)")
        print(f"     该跳幅处历史 {100*sum(1 for x in deltas if x < abs(locf[b][0]-locf[a][0]))/len(deltas):.0f} 分位"
              "，非异常但需标记")
    print(f"  2) 停割季空档 {blk} 处，最长 "
          f"{max((d(b)-d(a)).days for a,b in zip(dates,dates[1:]))} 天；"
          "期间 LOCF 沿用停割前旧价，序列被平滑")
    print(f"  3) 列表日(发布日) 与 标题日(数据日) 相差 1 天的期数 : "
          f"{sum(1 for r in recs if r['数据日'] and r['数据日'] != r['日期'])} / {len(recs)}"
          "（本表 `日期` 沿用列表日，与既有下游脚本一致）")
    print(f"  4) 2026-09-17 起新增 3 期已补入索引与 raw（数据窗口延至 "
          f"{dates[-1]}）")
    print()
    print(f"已写出: {OUT_FILE}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
span_quadrant.py —— 按「四象限 × 自然日跨度」重做案例统计（无界面版）

════════════════════════════════════════════════════════════════════
这个脚本要回答的问题（你这次指定的口径）
════════════════════════════════════════════════════════════════════
  「ZigZag 区间本身的长度落在 30–37 自然日 / ≈60 自然日 内时，
    四类区间各自的 期货涨幅 F、原料涨幅 R、A = R/F 是多少？」

与 analyze_asymmetry.py 的区别（重要）：
  那边是【滑动窗口】——固定长度、任意起点，一个窗口一个样本，样本数千；
  这边是【ZigZag 区间】——按趋势结构切出来的真实区间，一个区间一个样本。
  这边的"区间类型"是区间【本身】的性质（主升/回调/反弹/主跌），
  那边的"象限"只是窗口起点【所处】的趋势。**两者不是同一个口径，不能混用。**

样本量的残酷现实（已在计划阶段实测）：
  默认阈值下 30–37 自然日只有 6 个案例，≈60 自然日只有 5 个，
  且四象限铺不满。**这个量级做不了显著性检验，本脚本不假装能做** ——
  它把所有案例逐个列出来，统计量只作参考。
  想加大样本要靠调 ZigZag 阈值或改跨度，所以界面版把两者都做成可调。

产出：
    data/跨度象限统计.md
    控制台：案例计数网格 + 时长分布

用法：
    python span_quadrant.py
    python span_quadrant.py --minor 4 --major 15 --span 30-37 --span 55-65
"""

import argparse
import collections
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
# ⚠ 2026-09 修正：算法已移到 趋势判定方案/src/（同 build_dataset.py）
TREND_DIR = os.path.join(os.path.dirname(HERE), "趋势判定方案")
TREND_SRC = os.path.join(TREND_DIR, "src")
if TREND_SRC not in sys.path:
    sys.path.insert(0, TREND_SRC)

from build_dataset import (load_futures, load_cities, pair_common, d,   # noqa: E402
                           quad_of, bucket_of, pick_obs,
                           FUT_FILE, FUT_FILE_RAW, LVL_FILE)
from trend_analysis import label_legs, preset_params, judge_args           # noqa: E402
from analyze_asymmetry import _med, block_bootstrap_ci                 # noqa: E402

OUT_MD = os.path.join(DATA, "跨度象限统计.md")

FUT_CHOICES = {"adj": FUT_FILE, "lvl": LVL_FILE, "raw": FUT_FILE_RAW}
FUT_LABEL = {"adj": "后复权连续", "lvl": "未复权连续", "raw": "RU0 原始"}

# 输出顺序：你只关心四类，平稳区间放最后且默认单列
QUAD_ORDER = ["1-上涨趋势中的上涨区间", "2-上涨趋势中的下跌区间",
              "4-下跌趋势中的上涨区间", "5-下跌趋势中的下跌区间",
              "3-平稳区间"]


def parse_span(s: str):
    """'30-37' → (30, 37)；'60' → (60, 60)。"""
    if "-" in s:
        a, b = s.split("-", 1)
        return int(a), int(b)
    v = int(s)
    return v, v


def build_cases(bars, dates, legs, cities, max_stale, min_common, min_f):
    """把 ZigZag 区间补上原料端读数。**与 rubber_core.js 的 buildCases 同构。**"""
    out, drop = [], {"noObs": 0, "common": 0}
    for i, lg in enumerate(legs):
        d0, d1 = dates[lg.start_idx], dates[lg.end_idx]
        pr = pair_common(cities, d0, d1, max_stale, min_common)
        if pr is None:
            if pick_obs(cities, d0, max_stale)[1] is None or \
               pick_obs(cities, d1, max_stale)[1] is None:
                drop["noObs"] += 1
            else:
                drop["common"] += 1
            continue
        F0, F1 = bars[lg.start_idx].close, bars[lg.end_idx].close
        F = (F1 - F0) / F0 * 100.0
        R = (pr["r1"] - pr["r0"]) / pr["r0"] * 100.0
        out.append({
            "序号": i + 1,
            "象限": quad_of(lg.trend_dir, lg.direction),
            "幅度档": bucket_of(F),
            "起点日": d0, "终点日": d1,
            "交易日数": lg.duration_bars,
            "自然日数": (d(d1) - d(d0)).days,
            "F": F, "R": R,
            "A": (R / F) if abs(F) >= min_f else None,
            "G": R - F,
            "共同市县数": pr["n_common"],
            "观测起": pr["obs0"], "观测终": pr["obs1"],
            "区间涨跌幅": lg.change_pct,
        })
    return out, drop


def summarise(lst):
    A = [c["A"] for c in lst if c["A"] is not None]
    F = [c["F"] for c in lst]
    R = [c["R"] for c in lst]
    return {
        "n": len(lst), "nA": len(A),
        "Fmed": _med(F) if F else None,
        "Rmed": _med(R) if R else None,
        "Amed": _med(A) if A else None,
        "shareLt1": (sum(1 for x in A if x < 1) / len(A)) if A else None,
    }


# ------------------------------------------------------------------
# 统计层：固定长度窗口按「起点所处象限」分组
# ------------------------------------------------------------------
def bar_range(cal_lo, cal_hi):
    """自然日跨度 → 需要枚举的【交易日】长度范围。

    实测交易日/自然日 ≈ 0.714，这里放宽到 [0.55, 0.95] 以免漏掉边界。
    必须与 rubber_core.js 的 barRange 保持一致，否则界面与报告会对不上。
    """
    return max(2, int(cal_lo * 0.55)), max(3, -(-int(cal_hi * 0.95 * 1000) // 1000))


def window_cases(bars, dates, legs, cities, cal_lo, cal_hi, step, max_stale,
                 min_common, min_f):
    """枚举长度落在 [cal_lo, cal_hi] 自然日内的窗口，按起点所处象限归类。

    **与案例层的区别**：这里的"象限"是窗口起点【所处】的趋势，
    不是窗口本身的性质。样本大得多，但口径不同。
    """
    owner = {}
    for k, lg in enumerate(legs):
        for j in range(lg.start_idx, lg.end_idx + 1):
            owner[j] = k
    lo, hi = bar_range(cal_lo, cal_hi)
    out = []
    n = len(bars)
    for L in range(lo, hi + 1):
        for i in range(0, n - L, step):
            cal = (d(dates[i + L]) - d(dates[i])).days
            if cal < cal_lo or cal > cal_hi:
                continue
            k = owner.get(i)
            if k is None:
                continue
            pr = pair_common(cities, dates[i], dates[i + L], max_stale, min_common)
            if pr is None:
                continue
            F = (bars[i + L].close / bars[i].close - 1) * 100.0
            R = (pr["r1"] - pr["r0"]) / pr["r0"] * 100.0
            out.append({
                "象限": quad_of(legs[k].trend_dir, legs[k].direction),
                "起点日": dates[i], "终点日": dates[i + L],
                "自然日数": cal, "交易日数": L,
                "F": F, "R": R, "G": R - F,
                "A": (R / F) if abs(F) >= min_f else None,
            })
    return out


def non_overlapping(lst):
    """贪心抽互不重叠的样本 —— 窗口相邻间重叠 (L-1)/L，这才是有效样本量。"""
    v = sorted(lst, key=lambda x: x["起点日"])
    out, last = [], None
    for x in v:
        if last is None or x["起点日"] >= last:
            out.append(x)
            last = x["终点日"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="四象限 × 自然日跨度 案例统计")
    ap.add_argument("--minor", type=float, default=5.0, help="ZigZag 细阈值%")
    ap.add_argument("--major", type=float, default=15.0, help="ZigZag 粗阈值%")
    ap.add_argument("--min-duration", type=int, default=2, help="区间最短交易日")
    ap.add_argument("--step", type=int, default=1, help="统计层窗口滑动步长（交易日）")
    ap.add_argument("--span", action="append", default=None,
                    help="自然日跨度档，可重复，如 --span 30-37 --span 55-65")
    ap.add_argument("--max-stale", type=int, default=7)
    ap.add_argument("--min-common", type=int, default=6)
    ap.add_argument("--min-f", type=float, default=2.0, help="可算 A 的最小 |F|%")
    ap.add_argument("--futures", default="adj", choices=list(FUT_CHOICES))
    ap.add_argument("--price", default="close")
    ap.add_argument("--level", default="30天趋势",
                    help="趋势级别名（决定 min_rise / window / 均线口径）")
    ap.add_argument("--full-ohlc", dest="full_ohlc", action="store_true", default=True,
                    help="用真实 OHLC 切分（默认；ZigZag 在 high/low 上找极值）")
    ap.add_argument("--flat-ohlc", dest="full_ohlc", action="store_false",
                    help="把 OHLC 压平为单一口径（旧行为，仅作对照）")
    args = ap.parse_args()

    spans = [parse_span(s) for s in (args.span or ["30-37", "55-65"])]
    bars, dates = load_futures(FUT_CHOICES[args.futures], args.price,
                               full_ohlc=args.full_ohlc)
    cities = load_cities()
    i0 = next(i for i, x in enumerate(dates) if x >= min(cities))
    bars, dates = bars[i0:], dates[i0:]

    # ⚠ 2026-09 修正（审计 B2 + 兜底口径）：
    #   旧调用只传 6 个参数，window / min_rise / judge 全部走默认值，
    #   于是「30天趋势」级别定义（min_days=30 / min_rise=7.2% / window=37）
    #   完全没生效 —— 实际用的是 min_rise=5% + 无窗口约束。
    #   label_legs 的兜底分支（小区间中点落在所有粗区间之外时）会因此
    #   用弱口径算整体方向，与顶部判定不同源。现在从 preset_params 取齐。
    p = preset_params(args.level, "day")
    a = judge_args(p)
    legs = label_legs(bars, major_reversal_pct=args.major,
                      minor_reversal_pct=args.minor,
                      min_duration_bars=args.min_duration,
                      min_change_pct=0.0,
                      max_drawdown_limit=a["max_drawdown_limit"],
                      max_runup_limit=a["max_runup_limit"],
                      window=a["window"], min_rise=a["min_rise"], judge=a)
    cases, drop = build_cases(bars, dates, legs, cities, args.max_stale,
                              args.min_common, args.min_f)

    print(f"期货 {FUT_LABEL[args.futures]}，{len(bars)} 根（{dates[0]} ~ {dates[-1]}）")
    print(f"ZigZag 细 {args.minor}% / 粗 {args.major}% / 最短 {args.min_duration} 交易日")
    print(f"级别 {args.level}：window {a['window']} 根 / min_rise {a['min_rise']}% / "
          f"ma {(a['ma_short'], a['ma_long'])}")
    print(f"OHLC：{'真实极值（ZigZag 在 high/low 上找拐点）' if args.full_ohlc else '已压平（旧行为）'}")
    print(f"切出区间 {len(legs)} 个 → 有效案例 {len(cases)} 个"
          f"（丢弃 {drop['noObs']} 个端点无观测 + {drop['common']} 个共同市县不足）")

    # ---------- 案例计数网格 ----------
    grid = {}
    for q in QUAD_ORDER:
        row = [c for c in cases if c["象限"] == q]
        grid[q] = {"all": row, "cells": []}
        for lo, hi in spans:
            sel = [c for c in row if lo <= c["自然日数"] <= hi]
            grid[q]["cells"].append((lo, hi, sel, summarise(sel)))

    print()
    print("=" * 92)
    print("案例计数网格（单元格 = 案例数 ｜ F中位% / R中位% / A中位）")
    print("=" * 92)
    hdr = f"{'象限':<26}" + "".join(f"{f'{lo}-{hi}日':>22}" for lo, hi in spans) \
        + f"{'全部':>10}"
    print(hdr)
    print("-" * 92)
    for q in QUAD_ORDER:
        if q == "3-平稳区间" and not grid[q]["all"]:
            continue
        cells = []
        for lo, hi, sel, st in grid[q]["cells"]:
            if st["n"] == 0:
                cells.append("— 空")
            else:
                cells.append(f"{st['n']}个 {st['Fmed']:+.1f}/{st['Rmed']:+.1f}/"
                             f"{st['Amed']:.2f}" if st["Amed"] is not None
                             else f"{st['n']}个 {st['Fmed']:+.1f}/{st['Rmed']:+.1f}/—")
        print(f"{q:<26}" + "".join(f"{c:>22}" for c in cells)
              + f"{len(grid[q]['all']):>10}")

    # ---------- 时长分布 ----------
    print()
    print("=" * 92)
    print("每类区间的【自然日长度】分布 —— 这是「跨度该设多少」的依据")
    print("=" * 92)
    print(f"{'象限':<26}{'n':>5}{'最短':>7}{'25%':>7}{'中位':>7}{'75%':>7}{'最长':>7}")
    print("-" * 92)
    durstats = {}
    for q in QUAD_ORDER:
        v = sorted(c["自然日数"] for c in grid[q]["all"])
        if not v:
            durstats[q] = None
            continue
        n = len(v)
        durstats[q] = {"n": n, "min": v[0], "max": v[-1], "median": _med(v),
                       "q25": v[int(round(.25 * (n - 1)))],
                       "q75": v[int(round(.75 * (n - 1)))], "all": v}
        print(f"{q:<26}{n:>5}{v[0]:>7}{v[int(round(.25*(n-1)))]:>7}"
              f"{_med(v):>7.0f}{v[int(round(.75*(n-1)))]:>7}{v[-1]:>7}")

    _md(cases, grid, durstats, spans, args, drop, FUT_LABEL[args.futures])

    # ---------- 统计层 + 样本量诊断 ----------
    print()
    print("=" * 92)
    print("统计层：固定长度窗口按「起点所处象限」分组（口径不同，样本大得多）")
    print("=" * 92)
    stat = {}
    for lo, hi in spans:
        wins = window_cases(bars, dates, legs, cities, lo, hi, args.step,
                            args.max_stale, args.min_common, args.min_f)
        byq = collections.defaultdict(list)
        for w in wins:
            byq[w["象限"]].append(w)
        rows = []
        for q in QUAD_ORDER:
            lst = byq.get(q, [])
            if not lst:
                continue
            st = summarise(lst)
            ind = non_overlapping(lst)
            Aind = [x["A"] for x in ind if x["A"] is not None]
            st["nIndep"] = len(ind)
            st["AindMed"] = _med(Aind) if Aind else None
            st["ci"] = block_bootstrap_ci(Aind, seed=20260917) if len(Aind) >= 8 else (None, None)
            rows.append((q, st))
        stat[(lo, hi)] = rows
        print(f"\n--- {lo}-{hi} 自然日 ---")
        print(f"{'象限':<26}{'名义n':>8}{'独立n':>8}{'F中位':>8}{'R中位':>8}"
              f"{'A中位':>8}{'A中位(独立)':>11}{'独立A的95%CI':>20}")
        print("-" * 92)
        for q, st in rows:
            ci = (f"[{st['ci'][0]:.2f},{st['ci'][1]:.2f}]"
                  if st["ci"][0] is not None else "独立样本不足")
            am = f"{st['AindMed']:.3f}" if st["AindMed"] is not None else "—"
            print(f"{q:<26}{st['n']:>8}{st['nIndep']:>8}{st['Fmed']:>+8.2f}"
                  f"{st['Rmed']:>+8.2f}{st['Amed']:>8.3f}{am:>11}{ci:>20}")

    # 样本量诊断：独立样本数随窗口长度变化
    print()
    print("=" * 92)
    print("样本量诊断：独立样本数 vs 窗口长度（说明「样本够不够」不是调参能解决的）")
    print("=" * 92)
    diag = []
    for L in (10, 15, 20, 25, 30, 37, 45, 60, 90):
        ws = window_cases(bars, dates, legs, cities, L, L, max(args.step, 2),
                          args.max_stale, args.min_common, args.min_f)
        byq = collections.defaultdict(list)
        for w in ws:
            byq[w["象限"]].append(w)
        per = [len(non_overlapping(byq.get(q, []))) for q in QUAD_ORDER[:4]]
        diag.append((L, per, sum(per)))
        print(f"  窗口 {L:>3} 自然日：独立样本 "
              f"主升 {per[0]:>3}  回调 {per[1]:>3}  反弹 {per[2]:>3}  主跌 {per[3]:>3}"
              f"   合计 {sum(per):>4}（每格 ~{sum(per)/4:.0f}）")

    _md_stat(stat, diag, spans, drop, FUT_LABEL[args.futures], args)
    print()
    print(f"报告 → {OUT_MD}")


def _md(cases, grid, durstats, spans, args, drop, fut_label):
    L = []
    A = L.append
    A("## 九、按「四象限 × 自然日跨度」重看案例\n")
    A("**这一节的口径与前面各节不同**：前面用滑动窗口（一个窗口一个样本，数千个）；")
    A("这里用 **ZigZag 区间本身**（一个趋势区间一个样本），")
    A("区间的类型是它**自己**的性质（主升/回调/反弹/主跌），")
    A("而不是「窗口起点所处的趋势」。跨度按**自然日**计。\n")
    A(f"- 切分参数：ZigZag 细 **{args.minor}%** / 粗 **{args.major}%** / "
      f"最短 {args.min_duration} 交易日")
    A(f"- 期货：{fut_label}，{args.price} 口径；原料：共同市县 ≥ {args.min_common}，"
      f"端点 staleness ≤ {args.max_stale} 自然日")
    A(f"- 切出区间 **{len(cases) + drop['noObs'] + drop['common']}** 个 → "
      f"有效案例 **{len(cases)}** 个"
      f"（丢弃 {drop['noObs']} 个端点无观测、{drop['common']} 个共同市县不足）\n")

    A("### 案例计数网格\n")
    A("| 象限 | " + " | ".join(f"{lo}–{hi} 自然日" for lo, hi in spans)
      + " | 全部 |")
    A("|---" * (len(spans) + 2) + "|")
    for q in QUAD_ORDER:
        if q == "3-平稳区间" and not grid[q]["all"]:
            continue
        cells = []
        for lo, hi, sel, st in grid[q]["cells"]:
            if st["n"] == 0:
                cells.append("**0（空）**")
            else:
                a = f"{st['Amed']:.3f}" if st["Amed"] is not None else "—"
                cells.append(f"{st['n']} 个<br>F {st['Fmed']:+.2f}%<br>"
                             f"R {st['Rmed']:+.2f}%<br>A {a}")
        A(f"| {q} | " + " | ".join(cells) + f" | {len(grid[q]['all'])} |")
    A("")
    A("> **空格子的含义**：该跨度下这一类型的区间根本不存在。")
    A("> 这不是阈值没调好，是市场结构 —— 见下面的时长分布。\n")

    A("### 每类区间的自然日长度分布\n")
    A("| 象限 | n | 最短 | 25% | 中位 | 75% | 最长 |")
    A("|---|---|---|---|---|---|---|")
    for q in QUAD_ORDER:
        s = durstats.get(q)
        if not s:
            continue
        A(f"| {q} | {s['n']} | {s['min']} | {s['q25']} | {s['median']:.0f} | "
          f"{s['q75']} | {s['max']} |")
    A("")
    A("四类的自然时长差得很远：主升/主跌能拖很久，回调/反弹短促。")
    A("**卡一个跨度必然有格子空掉** —— 想让四类都有货，跨度得覆盖它们重叠的那一段。\n")

    A("### 全部案例明细\n")
    A("| # | 象限 | 幅度档 | 起止 | 自然日 | 交易日 | 期货F% | 原料R% | A | "
      "共同市县 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for c in sorted(cases, key=lambda x: x["起点日"]):
        a = f"{c['A']:.3f}" if c["A"] is not None else "—"
        A(f"| {c['序号']} | {c['象限'][:2]} | {c['幅度档']} | "
          f"{c['起点日']}~{c['终点日']} | {c['自然日数']} | {c['交易日数']} | "
          f"{c['F']:+.2f} | {c['R']:+.2f} | {a} | {c['共同市县数']} |")
    A("")

    A("### 结论口径的警告\n")
    A(f"本口径下每格只有 1~5 个案例（合计 {len(cases)} 个），"
      "**不足以做任何显著性判断**。")
    A("界面版（`橡胶调参界面.html`）把 ZigZag 阈值与跨度都做成可调，")
    A("用来找「既有足够样本、又不把不同类型的区间混在一起」的那个参数组合。\n")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def _md_stat(stat, diag, spans, drop, fut_label, args):
    """把统计层与样本量诊断追加到报告 —— **补进去，不覆盖案例层**。"""
    L = []
    A = L.append
    A("")
    A("---\n")
    A("### 统计层：同一四象限问题，但样本大得多\n")
    A("**口径与上面不同，必须分清**：上面「案例层」的象限是区间**本身**的性质；")
    A("这里「统计层」把长度落在该跨度内的**所有窗口**按「**起点所处**的趋势」分组。")
    A("换来的是大得多的样本，代价是口径变了。\n")
    A("> **有效样本量是「独立 n」那一列，不是「名义 n」。**")
    A("> 相邻窗口重叠 (L−1)/L，名义上几千个，真正独立的只有 `总跨度 ÷ 窗口长度 ÷ 4` 个。")
    A("> 置信区间一律基于互不重叠子样本 —— 对全部重叠窗口做 bootstrap 会给出假窄的区间。\n")
    for lo, hi in spans:
        rows = stat.get((lo, hi), [])
        A(f"**{lo}–{hi} 自然日**\n")
        if not rows:
            A("该跨度下没有任何窗口（跨度太短，窗口长度不足一天）。\n")
            continue
        A("| 象限 | 名义 n | **独立 n** | F中位 | R中位 | A中位 | A中位(独立样本) "
          "| 独立样本的95%CI |")
        A("|---|---|---|---|---|---|---|---|")
        for q, st in rows:
            ci = (f"[{st['ci'][0]:.2f}, {st['ci'][1]:.2f}]"
                  if st["ci"][0] is not None else "独立样本不足，不给区间")
            am = f"{st['AindMed']:.3f}" if st["AindMed"] is not None else "—"
            A(f"| {q} | {st['n']} | **{st['nIndep']}** | {st['Fmed']:+.2f} | "
              f"{st['Rmed']:+.2f} | {st['Amed']:.3f} | {am} | {ci} |")
        A("")
        A("> ⚠️ 注意「A中位」与「A中位(独立样本)」两列的差距。")
        A("> 两者差得越大，说明**点估计对「取哪几个独立样本」越敏感**，")
        A("> 也就越不该当精确值引用。这是样本量不足的直接证据。\n")

    A("### 样本量诊断：「样本够不够」不是调参能解决的\n")
    A("独立样本数 ≈ 总时间跨度 ÷ 窗口长度 ÷ 4。窗口压得越短样本越多，")
    A("但窗口越短、「趋势区间」的性质越弱 —— 这是个硬权衡：\n")
    A("| 窗口长度(自然日) | 主升 | 回调 | 反弹 | 主跌 | 合计 | 每格平均 |")
    A("|---|---|---|---|---|---|---|")
    for w, per, tot in diag:
        A(f"| {w} | {per[0]} | {per[1]} | {per[2]} | {per[3]} | **{tot}** | "
          f"~{tot/4:.0f} |")
    A("")
    A("**数据只有 6.2 年**，所以：窗口压到 20 天每格约 22 个独立样本，")
    A("30 天约 16 个，**60 天只剩约 9 个**。\n")
    A("要把每格独立样本从 16 提到 50，只有三条路，且都不在「调参」范围内：\n")
    A("1. **拉长历史** —— 最直接，但免费原料数据源已确认封死在 2020-06")
    A("（海南省价格监测中心的列表分页上限、无存档页、无站内检索；商业源要付费）；")
    A("2. **增加产区** —— 云南西双版纳的原料价是**每日**发布的（隆众/Mysteel），")
    A("但可抓取的历史只有约 2 年，且文章页有反爬（HTTP 451），只能增量补充近期；")
    A("3. **缩短窗口** —— 立刻见效，但研究对象就从「月度级行情」变成「周级波动」了。\n")
    A("**结论：在现有数据下，本口径的每格有效样本量约 9~26 个，")
    A("置信区间必然很宽。这个限制应当写在结论旁边，而不是回避。**\n")

    with open(OUT_MD, "a", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()

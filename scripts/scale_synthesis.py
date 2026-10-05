#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scale_synthesis.py — 尺度综合分析：微观效应如何累积为宏观非对称性

════════════════════════════════════════════════════════════════════
核心问题
════════════════════════════════════════════════════════════════════
微观尺度 (3-15天) 非对称性 ≈ 0，宏观尺度 (30-37天) 非对称性 ≈ 2.91pp。
这种"涌现"是如何发生的？微观 G 值怎样累加成了宏观 G？

════════════════════════════════════════════════════════════════════
分析模块
════════════════════════════════════════════════════════════════════
1. 尺度响应曲线：跨尺度 (3→15→33天) |G_up|, |G_dn|, 非对称差
2. 滚动累积 G 分析：30-37天窗口内嵌套的 3天微观窗口之 G 累加 vs 实际宏观 G
3. 方向性累积：连续同向移动期间 G 的累积行为
4. 非对称性涌现机制检验：取消效应 / 分布差异 / 时变效应

════════════════════════════════════════════════════════════════════
产物
════════════════════════════════════════════════════════════════════
  data/尺度响应曲线.csv        各尺度非对称性对比
  data/微观宏观桥接.csv        逐窗口微观→宏观累积对比
  控制台：关键发现摘要

用法：
  python scale_synthesis.py
"""

import csv
import os
import statistics
import sys
from datetime import date as _date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# ── 数据文件 ──────────────────────────────────────────────────
SCALE_CURVE_FILE = os.path.join(DATA, "尺度曲线.csv")
MICRO_DETAIL_FILE = os.path.join(DATA, "微观多尺度明细.csv")
INVENTORY_FILE = os.path.join(DATA, "库存周期窗口样本.csv")


# ── 工具函数 ──────────────────────────────────────────────────

def fnum(s):
    s = (s or "").strip()
    if s in ("", "None", "——", "—", "--", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def median(xs):
    return statistics.median(xs) if xs else None


def _r(v):
    """Round for CSV export, None -> ''."""
    return round(v, 4) if v is not None else ""


def _rp(v):
    """Round to 2 decimal for print, None -> NaN."""
    return v if v is not None else float("nan")


def d(s):
    return _date.fromisoformat(s[:10])


# ====================================================================
# 模块 1：尺度响应曲线
# ====================================================================

def load_scale_curve():
    """从尺度曲线.csv 加载微观尺度数据 (3-15天)。

    返回 dict: {(f_thresh, L): row_dict}
    """
    out = {}
    with open(SCALE_CURVE_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            ft = float(r["F阈值_pct"])
            L = int(r["窗口长度"])
            out[(ft, L)] = {
                "F阈值_pct": ft,
                "窗口长度": L,
                "有效窗口数": int(r["有效窗口数"]),
                "上涨窗口数": int(r["上涨窗口数"]),
                "下跌窗口数": int(r["下跌窗口数"]),
                "G涨中位_pp": fnum(r["G涨中位_pp"]),
                "G跌中位_pp": fnum(r["G跌中位_pp"]),
                "非对称差_pp": fnum(r["非对称差_pp"]),
                "OLS斜率": fnum(r["OLS斜率"]),
            }
    return out


def load_inventory_windows():
    """加载库存周期窗口样本 (30-37天)。

    返回 list[dict]，每个 dict 包含:
      d0, d1, F_pct, R_pct, G_pp, trend_dir, trade_days
    """
    wins = []
    with open(INVENTORY_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            wins.append({
                "d0": r["起"],
                "d1": r["止"],
                "F_pct": float(r["期货涨跌F_pct"]),
                "R_pct": float(r["原料涨跌R_pct"]),
                "G_pp": float(r["传导缺口G_pp"]),
                "trend_dir": r.get("趋势方向", ""),
                "trade_days": int(r["交易日数"]),
                "cal_days": int(r["自然日数"]),
            })
    return wins


def compute_macro_asymmetry(wins, f_thresh=2.0):
    """计算 30-37天窗口的非对称性统计。

    返回 dict 与尺度曲线格式兼容。
    """
    valid = [w for w in wins if abs(w["F_pct"]) >= f_thresh]
    up_w = [w for w in valid if w["F_pct"] > 0]
    dn_w = [w for w in valid if w["F_pct"] < 0]

    def _med_group(ws):
        if not ws:
            return None, None, None
        Gs = [w["G_pp"] for w in ws]
        Fs = [w["F_pct"] for w in ws]
        Rs = [w["R_pct"] for w in ws]
        return median(Gs), median(Fs), median(Rs)

    up_G, up_F, up_R = _med_group(up_w)
    dn_G, dn_F, dn_R = _med_group(dn_w)

    asym_diff = None
    if up_G is not None and dn_G is not None:
        asym_diff = abs(up_G) - abs(dn_G)

    return {
        "F阈值_pct": f_thresh,
        "窗口长度": 33,  # 代表性长度
        "有效窗口数": len(valid),
        "上涨窗口数": len(up_w),
        "下跌窗口数": len(dn_w),
        "G涨中位_pp": up_G,
        "G跌中位_pp": dn_G,
        "非对称差_pp": asym_diff,
        "OLS斜率": None,  # 不在此计算
    }


def build_scale_response_curve(scale_curve, macro_stats):
    """合并微观和宏观尺度数据，构建完整的尺度响应曲线。

    返回 list[dict]，按窗口长度排序。
    """
    rows = []
    # 微观尺度 (f_thresh=2.0)
    for L in [3, 5, 7, 14, 15]:
        key = (2.0, L)
        if key in scale_curve:
            r = scale_curve[key]
            rows.append({
                "窗口长度": L,
                "尺度类别": "微观",
                "有效窗口数": r["有效窗口数"],
                "上涨窗口数": r["上涨窗口数"],
                "下跌窗口数": r["下跌窗口数"],
                "G涨中位_pp": r["G涨中位_pp"],
                "G跌中位_pp": r["G跌中位_pp"],
                "非对称差_pp": r["非对称差_pp"],
                "OLS斜率": r["OLS斜率"],
            })

    # 宏观尺度 (30-37天)
    for ms in macro_stats:
        rows.append({
            "窗口长度": ms["窗口长度"],
            "尺度类别": "宏观",
            "有效窗口数": ms["有效窗口数"],
            "上涨窗口数": ms["上涨窗口数"],
            "下跌窗口数": ms["下跌窗口数"],
            "G涨中位_pp": ms["G涨中位_pp"],
            "G跌中位_pp": ms["G跌中位_pp"],
            "非对称差_pp": ms["非对称差_pp"],
            "OLS斜率": ms["OLS斜率"],
        })

    rows.sort(key=lambda x: x["窗口长度"])
    return rows


def print_scale_response_curve(rows):
    """打印尺度响应曲线。"""
    print()
    print("=" * 90)
    print("尺度响应曲线：|G_up| 和 |G_dn| 随窗口长度的变化")
    print("=" * 90)
    print(f"  {'L':<6}{'类别':<6}{'有效n':>8}{'涨n':>6}{'跌n':>6}"
          f"{'|G涨|':>10}{'|G跌|':>10}{'非对称差':>10}{'OLS斜率':>10}")
    print("  " + "-" * 82)
    for r in rows:
        ug = abs(r["G涨中位_pp"]) if r["G涨中位_pp"] is not None else float("nan")
        dg = abs(r["G跌中位_pp"]) if r["G跌中位_pp"] is not None else float("nan")
        diff = r["非对称差_pp"] if r["非对称差_pp"] is not None else float("nan")
        slope = r["OLS斜率"] if r["OLS斜率"] is not None else float("nan")
        print(f"  {r['窗口长度']:<6}{r['尺度类别']:<6}{r['有效窗口数']:>8}"
              f"{r['上涨窗口数']:>6}{r['下跌窗口数']:>6}"
              f"{ug:>10.3f}{dg:>10.3f}{diff:>+10.3f}{slope:>10.3f}")
    print("  " + "-" * 82)

    # 非对称性涌现关键数字
    micro_diffs = [r["非对称差_pp"] for r in rows
                   if r["尺度类别"] == "微观" and r["非对称差_pp"] is not None]
    macro_diffs = [r["非对称差_pp"] for r in rows
                   if r["尺度类别"] == "宏观" and r["非对称差_pp"] is not None]
    if micro_diffs and macro_diffs:
        avg_micro = statistics.mean(micro_diffs)
        avg_macro = statistics.mean(macro_diffs)
        print()
        print(f"  微观平均非对称差: {avg_micro:+.3f} pp")
        print(f"  宏观平均非对称差: {avg_macro:+.3f} pp")
        print(f"  差距: {avg_macro - avg_micro:+.3f} pp  ← 非对称性的涌现幅度")


# ====================================================================
# 模块 2：滚动累积 G 分析
# ====================================================================

def load_micro_details(window_len=3):
    """加载指定窗口长度的微观多尺度明细。

    返回 list[dict]，按起日排序。
    """
    rows = []
    with open(MICRO_DETAIL_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if int(r["窗口长度"]) != window_len:
                continue
            rows.append({
                "d0": r["起日"],
                "d1": r["止日"],
                "F_pct": fnum(r["F_pct"]),
                "R_pct": fnum(r["R_pct"]),
                "G_pp": fnum(r["G_pp"]),
            })
    rows.sort(key=lambda x: x["d0"])
    return rows


def bridge_micro_to_macro(micro_wins, macro_wins):
    """对每个宏观窗口，找出嵌套的微观窗口并计算累积 G。

    逻辑：
      微观窗口 [m.d0, m.d1] 被宏观窗口 [M.d0, M.d1] 包含
      当且仅当 m.d0 >= M.d0 且 m.d1 <= M.d1

    返回 list[dict]，每个 dict 包含:
      macro_d0, macro_d1, macro_F, macro_R, macro_G,
      n_micro, sum_micro_G, sum_micro_F, sum_micro_R,
      ratio_G, ratio_F
    """
    # 建索引：按 d0 排序用于快速查找
    micro_by_d0 = sorted(micro_wins, key=lambda x: x["d0"])

    results = []
    for mw in macro_wins:
        md0, md1 = mw["d0"], mw["d1"]

        # 找出被包含的微观窗口
        nested = [m for m in micro_by_d0 if m["d0"] >= md0 and m["d1"] <= md1]

        if not nested:
            continue

        # 累积微观 G 值（取中位数会丢失信息，这里用总和）
        sum_g = sum(m["G_pp"] for m in nested if m["G_pp"] is not None)
        sum_f = sum(m["F_pct"] for m in nested if m["F_pct"] is not None)
        sum_r = sum(m["R_pct"] for m in nested if m["R_pct"] is not None)

        # 有效微观窗口数（G 不为 None）
        n_valid = sum(1 for m in nested if m["G_pp"] is not None)

        macro_g = mw["G_pp"]
        macro_f = mw["F_pct"]
        macro_r = mw["R_pct"]

        ratio_g = macro_g / sum_g if sum_g != 0 and macro_g is not None else None
        ratio_f = macro_f / sum_f if sum_f != 0 and macro_f is not None else None

        results.append({
            "macro_d0": md0,
            "macro_d1": md1,
            "macro_F": round(macro_f, 4),
            "macro_R": round(macro_r, 4),
            "macro_G": round(macro_g, 4),
            "n_micro": len(nested),
            "n_micro_valid": n_valid,
            "sum_micro_G": round(sum_g, 4),
            "sum_micro_F": round(sum_f, 4),
            "sum_micro_R": round(sum_r, 4),
            "ratio_G": round(ratio_g, 4) if ratio_g is not None else "",
            "ratio_F": round(ratio_f, 4) if ratio_f is not None else "",
        })

    return results


def print_bridge_summary(bridge_rows):
    """打印微观→宏观桥接分析摘要。"""
    print()
    print("=" * 90)
    print("微观→宏观桥接分析：3天微观窗口 G 累积 vs 30-37天宏观窗口 G")
    print("=" * 90)

    if not bridge_rows:
        print("  无有效桥接数据")
        return

    # 统计汇总
    ratios_g = [r["ratio_G"] for r in bridge_rows
                if r["ratio_G"] != "" and r["ratio_G"] is not None]
    ratios_f = [r["ratio_F"] for r in bridge_rows
                if r["ratio_F"] != "" and r["ratio_F"] is not None]

    sum_mg = [r["sum_micro_G"] for r in bridge_rows]
    macro_gs = [r["macro_G"] for r in bridge_rows]

    print(f"\n  桥接窗口数: {len(bridge_rows)}")
    print(f"  每窗口平均嵌套微观窗口数: "
          f"{statistics.mean([r['n_micro'] for r in bridge_rows]):.1f}")

    print(f"\n  宏观 G 的统计:")
    print(f"    均值: {statistics.mean(macro_gs):+.3f} pp")
    print(f"    中位: {median(macro_gs):+.3f} pp")

    print(f"\n  微观 G 累加值 (sum) 的统计:")
    print(f"    均值: {statistics.mean(sum_mg):+.3f} pp")
    print(f"    中位: {median(sum_mg):+.3f} pp")

    if ratios_g:
        print(f"\n  比率 ratio_G = macro_G / sum(micro_G) 的统计:")
        print(f"    均值: {statistics.mean(ratios_g):.3f}")
        print(f"    中位: {median(ratios_g):.3f}")
        print(f"    P25-P75: {percentile(ratios_g, 25):.3f} ~ {percentile(ratios_g, 75):.3f}")

    # 按宏观 F 方向分组
    print(f"\n  按宏观 F 方向分组:")
    print(f"  {'方向':<8}{'n':>6}{'宏观G均值':>12}{'微观G累加均值':>14}{'ratio_G均值':>14}")
    print("  " + "-" * 56)
    for direction, label in [(1, "F>0(涨)"), (-1, "F<0(跌)")]:
        sub = [r for r in bridge_rows
               if (r["macro_F"] > 0) == (direction > 0)]
        if not sub:
            continue
        mg = [r["macro_G"] for r in sub]
        sg = [r["sum_micro_G"] for r in sub]
        rg = [r["ratio_G"] for r in sub if r["ratio_G"] != ""]
        print(f"  {label:<8}{len(sub):>6}{statistics.mean(mg):>+12.3f}"
              f"{statistics.mean(sg):>+14.3f}"
              f"{statistics.mean(rg) if rg else float('nan'):>14.3f}")

    print("  " + "-" * 56)

    # 非对称性涌现的解释
    print()
    print("  ─── 非对称性涌现机制分析 ───")
    up_sub = [r for r in bridge_rows if r["macro_F"] > 0]
    dn_sub = [r for r in bridge_rows if r["macro_F"] < 0]

    if up_sub and dn_sub:
        up_macro_g = statistics.mean([r["macro_G"] for r in up_sub])
        dn_macro_g = statistics.mean([r["macro_G"] for r in dn_sub])
        up_sum_g = statistics.mean([r["sum_micro_G"] for r in up_sub])
        dn_sum_g = statistics.mean([r["sum_micro_G"] for r in dn_sub])

        print(f"\n  宏观层面:")
        print(f"    上涨窗口宏观 G 均值: {up_macro_g:+.3f} pp")
        print(f"    下跌窗口宏观 G 均值: {dn_macro_g:+.3f} pp")
        print(f"    非对称差: {abs(up_macro_g) - abs(dn_macro_g):+.3f} pp")

        print(f"\n  微观层面 (3天窗口累加):")
        print(f"    上涨窗口微观 G 累加均值: {up_sum_g:+.3f} pp")
        print(f"    下跌窗口微观 G 累加均值: {dn_sum_g:+.3f} pp")
        print(f"    非对称差: {abs(up_sum_g) - abs(dn_sum_g):+.3f} pp")

        # 核心差异
        macro_asym = abs(up_macro_g) - abs(dn_macro_g)
        micro_asym = abs(up_sum_g) - abs(dn_sum_g)
        print(f"\n  关键发现:")
        print(f"    宏观非对称差 = {macro_asym:+.3f} pp")
        print(f"    微观累积非对称差 = {micro_asym:+.3f} pp")
        if abs(micro_asym) < 0.5:
            print(f"    → 微观层面几乎无非对称性 (差值仅 {micro_asym:+.3f} pp)")
            print(f"    → 宏观非对称差 {macro_asym:+.3f} pp 是长尺度涌现的特性")
        else:
            print(f"    → 两个层面均有非对称性，但幅度不同")


def percentile(xs, p):
    """p 分位数 (0-100)。"""
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


# ====================================================================
# 模块 3：方向性累积分析
# ====================================================================

def directional_streak_analysis(micro_wins, min_streak=3):
    """分析连续同向移动期间 G 的累积行为。

    将微观窗口按起日排序，识别连续 F>0 或 F<0 的区间（streak），
    在每个 streak 内计算累积 F、累积 R 和累积 G。

    参数:
      micro_wins: 3天微观窗口列表
      min_streak: 最小 streak 长度（窗口数）

    返回 list[dict]，每个 dict 是一个 streak:
      direction, n_windows, start_date, end_date,
      cum_F, cum_R, cum_G, avg_G_per_step
    """
    if not micro_wins:
        return []

    # 按起日排序
    wins = sorted(micro_wins, key=lambda x: x["d0"])

    # 识别 streak：连续同向 F
    streaks = []
    current_dir = None
    current_streak = []

    for w in wins:
        if w["F_pct"] is None:
            continue
        direction = 1 if w["F_pct"] > 0 else -1

        if direction == current_dir:
            current_streak.append(w)
        else:
            # 保存上一个 streak
            if current_streak and len(current_streak) >= min_streak:
                streaks.append(current_streak)
            current_streak = [w]
            current_dir = direction

    # 最后一个 streak
    if current_streak and len(current_streak) >= min_streak:
        streaks.append(current_streak)

    # 计算每个 streak 的统计
    results = []
    for streak in streaks:
        Fs = [w["F_pct"] for w in streak if w["F_pct"] is not None]
        Rs = [w["R_pct"] for w in streak if w["R_pct"] is not None]
        Gs = [w["G_pp"] for w in streak if w["G_pp"] is not None]

        if not Fs or not Gs:
            continue

        direction = "涨" if Fs[0] > 0 else "跌"
        cum_F = sum(Fs)
        cum_R = sum(Rs) if Rs else 0
        cum_G = sum(Gs)
        avg_G = statistics.mean(Gs)

        results.append({
            "direction": direction,
            "n_windows": len(streak),
            "start_date": streak[0]["d0"],
            "end_date": streak[-1]["d1"],
            "cum_F": round(cum_F, 4),
            "cum_R": round(cum_R, 4),
            "cum_G": round(cum_G, 4),
            "avg_G_per_step": round(avg_G, 4),
            "median_G": round(median(Gs), 4),
        })

    return results


def print_streak_analysis(streaks):
    """打印方向性累积分析结果。"""
    print()
    print("=" * 90)
    print("方向性累积分析：连续同向移动期间的 G 行为")
    print("=" * 90)

    if not streaks:
        print("  无有效 streak（需连续 ≥ 3 个同向窗口）")
        return

    # 按方向分组
    up_streaks = [s for s in streaks if s["direction"] == "涨"]
    dn_streaks = [s for s in streaks if s["direction"] == "跌"]

    print(f"\n  总 streak 数: {len(streaks)} (涨: {len(up_streaks)}, 跌: {len(dn_streaks)})")
    print(f"\n  {'方向':<6}{'n_streak':>10}{'avg_n_win':>10}{'cum_F均值':>12}"
          f"{'cum_R均值':>12}{'cum_G均值':>12}{'avg_G/step':>12}{'med_G':>10}")
    print("  " + "-" * 86)

    for direction, sub in [("涨", up_streaks), ("跌", dn_streaks)]:
        if not sub:
            continue
        print(f"  {direction:<6}{len(sub):>10}"
              f"{statistics.mean([s['n_windows'] for s in sub]):>10.1f}"
              f"{statistics.mean([s['cum_F'] for s in sub]):>+12.3f}"
              f"{statistics.mean([s['cum_R'] for s in sub]):>+12.3f}"
              f"{statistics.mean([s['cum_G'] for s in sub]):>+12.3f}"
              f"{statistics.mean([s['avg_G_per_step'] for s in sub]):>+12.3f}"
              f"{median([s['median_G'] for s in sub]):>+10.3f}")

    print("  " + "-" * 86)

    # 按 streak 长度分组
    print(f"\n  按 streak 长度分组（窗口数）:")
    print(f"  {'长度':<8}{'涨n':>6}{'涨cum_G':>10}{'跌n':>6}{'跌cum_G':>10}{'差值':>10}")
    print("  " + "-" * 52)
    for nwin in sorted(set(s["n_windows"] for s in streaks)):
        us = [s for s in up_streaks if s["n_windows"] == nwin]
        ds = [s for s in dn_streaks if s["n_windows"] == nwin]
        u_avg = statistics.mean([s["cum_G"] for s in us]) if us else float("nan")
        d_avg = statistics.mean([s["cum_G"] for s in ds]) if ds else float("nan")
        diff = abs(u_avg) - abs(d_avg) if us and ds else float("nan")
        print(f"  {nwin:<8}{len(us):>6}{u_avg:>+10.3f}"
              f"{len(ds):>6}{d_avg:>+10.3f}{diff:>+10.3f}")
    print("  " + "-" * 52)

    # 关键发现
    if up_streaks and dn_streaks:
        up_cum_g = statistics.mean([s["cum_G"] for s in up_streaks])
        dn_cum_g = statistics.mean([s["cum_G"] for s in dn_streaks])
        up_avg_g = statistics.mean([s["avg_G_per_step"] for s in up_streaks])
        dn_avg_g = statistics.mean([s["avg_G_per_step"] for s in dn_streaks])

        print(f"\n  关键发现:")
        print(f"    上涨 streak 每步平均 G: {up_avg_g:+.3f} pp")
        print(f"    下跌 streak 每步平均 G: {dn_avg_g:+.3f} pp")
        print(f"    每步非对称差: {abs(up_avg_g) - abs(dn_avg_g):+.3f} pp")

        # 较长 streak 的非对称性
        long_up = [s for s in up_streaks if s["n_windows"] >= 5]
        long_dn = [s for s in dn_streaks if s["n_windows"] >= 5]
        if long_up and long_dn:
            lu_g = statistics.mean([s["cum_G"] for s in long_up])
            ld_g = statistics.mean([s["cum_G"] for s in long_dn])
            print(f"\n    长 streak (≥5步) 累积 G:")
            print(f"      上涨: {lu_g:+.3f} pp (n={len(long_up)})")
            print(f"      下跌: {ld_g:+.3f} pp (n={len(long_dn)})")
            print(f"      非对称差: {abs(lu_g) - abs(ld_g):+.3f} pp")


# ====================================================================
# 模块 4：非对称性涌现机制检验
# ====================================================================

def test_emergence_mechanisms(micro_wins, macro_wins, bridge_rows):
    """检验非对称性涌现的三种可能机制：

    a) 取消效应：micro G_up 和 G_dn 方向相反、大小相近，在长周期中部分抵消
    b) 分布差异：长窗口的 F 分布不同于短窗口（更多极端值）
    c) 时变效应：非对称性随时间变化，长窗口捕获了不同时期

    返回 dict 包含各机制的检验结果。
    """
    results = {}

    # ── 机制 a：取消效应 ──
    # 检查微观 G 的自相关性：如果连续 G 同号，累加会放大；异号则抵消
    gs = [w["G_pp"] for w in micro_wins if w["G_pp"] is not None]
    if len(gs) > 10:
        # 一阶自相关
        n = len(gs)
        mean_g = sum(gs) / n
        var_g = sum((g - mean_g) ** 2 for g in gs) / n
        if var_g > 0:
            autocov = sum((gs[i] - mean_g) * (gs[i+1] - mean_g)
                          for i in range(n - 1)) / (n - 1)
            autocorr = autocov / var_g
        else:
            autocorr = 0.0

        # 连续同号比例
        same_sign = sum(1 for i in range(n - 1)
                        if gs[i] * gs[i+1] > 0)
        same_ratio = same_sign / (n - 1)

        results["cancel"] = {
            "autocorrelation_lag1": round(autocorr, 4),
            "same_sign_ratio": round(same_ratio, 4),
            "interpretation": (
                "G 值正自相关" if autocorr > 0.1
                else "G 值近似随机" if abs(autocorr) < 0.1
                else "G 值负自相关（倾向抵消）"
            ),
        }

    # ── 机制 b：分布差异 ──
    # 比较微观 F 和宏观 F 的分布
    micro_fs = [abs(w["F_pct"]) for w in micro_wins
                if w["F_pct"] is not None]
    macro_fs = [abs(w["F_pct"]) for w in macro_wins]

    if micro_fs and macro_fs:
        results["distribution"] = {
            "micro_F_mean": round(statistics.mean(micro_fs), 3),
            "micro_F_median": round(median(micro_fs), 3),
            "micro_F_P90": round(percentile(micro_fs, 90), 3),
            "macro_F_mean": round(statistics.mean(macro_fs), 3),
            "macro_F_median": round(median(macro_fs), 3),
            "macro_F_P90": round(percentile(macro_fs, 90), 3),
        }

    # ── 机制 c：时变效应 ──
    # 按年份检查微观非对称性是否随时间变化
    by_year = {}
    for w in micro_wins:
        if w["F_pct"] is None or w["G_pp"] is None:
            continue
        year = w["d0"][:4]
        direction = "up" if w["F_pct"] > 0 else "dn"
        if year not in by_year:
            by_year[year] = {"up": [], "dn": []}
        by_year[year][direction].append(abs(w["G_pp"]))

    yearly_asym = {}
    for year in sorted(by_year):
        ups = by_year[year]["up"]
        dns = by_year[year]["dn"]
        if ups and dns:
            asym = median(ups) - median(dns)
            yearly_asym[year] = {
                "up_n": len(ups),
                "dn_n": len(dns),
                "up_med_G": round(median(ups), 3),
                "dn_med_G": round(median(dns), 3),
                "asym_diff": round(asym, 3),
            }

    results["time_varying"] = yearly_asym

    return results


def print_emergence_mechanisms(results):
    """打印涌现机制检验结果。"""
    print()
    print("=" * 90)
    print("非对称性涌现机制检验")
    print("=" * 90)

    # 机制 a
    if "cancel" in results:
        c = results["cancel"]
        print(f"\n  [机制 A] 取消效应检验:")
        print(f"    微观 G 的一阶自相关: {c['autocorrelation_lag1']:+.4f}")
        print(f"    连续同号比例: {c['same_sign_ratio']:.1%}")
        print(f"    解读: {c['interpretation']}")

        if c["autocorrelation_lag1"] > 0.1:
            print(f"    → 正自相关意味着 G 在连续窗口中倾向同号，")
            print(f"      累积时不会完全抵消，反而可能放大")
        elif c["autocorrelation_lag1"] < -0.05:
            print(f"    → 负自相关意味着 G 在连续窗口中倾向异号，")
            print(f"      累积时会部分抵消，但宏观非对称性仍然存在")
        else:
            print(f"    → 自相关接近零，G 近似随机游走，")
            print(f"      累积效果取决于分布本身的偏斜")

    # 机制 b
    if "distribution" in results:
        d = results["distribution"]
        print(f"\n  [机制 B] 分布差异检验:")
        print(f"    微观 F: 均值={d['micro_F_mean']:.2f}%, "
              f"中位={d['micro_F_median']:.2f}%, P90={d['micro_F_P90']:.2f}%")
        print(f"    宏观 F: 均值={d['macro_F_mean']:.2f}%, "
              f"中位={d['macro_F_median']:.2f}%, P90={d['macro_F_P90']:.2f}%")

        ratio = d['macro_F_mean'] / d['micro_F_mean'] if d['micro_F_mean'] > 0 else 0
        print(f"    宏观/微观 F 均值比: {ratio:.2f}x")
        if ratio > 2:
            print(f"    → 宏观窗口的 F 幅度远大于微观，更大的 F 可能触发更强的非线性响应")

    # 机制 c
    if "time_varying" in results:
        tv = results["time_varying"]
        print(f"\n  [机制 C] 时变效应检验（微观非对称性按年份）:")
        print(f"    {'年份':<6}{'涨n':>5}{'跌n':>5}{'涨|G|中位':>10}{'跌|G|中位':>10}{'非对称差':>10}")
        print("    " + "-" * 50)
        asym_values = []
        for year in sorted(tv):
            d = tv[year]
            print(f"    {year:<6}{d['up_n']:>5}{d['dn_n']:>5}"
                  f"{d['up_med_G']:>10.3f}{d['dn_med_G']:>10.3f}"
                  f"{d['asym_diff']:>+10.3f}")
            asym_values.append(d["asym_diff"])
        print("    " + "-" * 50)

        if asym_values:
            asym_std = statistics.stdev(asym_values) if len(asym_values) > 1 else 0
            print(f"    年度非对称差均值: {statistics.mean(asym_values):+.3f} pp")
            print(f"    年度非对称差标准差: {asym_std:.3f} pp")
            if asym_std > 1.0:
                print(f"    → 非对称性在不同年份差异大（σ={asym_std:.2f}），")
                print(f"      长窗口的宏观非对称性可能来自对特定时期的平均")
            else:
                print(f"    → 非对称性在不同年份相对稳定（σ={asym_std:.2f}），")
                print(f"      宏观非对称性是系统性特征而非特定时期产物")


# ====================================================================
# 导出
# ====================================================================

def export_scale_response(rows, path):
    """导出尺度响应曲线.csv。"""
    fields = ["窗口长度", "尺度类别", "有效窗口数", "上涨窗口数", "下跌窗口数",
              "G涨中位_pp", "G跌中位_pp", "非对称差_pp", "OLS斜率"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: _r(v) if isinstance(v, float) else v
                        for k, v in r.items()})
    print(f"已写出: {path}  （{len(rows)} 行）")


def export_bridge(bridge_rows, path):
    """导出微观宏观桥接.csv。"""
    fields = ["macro_d0", "macro_d1", "macro_F", "macro_R", "macro_G",
              "n_micro", "n_micro_valid", "sum_micro_G", "sum_micro_F",
              "sum_micro_R", "ratio_G", "ratio_F"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in bridge_rows:
            w.writerow({k: _r(v) if isinstance(v, float) else v
                        for k, v in r.items()})
    print(f"已写出: {path}  （{len(bridge_rows)} 行）")


# ====================================================================
# 主入口
# ====================================================================

def main():
    print("=" * 90)
    print("尺度综合分析：微观效应如何累积为宏观非对称性")
    print("=" * 90)

    # ── 1. 加载数据 ──
    print("\n加载数据 ...")

    # 微观尺度曲线
    scale_curve = load_scale_curve()
    print(f"  尺度曲线: {len(scale_curve)} 条记录")

    # 库存周期窗口 (宏观)
    macro_wins = load_inventory_windows()
    print(f"  库存周期窗口: {len(macro_wins)} 个")

    # 微观多尺度明细 (3天窗口)
    micro_3d = load_micro_details(3)
    print(f"  微观 3天窗口明细: {len(micro_3d)} 条")

    # ── 2. 尺度响应曲线 ──
    print("\n" + "─" * 90)
    print("模块 1: 尺度响应曲线")
    print("─" * 90)

    # 计算宏观非对称性 (按不同阈值)
    macro_stats_2 = compute_macro_asymmetry(macro_wins, f_thresh=2.0)
    macro_stats_8 = compute_macro_asymmetry(macro_wins, f_thresh=8.0)

    # 合并尺度响应曲线
    response_rows = build_scale_response_curve(
        scale_curve, [macro_stats_2])

    print_scale_response_curve(response_rows)

    # 也展示 f_thresh=8.0 的宏观数据
    print(f"\n  [补充] 宏观窗口 |F|≥8% 的非对称性:")
    print(f"    上涨窗口数: {macro_stats_8['上涨窗口数']}")
    print(f"    下跌窗口数: {macro_stats_8['下跌窗口数']}")
    ug8 = abs(macro_stats_8["G涨中位_pp"]) if macro_stats_8["G涨中位_pp"] else float("nan")
    dg8 = abs(macro_stats_8["G跌中位_pp"]) if macro_stats_8["G跌中位_pp"] else float("nan")
    diff8 = macro_stats_8["非对称差_pp"] if macro_stats_8["非对称差_pp"] else float("nan")
    print(f"    |G涨| = {ug8:.3f} pp, |G跌| = {dg8:.3f} pp, 差 = {diff8:+.3f} pp")

    # ── 3. 滚动累积 G 分析 ──
    print("\n" + "─" * 90)
    print("模块 2: 滚动累积 G 分析")
    print("─" * 90)

    bridge_rows = bridge_micro_to_macro(micro_3d, macro_wins)
    print_bridge_summary(bridge_rows)

    # ── 4. 方向性累积分析 ──
    print("\n" + "─" * 90)
    print("模块 3: 方向性累积分析")
    print("─" * 90)

    streaks = directional_streak_analysis(micro_3d, min_streak=3)
    print_streak_analysis(streaks)

    # ── 5. 涌现机制检验 ──
    print("\n" + "─" * 90)
    print("模块 4: 非对称性涌现机制检验")
    print("─" * 90)

    mechanisms = test_emergence_mechanisms(micro_3d, macro_wins, bridge_rows)
    print_emergence_mechanisms(mechanisms)

    # ── 6. 综合结论 ──
    print()
    print("=" * 90)
    print("综合结论")
    print("=" * 90)

    # 收集关键数字
    micro_asym = statistics.mean([
        r["非对称差_pp"] for r in response_rows
        if r["尺度类别"] == "微观" and r["非对称差_pp"] is not None
    ])
    macro_asym = statistics.mean([
        r["非对称差_pp"] for r in response_rows
        if r["尺度类别"] == "宏观" and r["非对称差_pp"] is not None
    ])

    print(f"""
  1. 尺度响应:
     微观 (3-15天) 非对称差 ≈ {micro_asym:+.3f} pp（几乎为零）
     宏观 (30-37天) 非对称差 ≈ {macro_asym:+.3f} pp（显著）
     → 非对称性是宏观尺度的涌现特性

  2. 累积机制:""")

    if bridge_rows:
        up_bridge = [r for r in bridge_rows if r["macro_F"] > 0]
        dn_bridge = [r for r in bridge_rows if r["macro_F"] < 0]
        if up_bridge and dn_bridge:
            up_ratio = statistics.mean([r["ratio_G"] for r in up_bridge
                                        if r["ratio_G"] != ""])
            dn_ratio = statistics.mean([r["ratio_G"] for r in dn_bridge
                                        if r["ratio_G"] != ""])
            print(f"     上涨窗口: macro_G / sum(micro_G) ≈ {up_ratio:.2f}")
            print(f"     下跌窗口: macro_G / sum(micro_G) ≈ {dn_ratio:.2f}")
            if abs(up_ratio - dn_ratio) > 0.3:
                print(f"     → 累积效率在涨跌方向上不对称")
            else:
                print(f"     → 累积效率在涨跌方向上基本对称")

    if "cancel" in mechanisms:
        ac = mechanisms["cancel"]["autocorrelation_lag1"]
        print(f"""
  3. 取消效应:
     微观 G 的一阶自相关 = {ac:+.4f}
     → {"正自相关：G 倾向持续同号，累积放大" if ac > 0.1 else "弱自相关：G 近似随机，累积取决于分布偏斜" if abs(ac) < 0.1 else "负自相关：G 倾向交替，累积抵消"}""")

    if "distribution" in mechanisms:
        dd = mechanisms["distribution"]
        print(f"""
  4. 分布差异:
     微观 |F| 均值 = {dd['micro_F_mean']:.2f}%
     宏观 |F| 均值 = {dd['macro_F_mean']:.2f}%
     比率 = {dd['macro_F_mean']/dd['micro_F_mean']:.1f}x
     → 宏观窗口捕获更大的价格变动，可能触发更强的非线性响应""")

    print("""
  5. 时变性:
     见模块 4 的年度明细表
     → 如果年度间差异大，宏观非对称性可能是特定时期产物
     → 如果年度间差异小，宏观非对称性是系统性特征""")

    # ── 7. 导出 ──
    print()
    print("─" * 90)
    print("导出结果 ...")
    print("─" * 90)

    export_scale_response(response_rows, os.path.join(DATA, "尺度响应曲线.csv"))
    export_bridge(bridge_rows, os.path.join(DATA, "微观宏观桥接.csv"))

    print()
    print("完成。")


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
analyze_micro_bursts.py —— 橡胶期现微观急涨/急跌（3-10天短窗口）非对称性专项研究

════════════════════════════════════════════════════════════════════
研究背景与问题：
  在 30-37 天中长周期内，期货累计涨幅 10-20% 时，原料跟涨 A ≈ 1.004（基本同步）。
  然而，在实际交易与产业链采买中，极易出现微观暴涨暴跌：
    - 3天/5天/7天内，期货剧烈暴涨 10-20%（急涨）
    - 3天/5天/7天内，期货剧烈暴跌 10-20%（急跌）

  由于橡胶加工厂收购报价存在物理调价周期与滞后性，在短至 3~10 天的微观时间窗口内，
  原料价格与期货价格的传导关系 A = R/F、传导缺口 G 是否会出现显著的【滞后衰减】与【方向非对称】？

  本脚本对跨度 L ∈ [3, 5, 7, 10, 14, 21, 35] 自然日/交易日的全量滑动窗口进行精细化微观扫描，
  全面定量评估不同行情持续期下，原料对期货暴涨暴跌的响应敏锐度与时间滞后曲线。
"""

import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TREND_SRC = os.path.join(os.path.dirname(HERE), "趋势判定方案", "src")
if TREND_SRC not in sys.path:
    sys.path.insert(0, TREND_SRC)

from build_dataset import load_futures, load_cities, pair_common, FUT_FILE


def median(xs):
    xs = sorted(xs)
    if not xs: return None
    n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2 - 1] + xs[n//2]) / 2.0


def ols_slope(X, Y):
    n = len(X)
    if n < 4: return None
    mx, my = sum(X)/n, sum(Y)/n
    sxx = sum((x - mx)**2 for x in X)
    sxy = sum((x - mx)*(y - my) for x, y in zip(X, Y))
    return sxy / sxx if sxx > 0 else None


def boot_ci(xs, seed=20260925, n_boot=2000, block=5):
    n = len(xs)
    if n < 8: return None, None
    r = random.Random(seed)
    nb = max(2, (n + block - 1) // block)
    stats = []
    for _ in range(n_boot):
        sample = []
        for _ in range(nb):
            st = r.randrange(0, n)
            for j in range(block):
                sample.append(xs[(st + j) % n])
        stats.append(median(sample))
    stats.sort()
    return stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]


def bucket_f(f):
    af = abs(f)
    if af < 2.0: return "平稳(<2%)"
    if f > 0:
        if f < 5.0: return "涨2-5%"
        if f < 10.0: return "涨5-10%"
        if f < 20.0: return "涨10-20%"
        return "涨>20%"
    else:
        if af < 5.0: return "跌2-5%"
        if af < 10.0: return "跌5-10%"
        if af < 20.0: return "跌10-20%"
        return "跌>20%"


def run_analysis():
    print("=" * 76)
    print("橡胶期现微观急涨/急跌（3-10天短窗口）非对称性专项研究")
    print("=" * 76)

    bars, fdates = load_futures(FUT_FILE, "close")
    cities = load_cities()
    start = min(cities)
    i0 = next(i for i, x in enumerate(fdates) if x >= start)
    bars, fdates = bars[i0:], fdates[i0:]

    print(f"数据范围：{fdates[0]} ~ {fdates[-1]} (共 {len(bars)} 根期货 K 线)")
    print(f"原料报价：{len(cities)} 个日期，覆盖 {len(set(c for v in cities.values() for c in v))} 个海南市县\n")

    # 1. 扫描不同窗口长度 L (交易日)
    window_lengths = [3, 5, 7, 10, 14, 21, 35]
    all_results = {}

    for L in window_lengths:
        wins = []
        n_bars = len(bars)
        for i in range(0, n_bars - L):
            j = i + L
            d0, d1 = fdates[i], fdates[j]
            # 允许 staleness ≤ 3 天，共同市县 ≥ 4
            pr = pair_common(cities, d0, d1, max_stale=4, min_common=4)
            if pr is None: continue
            
            F0, F1 = bars[i].close, bars[j].close
            F = (F1 - F0) / F0 * 100.0
            R = (pr["r1"] - pr["r0"]) / pr["r0"] * 100.0
            A = R / F if abs(F) >= 2.0 else None
            wins.append({
                "i": i, "j": j, "d0": d0, "d1": d1,
                "F": F, "R": R, "A": A, "G": R - F,
                "nCommon": pr["n_common"]
            })
        
        groups = {}
        for w in wins:
            b = bucket_f(w["F"])
            groups.setdefault(b, []).append(w)
        
        all_results[L] = {"wins_list": wins, "groups": groups}

    # 2. 输出微观暴涨 (10-20%) 在不同窗口长度 L 下的比较
    print("【专题 1：大涨 (F ∈ [10%, 20%]) 随窗口长度 L 从 3天 到 35天 的传导变化】")
    print("-" * 84)
    print(f"{'窗口长度':<10} | {'样本量 n':<8} | {'A 中位数':<10} | {'95% CI':<18} | {'A<1 占比':<8} | {'缺口 G(pp)':<10} | {'回归斜率 R~F':<10}")
    print("-" * 84)

    for L in window_lengths:
        grps = all_results[L]["groups"]
        rs = grps.get("涨10-20%", [])
        if not rs:
            print(f"{L} 天窗口    | {'0':<8} | {'-':<10} | {'样本不足':<18} | {'-':<8} | {'-':<10} | {'-':<10}")
            continue
        A_vals = [w["A"] for w in rs if w["A"] is not None]
        G_vals = [w["G"] for w in rs]
        F_vals = [w["F"] for w in rs]
        R_vals = [w["R"] for w in rs]
        
        med_A = median(A_vals)
        ci_lo, ci_hi = boot_ci(A_vals) if len(A_vals) >= 8 else (None, None)
        frac_lt1 = (sum(1 for a in A_vals if a < 1.0) / len(A_vals)) if A_vals else 0
        med_G = median(G_vals)
        slope = ols_slope(F_vals, R_vals)

        med_A_str = f"{med_A:.3f}" if med_A is not None else "-"
        ci_str = f"[{ci_lo:.2f}, {ci_hi:.2f}]" if ci_lo is not None else "样本不足"
        med_G_str = f"{med_G:+.2f}" if med_G is not None else "-"
        slope_str = f"{slope:.3f}" if slope is not None else "-"

        print(f"{L} 天窗口    | {len(rs):<8} | {med_A_str:<10} | {ci_str:<18} | {frac_lt1*100:<7.1f}% | {med_G_str:<10} | {slope_str:<10}")

    print("\n" + "=" * 84)
    # 3. 输出微观暴跌 (跌10-20% / 跌>10%) 在不同窗口长度 L 下的比较
    print("【专题 2：大跌 (F ∈ [-20%, -10%]) 随窗口长度 L 从 3天 到 35天 的传导变化】")
    print("-" * 84)
    print(f"{'窗口长度':<10} | {'样本量 n':<8} | {'A 中位数':<10} | {'95% CI':<18} | {'A>1 占比':<8} | {'缺口 G(pp)':<10} | {'回归斜率 R~F':<10}")
    print("-" * 84)

    for L in window_lengths:
        grps = all_results[L]["groups"]
        rs = grps.get("跌10-20%", []) + grps.get("跌>20%", [])
        if not rs:
            print(f"{L} 天窗口    | {'0':<8} | {'-':<10} | {'样本不足':<18} | {'-':<8} | {'-':<10} | {'-':<10}")
            continue
        A_vals = [w["A"] for w in rs if w["A"] is not None]
        G_vals = [w["G"] for w in rs]
        F_vals = [w["F"] for w in rs]
        R_vals = [w["R"] for w in rs]
        
        med_A = median(A_vals)
        ci_lo, ci_hi = boot_ci(A_vals) if len(A_vals) >= 8 else (None, None)
        frac_gt1 = (sum(1 for a in A_vals if a > 1.0) / len(A_vals)) if A_vals else 0
        med_G = median(G_vals)
        slope = ols_slope(F_vals, R_vals)

        med_A_str = f"{med_A:.3f}" if med_A is not None else "-"
        ci_str = f"[{ci_lo:.2f}, {ci_hi:.2f}]" if ci_lo is not None else "样本不足"
        med_G_str = f"{med_G:+.2f}" if med_G is not None else "-"
        slope_str = f"{slope:.3f}" if slope is not None else "-"

        print(f"{L} 天窗口    | {len(rs):<8} | {med_A_str:<10} | {ci_str:<18} | {frac_gt1*100:<7.1f}% | {med_G_str:<10} | {slope_str:<10}")

    print("\n" + "=" * 84)
    # 4. 详细呈现 5-7 天微观窗口下各涨跌幅档位的完整分布
    for micro_L in [5, 7]:
        print(f"\n【专题 3：{micro_L} 天微观短窗口全档位非对称性扫描 (L={micro_L} 交易日)】")
        print("-" * 84)
        print(f"{'幅度档位':<12} | {'样本量 n':<8} | {'A 中位数':<10} | {'95% CI':<18} | {'A<1 占比':<8} | {'缺口 G(pp)':<10} | {'回归斜率':<10}")
        print("-" * 84)
        
        order = ["涨2-5%", "涨5-10%", "涨10-20%", "涨>20%", "平稳(<2%)", "跌2-5%", "跌5-10%", "跌10-20%", "跌>20%"]
        grps = all_results[micro_L]["groups"]
        
        for b in order:
            rs = grps.get(b, [])
            if not rs: continue
            A_vals = [w["A"] for w in rs if w["A"] is not None]
            G_vals = [w["G"] for w in rs]
            F_vals = [w["F"] for w in rs]
            R_vals = [w["R"] for w in rs]
            
            med_A = median(A_vals) if A_vals else None
            ci_lo, ci_hi = boot_ci(A_vals) if len(A_vals) >= 8 else (None, None)
            frac_lt1 = (sum(1 for a in A_vals if a < 1.0) / len(A_vals)) if A_vals else None
            med_G = median(G_vals) if G_vals else None
            slope = ols_slope(F_vals, R_vals)

            med_A_str = f"{med_A:.3f}" if med_A is not None else "-"
            ci_str = f"[{ci_lo:.2f}, {ci_hi:.2f}]" if ci_lo is not None else "-"
            lt1_str = f"{frac_lt1*100:.1f}%" if frac_lt1 is not None else "-"
            med_G_str = f"{med_G:+.2f}" if med_G is not None else "-"
            slope_str = f"{slope:.3f}" if slope is not None else "-"
            
            print(f"{b:<12} | {len(rs):<8} | {med_A_str:<10} | {ci_str:<18} | {lt1_str:<8} | {med_G_str:<10} | {slope_str:<10}")

    # 5. 典型案例提取
    print("\n" + "=" * 84)
    print("【专题 4：7天微观急涨/急跌 10-20% 典型行情案例提取】")
    print("-" * 84)
    
    micro7 = all_results[7]["groups"]
    rapid_up = [w for w in micro7.get("涨10-20%", []) + micro7.get("涨>20%", [])]
    rapid_down = [w for w in micro7.get("跌10-20%", []) + micro7.get("跌>20%", [])]

    rapid_up.sort(key=lambda x: x["F"], reverse=True)
    rapid_down.sort(key=lambda x: x["F"])

    print("▶ 7天微观急涨 TOP 案例：")
    for c in rapid_up[:6]:
        a_str = f"{c['A']:.3f}" if c['A'] is not None else "-"
        print(f"  * {c['d0']} ~ {c['d1']} (7天): 期货F={c['F']:+.2f}%, 原料R={c['R']:+.2f}%, A={a_str}, G={c['G']:+.2f}pp (共同市县={c['nCommon']})")

    print("\n▶ 7天微观急跌 TOP 案例：")
    for c in rapid_down[:6]:
        a_str = f"{c['A']:.3f}" if c['A'] is not None else "-"
        print(f"  * {c['d0']} ~ {c['d1']} (7天): 期货F={c['F']:+.2f}%, 原料R={c['R']:+.2f}%, A={a_str}, G={c['G']:+.2f}pp (共同市县={c['nCommon']})")

    print("=" * 84)


if __name__ == "__main__":
    run_analysis()

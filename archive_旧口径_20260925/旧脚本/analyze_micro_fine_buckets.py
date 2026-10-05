# -*- coding: utf-8 -*-
"""
analyze_micro_fine_buckets.py —— 3天/5天/7天微观窗口下，10%、15%、20% 细分档位期现传导精细化统计
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
    if n < 3: return None
    mx, my = sum(X)/n, sum(Y)/n
    sxx = sum((x - mx)**2 for x in X)
    sxy = sum((x - mx)*(y - my) for x, y in zip(X, Y))
    return sxy / sxx if sxx > 0 else None


def fine_bucket(f):
    if f > 0:
        if 8.0 <= f < 12.0: return "涨10%档(8-12%)"
        if 12.0 <= f < 18.0: return "涨15%档(12-18%)"
        if f >= 18.0: return "涨20%档(≥18%)"
        if 2.0 <= f < 8.0: return "涨2-8%温和"
    else:
        af = abs(f)
        if 8.0 <= af < 12.0: return "跌10%档(8-12%)"
        if 12.0 <= af < 18.0: return "跌15%档(12-18%)"
        if af >= 18.0: return "跌20%档(≥18%)"
        if 2.0 <= af < 8.0: return "跌2-8%温和"
    return "平稳(<2%)"


def run():
    bars, fdates = load_futures(FUT_FILE, "close")
    cities = load_cities()
    start = min(cities)
    i0 = next(i for i, x in enumerate(fdates) if x >= start)
    bars, fdates = bars[i0:], fdates[i0:]

    windows = [3, 5, 7, 15]
    results = {}

    for L in windows:
        wins = []
        n_bars = len(bars)
        for i in range(0, n_bars - L):
            j = i + L
            d0, d1 = fdates[i], fdates[j]
            pr = pair_common(cities, d0, d1, max_stale=4, min_common=4)
            if pr is None: continue
            
            F0, F1 = bars[i].close, bars[j].close
            F = (F1 - F0) / F0 * 100.0
            R = (pr["r1"] - pr["r0"]) / pr["r0"] * 100.0
            A = R / F if abs(F) >= 2.0 else None
            b = fine_bucket(F)
            wins.append({
                "i": i, "j": j, "d0": d0, "d1": d1,
                "F": F, "R": R, "A": A, "G": R - F,
                "bucket": b, "nCommon": pr["n_common"],
                "r0": pr["r0"], "r1": pr["r1"], "f0": F0, "f1": F1
            })
        
        groups = {}
        for w in wins:
            groups.setdefault(w["bucket"], []).append(w)
        results[L] = {"all": wins, "groups": groups}

    # 打印精细表
    fine_order_up = ["涨10%档(8-12%)", "涨15%档(12-18%)", "涨20%档(≥18%)"]
    fine_order_down = ["跌10%档(8-12%)", "跌15%档(12-18%)", "跌20%档(≥18%)"]

    print("=" * 88)
    print("3天 / 5天 / 7天 微观窗口下 10% / 15% / 20% 细分档位期现传导统计")
    print("=" * 88)

    for L in windows:
        print(f"\n【{L} 天微观窗口分析 (L = {L} 交易日)】")
        print("-" * 88)
        print(f"{'细分档位':<16} | {'样本量 n':<8} | {'期货F中位%':<10} | {'原料R中位%':<10} | {'A 中位数':<10} | {'缺口G中位':<10} | {'斜率 R~F':<8}")
        print("-" * 88)

        for b in fine_order_up + fine_order_down:
            rs = results[L]["groups"].get(b, [])
            if not rs:
                print(f"{b:<16} | {'0':<8} | {'-':<10} | {'-':<10} | {'-':<10} | {'-':<10} | {'-':<8}")
                continue
            
            F_vals = [w["F"] for w in rs]
            R_vals = [w["R"] for w in rs]
            A_vals = [w["A"] for w in rs if w["A"] is not None]
            G_vals = [w["G"] for w in rs]

            med_F = median(F_vals)
            med_R = median(R_vals)
            med_A = median(A_vals) if A_vals else None
            med_G = median(G_vals)
            slope = ols_slope(F_vals, R_vals)

            med_F_str = f"{med_F:+.2f}%" if med_F is not None else "-"
            med_R_str = f"{med_R:+.2f}%" if med_R is not None else "-"
            med_A_str = f"{med_A:.3f}" if med_A is not None else "-"
            med_G_str = f"{med_G:+.2f}pp" if med_G is not None else "-"
            slope_str = f"{slope:.3f}" if slope is not None else "-"

            print(f"{b:<16} | {len(rs):<8} | {med_F_str:<10} | {med_R_str:<10} | {med_A_str:<10} | {med_G_str:<10} | {slope_str:<8}")

        print("-" * 88)

        # 打印部分代表案例
        print(f"▶ {L} 天窗口大涨/大跌代表案例 (F ≥ 8% 或 F ≤ -8%)：")
        big_cases = [w for w in results[L]["all"] if abs(w["F"]) >= 8.0]
        big_cases.sort(key=lambda x: abs(x["F"]), reverse=True)
        for c in big_cases[:6]:
            a_str = f"{c['A']:.3f}" if c['A'] is not None else "-"
            print(f"  * [{c['bucket']}] {c['d0']} ~ {c['d1']}: 期货F={c['F']:+.2f}%, 原料R={c['R']:+.2f}%, A={a_str}, G={c['G']:+.2f}pp")

    return results


if __name__ == "__main__":
    run()

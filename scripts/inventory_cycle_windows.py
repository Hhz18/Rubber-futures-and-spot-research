# -*- coding: utf-8 -*-
"""
inventory_cycle_windows.py —— 30-37 天库存周期窗口：期货与原料价差利润分析

════════════════════════════════════════════════════════════════════
核心问题
════════════════════════════════════════════════════════════════════
在一个库存周期（30-37 个交易日，约 1.5 自然月）内：
  1. 期货涨幅 F% 与原料涨幅 R% 的关系如何？
  2. 期现价差 S = 期货(元/吨) - 原料(元/公斤)×1000 如何变化？
  3. 最理想 / 一般 / 最差利润差分别是多少？

════════════════════════════════════════════════════════════════════
两条期货序列各司其职
════════════════════════════════════════════════════════════════════
  未复权连续（期货_连续_未复权.csv）→ 价差计算（真实元/吨）
  后复权连续（期货_连续_后复权.csv）→ 趋势判定（消除换月跳空）

════════════════════════════════════════════════════════════════════
产物
════════════════════════════════════════════════════════════════════
  控制台：5 张表（概览 / 全样本利润 / 按趋势 / 趋势×幅度 / 非重叠）
  data/库存周期窗口样本.csv    逐窗口明细

用法：
    python inventory_cycle_windows.py
    python inventory_cycle_windows.py --max-stale 7 --min-window 30 --max-window 37
"""

import argparse
import bisect
import collections
import csv
import os
import statistics
import sys
from datetime import date as _date, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# ── 趋势判定方案 ──────────────────────────────────────────────
TREND_SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "趋势判定方案", "src")
if TREND_SRC not in sys.path:
    sys.path.insert(0, TREND_SRC)

from trend_analysis import (  # noqa: E402
    MinuteBar, overall_trend, preset_params, judge_args,
)

# ── 数据文件 ──────────────────────────────────────────────────
FUT_FILE = os.path.join(DATA, "期货_连续_后复权.csv")    # 后复权 → 趋势判定
LVL_FILE = os.path.join(DATA, "期货_连续_未复权.csv")    # 未复权 → 价差计算
CLEAN_FILE = os.path.join(DATA, "收购价_全省_清洗版.csv")  # LOCF 全省均价
OUT_FILE = os.path.join(DATA, "库存周期窗口样本.csv")

# ── 幅度分档（复用 build_dataset.py）──────────────────────────
BUCKETS = [(0.0, 2.0, "0-2%"), (2.0, 10.0, "2-10%"),
           (10.0, 20.0, "10-20%"), (20.0, 1e9, ">20%")]


# ====================================================================
# 数据加载
# ====================================================================

def d(s):
    """str -> date。"""
    return _date.fromisoformat(s[:10])


def load_clean_prov():
    """LOCF 清洗版全省均价 -> {日期: 元/公斤}。"""
    out = {}
    with open(CLEAN_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = r["全省均价_可比"].strip()
            if v:
                out[r["日期"]] = float(v)
    return out


def load_futures_raw():
    """未复权期货收盘价 -> {日期: 元/吨}。"""
    out = {}
    with open(LVL_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out[r["日期"]] = float(r["收"])
    return out


def load_futures_adj():
    """后复权期货 -> (List[MinuteBar], List[str])。"""
    bars, dates = [], []
    with open(FUT_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            o, h, l, c = (float(r["开"]), float(r["高"]),
                          float(r["低"]), float(r["收"]))
            bars.append(MinuteBar(time=r["日期"], open=o, high=h,
                                  low=l, close=c))
            dates.append(r["日期"])
    return bars, dates


# ====================================================================
# LOCF 最近观测查找
# ====================================================================

class LOCFSeries:
    """LOCF 价格序列，支持"取 target 日或之前最近观测"。"""

    def __init__(self, prices):
        self.prices = prices          # {date_str: float}
        self.dates = sorted(prices)

    def pick(self, target, max_stale):
        """返回 (观测日, 价格, 滞后天数) 或 None。"""
        i = bisect.bisect_right(self.dates, target[:10])
        if i == 0:
            return None
        dt = self.dates[i - 1]
        stale = (d(target) - d(dt)).days
        if stale > max_stale:
            return None
        return dt, self.prices[dt], stale


# ====================================================================
# 窗口枚举与计算
# ====================================================================

def bucket_of(f_pct):
    """绝对期货涨跌幅 -> 幅度档名称。"""
    a = abs(f_pct)
    for lo, hi, name in BUCKETS:
        if lo <= a < hi:
            return name
    return ">20%"


def ols_slope(X, Y):
    """最小二乘斜率（无截距回归的斜率 b）。"""
    n = len(X)
    if n < 3:
        return None
    mx = sum(X) / n
    my = sum(Y) / n
    sxx = sum((x - mx) ** 2 for x in X)
    sxy = sum((x - mx) * (y - my) for x, y in zip(X, Y))
    return sxy / sxx if sxx > 0 else None


def enumerate_windows(fdates, locf, fut_raw, min_cal, max_cal, max_stale):
    """枚举所有自然日长度在 [min_cal, max_cal] 内的滑动窗口。

    对每个期货交易日起点，用 bisect 找到自然日落在 [min_cal, max_cal]
    区间内的终点期货交易日。若区间内有多个交易日，取最接近 min_cal 的。

    返回 list of dict，每个含：
      idx0, idx1, d0, d1, cal_days, trade_days,
      obs_d0, obs_d1, price_0, price_1, stale_0, stale_1,
      f_price_0, f_price_1
    """
    out = []
    # 预计算每个期货交易日对应的 date 对象
    fdate_objs = [d(dt) for dt in fdates]

    for i in range(len(fdates)):
        d0 = fdates[i]
        d0_obj = fdate_objs[i]

        # 目标终点：d0 + min_cal ~ d0 + max_cal 天
        target_lo = d0_obj + timedelta(days=min_cal)
        target_hi = d0_obj + timedelta(days=max_cal)

        # 在 fdate_objs 中找落在 [target_lo, target_hi] 的交易日
        lo_idx = bisect.bisect_left(fdate_objs, target_lo)
        hi_idx = bisect.bisect_right(fdate_objs, target_hi)

        if lo_idx >= len(fdates) or hi_idx <= lo_idx:
            continue

        # 取最接近 target_lo 的（即自然日最短的有效终点）
        j = lo_idx
        d1 = fdates[j]
        cal = (fdate_objs[j] - d0_obj).days
        trade_days = j - i

        # 原料端点取价
        p0 = locf.pick(d0, max_stale)
        p1 = locf.pick(d1, max_stale)
        if p0 is None or p1 is None:
            continue
        if p0[0] == p1[0]:      # 同一观测 → R 恒为 0，假样本
            continue

        # 期货端点取价（未复权）
        f0 = fut_raw.get(d0)
        f1 = fut_raw.get(d1)
        if f0 is None or f1 is None or f0 <= 0:
            continue

        out.append({
            "idx0": i, "idx1": j, "d0": d0, "d1": d1,
            "cal_days": cal, "trade_days": trade_days,
            "obs_d0": p0[0], "obs_d1": p1[0],
            "price_0": p0[1], "price_1": p1[1],
            "stale_0": p0[2], "stale_1": p1[2],
            "f_price_0": f0, "f_price_1": f1,
        })
    return out


def compute_metrics(w):
    """为单个窗口计算 F%、R%、S0、S1、dS、G。"""
    F = (w["f_price_1"] - w["f_price_0"]) / w["f_price_0"] * 100
    R = (w["price_1"] - w["price_0"]) / w["price_0"] * 100
    S0 = w["f_price_0"] - w["price_0"] * 1000
    S1 = w["f_price_1"] - w["price_1"] * 1000
    dS = S1 - S0
    G = R - F
    w["F_pct"] = round(F, 4)
    w["R_pct"] = round(R, 4)
    w["S0"] = round(S0, 1)
    w["S1"] = round(S1, 1)
    w["dS"] = round(dS, 1)
    w["G_pp"] = round(G, 4)
    w["bucket"] = bucket_of(F)


def classify_trend(bars_adj, idx1, trade_days, trend_params):
    """用后复权序列判定趋势方向。

    trade_days 是窗口内的实际交易日数（由自然日窗口决定，非固定值）。
    overall_trend() 内部取末尾 trade_days 根判定，MA 预热在完整序列上完成。
    """
    sub = bars_adj[:idx1 + 1]
    if len(sub) < trade_days:
        return "range"
    return overall_trend(
        sub,
        max_drawdown_limit=trend_params["max_drawdown_limit"],
        max_runup_limit=trend_params["max_runup_limit"],
        min_rise=trend_params["min_rise"],
        min_fall=trend_params["min_fall"],
        window=trade_days,
        continuity_ratio=trend_params["continuity_ratio"],
        rebound_ratio=trend_params["rebound_ratio"],
        recover_bars=trend_params.get("term_recover_bars"),
        ma_short=trend_params["ma_short"],
        ma_long=trend_params["ma_long"],
    )


# ====================================================================
# 分组与统计
# ====================================================================

def group_key(w):
    return (w.get("trend_dir", "all"), w.get("bucket", "all"))


def select_non_overlapping(windows):
    """贪心选取不重叠窗口子集。"""
    out = []
    last_end = -1
    for w in windows:
        if w["idx0"] >= last_end:
            out.append(w)
            last_end = w["idx1"]
    return out


def percentile(xs, p):
    """p 分位数（0-100）。"""
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def profit_stats(dS_list):
    """dS 列表 -> 利润统计 dict。"""
    if not dS_list:
        return None
    return {
        "n": len(dS_list),
        "best": max(dS_list),
        "avg": sum(dS_list) / len(dS_list),
        "worst": min(dS_list),
        "P10": percentile(dS_list, 10),
        "P25": percentile(dS_list, 25),
        "P50": percentile(dS_list, 50),
        "P75": percentile(dS_list, 75),
        "P90": percentile(dS_list, 90),
        "std": statistics.stdev(dS_list) if len(dS_list) > 1 else 0,
        "pos_share": sum(1 for x in dS_list if x > 0) / len(dS_list) * 100,
    }


# ====================================================================
# 输出
# ====================================================================

def print_overview(all_wins, fut_raw, locf):
    print("=" * 96)
    print("30-37 自然日库存周期窗口：期货与原料价差利润分析")
    print("=" * 96)
    fdates = sorted(fut_raw)
    print(f"  期货未复权: {len(fut_raw)} 个交易日  {fdates[0]} ~ {fdates[-1]}")
    print(f"  原料 LOCF:  {len(locf)} 个观测  {min(locf)} ~ {max(locf)}")
    print(f"  总窗口数:   {len(all_wins)}")
    if all_wins:
        # 按自然日长度分布
        by_cal = collections.Counter(w["cal_days"] for w in all_wins)
        print(f"  自然日分布: ", end="")
        for cal in sorted(by_cal):
            print(f"{cal}天:{by_cal[cal]}  ", end="")
        print()
        # 按交易日长度分布
        by_trade = collections.Counter(w["trade_days"] for w in all_wins)
        print(f"  交易日分布: ", end="")
        for t in sorted(by_trade):
            print(f"{t}日:{by_trade[t]}  ", end="")
        print()
    by_trend = collections.Counter(w.get("trend_dir", "?") for w in all_wins)
    print(f"  趋势分布: {dict(by_trend)}")


def print_profit_table(title, windows):
    """打印一组窗口的利润统计表。"""
    print()
    print("=" * 96)
    print(title)
    print("=" * 96)

    dS_all = [w["dS"] for w in windows]
    st = profit_stats(dS_all)
    if st is None:
        print("  无有效窗口")
        return

    print(f"\n  全样本 (n={st['n']}):")
    print(f"    最理想利润差  {st['best']:>+10.1f} 元/吨")
    print(f"    一般利润差    {st['avg']:>+10.1f} 元/吨")
    print(f"    最差利润差    {st['worst']:>+10.1f} 元/吨")
    print(f"    标准差        {st['std']:>10.1f} 元/吨")
    print(f"    正利润占比    {st['pos_share']:>10.1f}%")
    print(f"    分位数: P10={st['P10']:+.0f}  P25={st['P25']:+.0f}  "
          f"P50={st['P50']:+.0f}  P75={st['P75']:+.0f}  P90={st['P90']:+.0f}")

    # 按趋势方向
    print(f"\n  {'趋势':<10}{'n':>6}{'F中位%':>10}{'R中位%':>10}"
          f"{'dS均值':>10}{'dS中位':>10}{'dS最优':>10}{'dS最差':>10}{'正利润%':>8}")
    print("  " + "-" * 84)
    for trend in ["up", "down", "range"]:
        ws = [w for w in windows if w.get("trend_dir") == trend]
        if not ws:
            continue
        F_med = statistics.median([w["F_pct"] for w in ws])
        R_med = statistics.median([w["R_pct"] for w in ws])
        ds = [w["dS"] for w in ws]
        st2 = profit_stats(ds)
        print(f"  {trend:<10}{len(ws):>6}{F_med:>+10.2f}{R_med:>+10.2f}"
              f"{st2['avg']:>+10.0f}{st2['P50']:>+10.0f}"
              f"{st2['best']:>+10.0f}{st2['worst']:>+10.0f}"
              f"{st2['pos_share']:>8.1f}")

    # 趋势×幅度交叉表
    print(f"\n  趋势×幅度 交叉表（dS 均值 元/吨）")
    print(f"  {'':>16}", end="")
    bucket_names = ["0-2%", "2-10%", "10-20%", ">20%"]
    for b in bucket_names:
        print(f"{b:>12}", end="")
    print(f"{'合计':>10}")
    print("  " + "-" * 70)
    for trend in ["up", "down", "range"]:
        print(f"  {trend:>14}", end="")
        row_all = []
        for b in bucket_names:
            ws = [w for w in windows
                  if w.get("trend_dir") == trend and w.get("bucket") == b]
            if ws:
                avg_ds = statistics.mean([w["dS"] for w in ws])
                print(f"{avg_ds:>+10.0f}({len(ws):>1d})", end="")
                row_all.extend(ws)
            else:
                print(f"{'—':>12}", end="")
        if row_all:
            avg_all = statistics.mean([w["dS"] for w in row_all])
            print(f"{avg_all:>+8.0f}({len(row_all):>1d})")
        else:
            print(f"{'—':>10}")
    print("  " + "-" * 70)
    # 合计行
    print(f"  {'全部':>14}", end="")
    for b in bucket_names:
        ws = [w for w in windows if w.get("bucket") == b]
        if ws:
            avg_ds = statistics.mean([w["dS"] for w in ws])
            print(f"{avg_ds:>+10.0f}({len(ws):>1d})", end="")
        else:
            print(f"{'—':>12}", end="")
    all_ds = [w["dS"] for w in windows]
    if all_ds:
        print(f"{statistics.mean(all_ds):>+8.0f}({len(all_ds):>1d})")


def export_csv(windows, out_path):
    """写出逐窗口明细 CSV。"""
    fields = ["自然日数", "交易日数", "起", "止",
              "期货起点价_元吨", "期货终点价_元吨", "期货涨跌F_pct",
              "原料观测起点日", "原料观测终点日",
              "原料起点价_元kg", "原料终点价_元kg", "原料涨跌R_pct",
              "起点价差_元吨", "终点价差_元吨", "价差变化dS_元吨",
              "传导缺口G_pp", "趋势方向", "幅度档",
              "端点滞后_起", "端点滞后_止"]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for x in windows:
            w.writerow([
                x["cal_days"], x["trade_days"], x["d0"], x["d1"],
                x["f_price_0"], x["f_price_1"], x["F_pct"],
                x["obs_d0"], x["obs_d1"],
                x["price_0"], x["price_1"], x["R_pct"],
                x["S0"], x["S1"], x["dS"],
                x["G_pp"], x.get("trend_dir", ""), x.get("bucket", ""),
                x["stale_0"], x["stale_1"],
            ])
    print(f"\n已写出: {out_path}  （{len(windows)} 条）")


# ====================================================================
# 主流程
# ====================================================================

def main():
    ap = argparse.ArgumentParser(description="30-37 自然日库存周期窗口价差利润分析")
    ap.add_argument("--max-stale", type=int, default=7,
                    help="端点原料取价最大滞后天数，默认 7")
    ap.add_argument("--min-cal", type=int, default=30,
                    help="窗口最短自然日，默认 30")
    ap.add_argument("--max-cal", type=int, default=37,
                    help="窗口最长自然日，默认 37")
    args = ap.parse_args()

    # ── 1. 加载数据 ──────────────────────────────────────────
    print("加载数据 …")
    locf_prices = load_clean_prov()
    fut_raw = load_futures_raw()
    bars_adj, fdates = load_futures_adj()
    locf = LOCFSeries(locf_prices)

    # 对齐起始日（原料数据可用起点）
    start_date = min(locf_prices)
    i0 = next(i for i, x in enumerate(fdates) if x >= start_date)
    bars_adj = bars_adj[i0:]
    fdates = fdates[i0:]

    # 趋势判定参数
    tp = preset_params("30天趋势", "day")
    trend_params = judge_args(tp)

    print_overview([], fut_raw, locf_prices)

    # ── 2. 枚举并计算所有窗口（自然日窗口）──────────────────
    print(f"  枚举 {args.min_cal}-{args.max_cal} 自然日窗口 …", end=" ", flush=True)
    all_windows = enumerate_windows(fdates, locf, fut_raw,
                                    args.min_cal, args.max_cal, args.max_stale)
    print(f"{len(all_windows)} 个窗口")

    # 计算指标
    for w in all_windows:
        compute_metrics(w)

    # 趋势分类（用实际交易日数调用 overall_trend）
    for w in all_windows:
        w["trend_dir"] = classify_trend(bars_adj, w["idx1"],
                                        w["trade_days"], trend_params)

    all_windows.sort(key=lambda w: w["d0"])

    # ── 3. 控制台输出 ────────────────────────────────────────
    print_overview(all_windows, fut_raw, locf_prices)
    print_profit_table(
        "全样本利润分布（全部滑动窗口，含重叠）",
        all_windows)

    # 非重叠子样本
    non_ovlp = select_non_overlapping(all_windows)
    print_profit_table(
        f"非重叠样本利润分布（n={len(non_ovlp)}，窗口互不重叠）",
        non_ovlp)

    # 警告
    print()
    print("=" * 96)
    print("⚠ 注意事项")
    print("=" * 96)
    print("  1. 滑动窗口高度重叠，全样本 n 不代表独立样本量。")
    print(f"     非重叠样本 n={len(non_ovlp)} 才是独立估计。")
    print("  2. 原料观测频率 ≈ 3 个交易日一次，端点取价有滞后。")
    print(f"     允许最大滞后: {args.max_stale} 天。")
    print("  3. 每年 1-3 月停割期（84-123 天无数据），期间篮子沿用旧价。")
    print("  4. 2024-10-07 起折算口径变更（干胶→胶水/胶块/胶粒分别折算）。")
    print("  5. 价差 S = 未复权期货收盘 - LOCF全省均价×1000，是加工利润的近似。")

    # ── 4. 写出 CSV ──────────────────────────────────────────
    export_csv(all_windows, OUT_FILE)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
micro_windows_provincial.py —— 3/5/7/15 天微观窗口期现传导：全省均价口径

════════════════════════════════════════════════════════════════════
三种原料口径并列对比（这是本脚本存在的唯一理由）
════════════════════════════════════════════════════════════════════
  A. 官方发布全省均价（原始列，含构成污染）—— 不推荐，仅作对照
  B. LOCF 固定篮子全省均价（清洗版）        —— 推荐，单列全省价
  C. 两端共同市县均值（build_dataset.py）   —— 既有交付数字用的口径

A 的问题：官方"全省均价"= 当期有报价市县的简单均值，报价市县数在 1~18 浮动，
逐期构成效应中位 0.31pp、最大 18.53pp。用它算窗口涨幅，R 里混着"这次
恰好报了哪几个县"，而不是"原料涨了多少"。

B 的做法：把篮子固定成全部 18 个市县，未报价的沿用它最近一次报价，
再取 18 个值的算术平均。在 18 市县全覆盖日与官方值偏差仅 0.0026 元/公斤，
所以水平仍是真实的 元/公斤，但任何两个时点都可比。

C 的做法：两端都报价的市县才用，两端同一批县比。可比性最强，但两端
共同市县数不足时窗口会被丢弃（本数据集相邻期中位 16、最小 0）。

════════════════════════════════════════════════════════════════════
窗口端点取价
════════════════════════════════════════════════════════════════════
窗口端点落在期货交易日上（L 用交易日数）。原料观测不是每天都有（约 3 个
交易日一发），所以端点取"该交易日或之前最近一次观测"，并记录滞后天数。
滞后超过 MAX_STALE 的窗口丢弃（否则停割季的端点会取到几十天前的旧价）。

B 口径下另有 `距上次报价天数_最大`（篮子内最新鲜与最陈旧报价的天数差），
停割季会很大 —— 这类窗口单独标记，因为篮子里有一部分是旧价。

════════════════════════════════════════════════════════════════════
产物
════════════════════════════════════════════════════════════════════
    控制台：三口径 × 4 个窗口 × 6 个幅度档的对比表 + 样本时间集中度
    控制台：停割季/口径变更敏感窗口清单
    data/微观窗口样本_全省口径.csv   逐窗口明细（B 口径）

用法：
    python micro_windows_provincial.py
    python micro_windows_provincial.py --max-stale 6
"""

import argparse
import bisect
import collections
import csv
import os
import statistics
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TREND_SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "趋势判定方案", "src")
if TREND_SRC not in sys.path:
    sys.path.insert(0, TREND_SRC)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# load_futures / pair_common 复用既有实现；原料数据一律来自 raw/ 原文，
# 不再经过 城市矩阵 / 分市县表 等中间 CSV（见 rubber_source.py）。
from build_dataset import load_futures, pair_common, FUT_FILE  # noqa: E402
from rubber_source import county_panel, provincial  # noqa: E402

CLEAN_FILE = os.path.join(DATA, "收购价_全省_清洗版.csv")
OUT_FILE = os.path.join(DATA, "微观窗口样本_全省口径.csv")
OUT_EVENT_FILE = os.path.join(DATA, "微观窗口事件_全省口径.csv")

WINDOWS = [3, 5, 7, 15]
BUCKET_ORDER = ["涨10%档(8-12%)", "涨15%档(12-18%)", "涨20%档(≥18%)",
                "跌10%档(8-12%)", "跌15%档(12-18%)", "跌20%档(≥18%)"]


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


def ols_slope(X, Y):
    n = len(X)
    if n < 3:
        return None
    mx, my = sum(X) / n, sum(Y) / n
    sxx = sum((x - mx) ** 2 for x in X)
    sxy = sum((x - mx) * (y - my) for x, y in zip(X, Y))
    return sxy / sxx if sxx > 0 else None


def fine_bucket(f):
    a = abs(f)
    if a < 8.0:
        return None
    if a >= 18.0:
        d = "涨20%档(≥18%)" if f > 0 else "跌20%档(≥18%)"
    elif a >= 12.0:
        d = "涨15%档(12-18%)" if f > 0 else "跌15%档(12-18%)"
    else:
        d = "涨10%档(8-12%)" if f > 0 else "跌10%档(8-12%)"
    return d


def load_clean_prov():
    """清洗版全省均价 -> ({日期: 可比均价}, {日期: 滞后天数}, {日期: 折算口径})."""
    px, lag, basis = {}, {}, {}
    with open(CLEAN_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = fnum(r["全省均价_可比"])
            if v is not None:
                px[r["日期"]] = v
                lag[r["日期"]] = int(r["距上次报价天数_最大"])
                basis[r["日期"]] = r["折算口径"]
    return px, lag, basis


def load_raw_prov():
    """官方发布全省均价 -> {日期: 均价}（去重，仅日度）。"""
    return {dt: v["均价"] for dt, v in provincial().items()}


class Series:
    """一条按观测日定义的原料序列，支持"取 target 日或之前最近一次观测"。"""

    def __init__(self, by_date, lags=None, basis=None):
        self.by_date = by_date
        self.dates = sorted(by_date)
        self.lags = lags or {}
        self.basis = basis or {}

    def pick(self, target, max_stale):
        i = bisect.bisect_right(self.dates, target[:10])
        if i == 0:
            return None
        dt = self.dates[i - 1]
        stale = (self._d(target) - self._d(dt)).days
        if stale > max_stale:
            return None
        return dt, self.by_date[dt], stale, self.lags.get(dt), self.basis.get(dt)

    @staticmethod
    def _d(s):
        from datetime import date
        return date.fromisoformat(s[:10])


def run_windows(bars, fdates, series, max_stale, use_common=None):
    """对每个窗口算 F / R / A / G。series=None 时走 use_common（共同市县法）。"""
    out = {}
    for L in WINDOWS:
        wins = []
        for i in range(len(bars) - L):
            j = i + L
            d0, d1 = fdates[i], fdates[j]
            F = (bars[j].close - bars[i].close) / bars[i].close * 100
            b = fine_bucket(F)
            if b is None:
                continue
            if series is not None:
                p0 = series.pick(d0, max_stale)
                p1 = series.pick(d1, max_stale)
                if p0 is None or p1 is None or p0[0] == p1[0]:
                    continue
                R = (p1[1] - p0[1]) / p0[1] * 100
                wins.append({"L": L, "d0": d0, "d1": d1, "F": F, "R": R,
                             "bucket": b, "lag0": p0[2], "lag1": p1[2],
                             "basket_lag": max(p0[3] or 0, p1[3] or 0),
                             "basis_mix": p0[4] != p1[4]})
            else:
                pr = use_common(d0, d1)
                if pr is None:
                    continue
                R = (pr["r1"] - pr["r0"]) / pr["r0"] * 100
                wins.append({"L": L, "d0": d0, "d1": d1, "F": F, "R": R,
                             "bucket": b, "lag0": pr["stale0"], "lag1": pr["stale1"],
                             "n_common": pr["n_common"], "basket_lag": 0,
                             "basis_mix": False})
        for w in wins:
            w["A"] = w["R"] / w["F"] if abs(w["F"]) >= 2.0 else None
            w["G"] = w["R"] - w["F"]
        out[L] = wins
    return out


def group(wins):
    g = collections.defaultdict(list)
    for w in wins:
        g[w["bucket"]].append(w)
    return g


def merge_events(ws):
    """把时间上重叠的窗口并成独立事件。ws 已按 d0 排序。

    滑动窗口逐日推进，同一个行情片段会生成几十个窗口；引用"样本量 n"
    时会严重高估证据量，故所有结论表都应按事件数复核。
    """
    eps, cur = [], [ws[0]]
    for w in ws[1:]:
        if w["d0"] <= cur[-1]["d1"]:
            cur.append(w)
        else:
            eps.append(cur)
            cur = [w]
    eps.append(cur)
    return eps


def print_table(results, title):
    print()
    print("=" * 96)
    print(title)
    print("=" * 96)
    for L in WINDOWS:
        print(f"\n【{L} 天窗口】")
        print("-" * 96)
        print(f"{'细分档位':<16}{'n':>4}{'F中位%':>10}{'R中位%':>10}"
              f"{'A中位':>9}{'G中位':>10}{'斜率R~F':>10}  时间分布")
        print("-" * 96)
        g = group(results[L])
        for b in BUCKET_ORDER:
            rs = g.get(b)
            if not rs:
                continue
            F = [w["F"] for w in rs]
            R = [w["R"] for w in rs]
            A = [w["A"] for w in rs if w["A"] is not None]
            G = [w["G"] for w in rs]
            sl = ols_slope(F, R)
            yrs = collections.Counter(w["d0"][:4] for w in rs)
            ys = " ".join(f"{y}:{n}" for y, n in sorted(yrs.items()))
            print(f"{b:<16}{len(rs):>4}{median(F):>+10.2f}{median(R):>+10.2f}"
                  f"{(median(A) if A else float('nan')):>9.3f}{median(G):>+10.2f}"
                  f"{(sl if sl is not None else float('nan')):>10.3f}  {ys}")
        print("-" * 96)


def main():
    ap = argparse.ArgumentParser(description="全省均价口径下的微观窗口分析")
    ap.add_argument("--max-stale", type=int, default=4,
                    help="端点取价允许的最大滞后天数，默认 4")
    args = ap.parse_args()

    bars, fdates = load_futures(FUT_FILE, "close")
    cities = county_panel()
    start = min(cities)
    i0 = next(i for i, x in enumerate(fdates) if x >= start)
    bars, fdates = bars[i0:], fdates[i0:]

    clean_px, clean_lag, clean_basis = load_clean_prov()
    raw_px = load_raw_prov()

    print("=" * 96)
    print("3 / 5 / 7 / 15 天微观窗口期现传导 —— 三种原料口径并列")
    print("=" * 96)
    print(f"期货: {os.path.basename(FUT_FILE)}  {len(bars)} 根  "
          f"{fdates[0]} ~ {fdates[-1]}")
    print(f"原料观测期数: 清洗版 {len(clean_px)}  官方版 {len(raw_px)}")
    print(f"端点最大滞后: {args.max_stale} 天")

    res_official = run_windows(bars, fdates, Series(raw_px), args.max_stale)
    res_clean = run_windows(bars, fdates, Series(clean_px, clean_lag, clean_basis),
                            args.max_stale)
    res_common = run_windows(bars, fdates, None, args.max_stale,
                             use_common=lambda a, b: pair_common(
                                 cities, a, b, max_stale=args.max_stale, min_common=4))

    print_table(res_official, "口径 A：官方发布全省均价（含构成污染，仅作对照）")
    print_table(res_clean, "口径 B：LOCF 固定篮子全省均价（清洗版，推荐）")
    print_table(res_common, "口径 C：两端共同市县均值（既有交付数字用的口径）")

    # ---------- 三口径读数对照 ----------
    print()
    print("=" * 96)
    print("三口径读数对照（R 中位%，pp = 相对 B 的差）")
    print("=" * 96)
    print(f"{'窗口':<6}{'档位':<18}{'A 官方':>12}{'B 清洗全省':>13}"
          f"{'C 共同市县':>13}{'A-B':>10}{'C-B':>10}")
    print("-" * 96)
    for L in WINDOWS:
        go, gc, gm = group(res_official[L]), group(res_clean[L]), group(res_common[L])
        for b in BUCKET_ORDER:
            if not gc.get(b):
                continue
            ro = median([w["R"] for w in go.get(b, [])]) if go.get(b) else None
            rc = median([w["R"] for w in gc[b]])
            rm = median([w["R"] for w in gm.get(b, [])]) if gm.get(b) else None
            print(f"{L:<6}{b:<18}"
                  f"{(f'{ro:+.2f}' if ro is not None else '-'):>12}"
                  f"{rc:>+13.2f}"
                  f"{(f'{rm:+.2f}' if rm is not None else '-'):>13}"
                  f"{(f'{ro-rc:+.2f}' if ro is not None else '-'):>10}"
                  f"{(f'{rm-rc:+.2f}' if rm is not None else '-'):>10}")
        print("-" * 96)

    # ---------- 独立事件数：滑动窗口高度重叠，"样本量 n" 会严重高估证据量 ----------
    print()
    print("=" * 96)
    print("独立事件数（相邻窗口在时间上重叠则归为同一事件）—— 引用 n 之前必读")
    print("=" * 96)
    print("  滑动窗口是逐日推进的，同一个行情片段会生成几十个窗口。")
    print("  表中 n 是【窗口数】，不是【独立行情事件数】。把重叠窗口合并后：")
    print()
    print(f"  {'L':<4}{'档位':<18}{'窗口数n':>8}{'独立事件':>9}   事件（起~止, 含窗口数）")
    print("  " + "-" * 92)
    for L in WINDOWS:
        g = group(res_clean[L])
        for b in BUCKET_ORDER:
            ws = g.get(b)
            if not ws:
                continue
            ws = sorted(ws, key=lambda w: w["d0"])
            eps = merge_events(ws)
            det = " | ".join(f"{e[0]['d0']}~{e[-1]['d1']}({len(e)})" for e in eps)
            print(f"  {L:<4}{b:<18}{len(ws):>8}{len(eps):>9}   {det}")
    print()
    print("  → 例：15 天 涨20%档 n=9，实为 2 个事件（2020-09 那次占 8 个窗口）。")
    print("     7 天 涨15%档 n=7，实为 1 个事件（2020-09-30~2020-10-28）。")
    print("     任何以 n 为权重、或以 A 的中位数当'规律'的结论，都应按事件数重新审视。")

    # ---------- 敏感窗口 ----------
    allw = [w for L in WINDOWS for w in res_clean[L]]
    print()
    print("=" * 96)
    print("口径 B 下的敏感窗口（引用结论时应剔除或单独说明）")
    print("=" * 96)
    mix = [w for w in allw if w["basis_mix"]]
    print(f"  跨越 2024-10-07 折算口径变更日的窗口 : {len(mix)} / {len(allw)}")
    for w in mix[:8]:
        print(f"    L={w['L']:<3}{w['d0']} ~ {w['d1']}  F={w['F']:+.2f}%  R={w['R']:+.2f}%")
    stale = sorted(allw, key=lambda w: -w["basket_lag"])[:8]
    print("  篮子滞后最大（停割季，篮内部分是旧价）的窗口：")
    for w in stale:
        print(f"    L={w['L']:<3}{w['d0']} ~ {w['d1']}  篮子滞后 {w['basket_lag']:>3} 天"
              f"  F={w['F']:+.2f}%  R={w['R']:+.2f}%")
    print("  端点滞后最大的窗口：")
    for w in sorted(allw, key=lambda w: -max(w["lag0"], w["lag1"]))[:5]:
        print(f"    L={w['L']:<3}{w['d0']} ~ {w['d1']}  端点滞后 "
              f"{w['lag0']}/{w['lag1']} 天  F={w['F']:+.2f}%  R={w['R']:+.2f}%")

    # ---------- 导出明细 ----------
    with open(OUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["窗口长度_交易日", "起", "止", "期货涨跌F_pct", "全省原料涨跌R_pct",
                    "非对称比A", "传导缺口G_pp", "幅度档", "端点滞后_起", "端点滞后_止",
                    "篮子最大滞后_天", "跨越折算口径变更"])
        for L in WINDOWS:
            for x in res_clean[L]:
                w.writerow([L, x["d0"], x["d1"], round(x["F"], 4), round(x["R"], 4),
                            (round(x["A"], 4) if x["A"] is not None else ""),
                            round(x["G"], 4), x["bucket"], x["lag0"], x["lag1"],
                            x["basket_lag"], 1 if x["basis_mix"] else 0])
    print()
    print(f"已写出: {OUT_FILE}  （口径 B 逐窗口明细，{len(allw)} 条）")

    # ---------- 按独立事件聚合的汇总表 ----------
    print()
    print("=" * 96)
    print("按【独立事件】聚合的读数（每个行情片段只算一次，不重复计入）")
    print("=" * 96)
    print("  做法：同一 (L, 档位) 内把时间重叠的窗口并成一个事件，")
    print("        事件级 F/R 取该事件内窗口的中位数（同一片段内数值接近）。")
    print()
    print(f"  {'L':<4}{'档位':<18}{'事件数':>7}{'F中位%':>10}{'R中位%':>10}"
          f"{'A中位':>9}{'G中位':>10}   事件列表")
    print("  " + "-" * 92)
    ev_rows, n_event = [], 0
    for L in WINDOWS:
        g = group(res_clean[L])
        for b in BUCKET_ORDER:
            ws = g.get(b)
            if not ws:
                continue
            eps = merge_events(sorted(ws, key=lambda w: w["d0"]))
            n_event += len(eps)
            F = [median([w["F"] for w in e]) for e in eps]
            R = [median([w["R"] for w in e]) for e in eps]
            A = [r_ / f for f, r_ in zip(F, R) if abs(f) >= 2.0]
            G = [r_ - f for f, r_ in zip(F, R)]
            names = " | ".join(f"{e[0]['d0']}~{e[-1]['d1']}" for e in eps)
            print(f"  {L:<4}{b:<18}{len(eps):>7}{median(F):>+10.2f}{median(R):>+10.2f}"
                  f"{(median(A) if A else float('nan')):>9.3f}{median(G):>+10.2f}   {names}")
            for e, f_, r_, g_ in zip(eps, F, R, G):
                a_ = (r_ / f_) if abs(f_) >= 2.0 else None
                ev_rows.append([L, b, len(eps), e[0]["d0"], e[-1]["d1"], len(e),
                                round(f_, 4), round(r_, 4),
                                (round(a_, 4) if a_ is not None else ""),
                                round(g_, 4)])

    with open(OUT_EVENT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["窗口长度_交易日", "幅度档", "该档事件总数", "事件起", "事件止",
                    "含窗口数", "期货涨跌F_pct", "全省原料涨跌R_pct",
                    "非对称比A", "传导缺口G_pp"])
        w.writerows(ev_rows)
    print()
    print(f"已写出: {OUT_EVENT_FILE}  （按独立事件聚合，{n_event} 个事件）")


if __name__ == "__main__":
    main()

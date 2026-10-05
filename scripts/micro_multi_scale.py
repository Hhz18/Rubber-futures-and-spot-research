#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""micro_multi_scale.py — 多尺度微观窗口非对称性分析

对 3/5/7/14/15 天窗口分别计算期现非对称指标，产出跨尺度对比表与
尺度曲线数据，用于可视化 F→R 传导缺口随窗口长度的变化规律。

核心思路：
  每个窗口 = 一段期货交易日区间 [i, i+L]，取期货涨幅 F% 与原料涨幅 R%，
  计算传导缺口 G = R − F；按 F 方向（涨/跌）分组后对比 |G| 的非对称性。

口径：
  原料端用 LOCF 固定篮子全省均价（清洗版），详见 clean_provincial_price.py。
  期货端用后复权连续序列收盘价。

产物：
  控制台：尺度对比表 + 非对称性摘要
  data/微观多尺度汇总.csv   每个 (窗口长度, 方向, 幅度档) 的统计
  data/尺度曲线.csv          尺度曲线数据（供可视化）

用法：
  python micro_multi_scale.py
  python micro_multi_scale.py --max-stale 6
"""

import argparse
import bisect
import collections
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
TREND_SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "趋势判定方案", "src")
if TREND_SRC not in sys.path:
    sys.path.insert(0, TREND_SRC)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from build_dataset import load_futures, pair_common, FUT_FILE  # noqa: E402
from rubber_source import county_panel, provincial  # noqa: E402

CLEAN_FILE = os.path.join(DATA, "收购价_全省_清洗版.csv")

WINDOWS = [3, 5, 7, 14, 15]
BUCKET_ORDER = [
    "涨10%档(8-12%)", "涨15%档(12-18%)", "涨20%档(≥18%)",
    "跌10%档(8-12%)", "跌15%档(12-18%)", "跌20%档(≥18%)",
]


# ── 工具函数 ──────────────────────────────────────────────────────────

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
    """幅度分档：|F|<8% 返回 None，否则返回档名。"""
    a = abs(f)
    if a < 8.0:
        return None
    if a >= 18.0:
        return "涨20%档(≥18%)" if f > 0 else "跌20%档(≥18%)"
    if a >= 12.0:
        return "涨15%档(12-18%)" if f > 0 else "跌15%档(12-18%)"
    return "涨10%档(8-12%)" if f > 0 else "跌10%档(8-12%)"


# ── LOCF 序列 ────────────────────────────────────────────────────────

class LOCFSeries:
    """一条按观测日定义的原料序列，支持"取 target 日或之前最近一次观测"。

    与 micro_windows_provincial.Series 相同逻辑，这里重新实现一份
    以避免跨文件 import 复杂度。
    """

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
        return _date.fromisoformat(s[:10])


# ── 数据加载 ─────────────────────────────────────────────────────────

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


# ── 窗口计算 ─────────────────────────────────────────────────────────

def compute_windows(bars, fdates, locf, L, max_stale):
    """对窗口长度 L，滑动计算所有窗口的 F%, R%, G。

    返回 list[dict]，每个 dict 包含:
      L, d0, d1, F, R, G, bucket, lag0, lag1, basis_mix
    """
    wins = []
    for i in range(len(bars) - L):
        j = i + L
        d0, d1 = fdates[i], fdates[j]
        F = (bars[j].close - bars[i].close) / bars[i].close * 100

        p0 = locf.pick(d0, max_stale)
        p1 = locf.pick(d1, max_stale)
        if p0 is None or p1 is None:
            continue
        # 同一观测 → R 恒为 0，是假样本
        if p0[0] == p1[0]:
            continue

        R = (p1[1] - p0[1]) / p0[1] * 100
        A = R / F if abs(F) >= 2.0 else None
        G = R - F

        wins.append({
            "L": L, "d0": d0, "d1": d1,
            "F": F, "R": R, "A": A, "G": G,
            "bucket": fine_bucket(F),
            "lag0": p0[2], "lag1": p1[2],
            "basket_lag": max(p0[3] or 0, p1[3] or 0),
            "basis_mix": p0[4] != p1[4],
        })
    return wins


# ── 非对称性统计 ─────────────────────────────────────────────────────

def asymmetry_stats(wins, f_thresh=2.0):
    """对一组窗口计算非对称性统计。

    f_thresh: |F| 的有效阈值（%），低于此的窗口不计入有效样本。

    返回 dict:
      total      - 总窗口数
      valid      - |F|>=f_thresh 的窗口数
      up_n       - F>0 且 |F|>=f_thresh 的窗口数
      dn_n       - F<0 且 |F|>=f_thresh 的窗口数
      up_med_F   - 上涨组 F 中位数
      up_med_R   - 上涨组 R 中位数
      up_med_G   - 上涨组 G 中位数
      up_med_RF  - 上涨组 R/F 中位数
      dn_med_F   - 下跌组 F 中位数
      dn_med_R   - 下跌组 R 中位数
      dn_med_G   - 下跌组 G 中位数
      dn_med_RF  - 下跌组 R/F 中位数
      asym_diff  - |G_up| − |G_dn|（非对称性差异）
      slope      - OLS R~F 斜率
      by_bucket  - {bucket: {n, med_F, med_R, med_G, med_RF}}
    """
    total = len(wins)
    valid_w = [w for w in wins if abs(w["F"]) >= f_thresh]
    up_w = [w for w in valid_w if w["F"] > 0]
    dn_w = [w for w in valid_w if w["F"] < 0]

    def _med_group(ws):
        if not ws:
            return {k: None for k in ("med_F", "med_R", "med_G", "med_RF")}
        Fs = [w["F"] for w in ws]
        Rs = [w["R"] for w in ws]
        Gs = [w["G"] for w in ws]
        RFs = [w["R"] / w["F"] for w in ws if abs(w["F"]) >= 2.0]
        return {
            "med_F": median(Fs),
            "med_R": median(Rs),
            "med_G": median(Gs),
            "med_RF": median(RFs) if RFs else None,
        }

    ug = _med_group(up_w)
    dg = _med_group(dn_w)

    asym_diff = None
    if ug["med_G"] is not None and dg["med_G"] is not None:
        asym_diff = abs(ug["med_G"]) - abs(dg["med_G"])

    slope = ols_slope([w["F"] for w in valid_w], [w["R"] for w in valid_w])

    # 按幅度档细分
    by_bucket = {}
    for b in BUCKET_ORDER:
        bw = [w for w in valid_w if w["bucket"] == b]
        if not bw:
            continue
        Fs = [w["F"] for w in bw]
        Rs = [w["R"] for w in bw]
        Gs = [w["G"] for w in bw]
        RFs = [w["R"] / w["F"] for w in bw if abs(w["F"]) >= 2.0]
        by_bucket[b] = {
            "n": len(bw),
            "med_F": median(Fs),
            "med_R": median(Rs),
            "med_G": median(Gs),
            "med_RF": median(RFs) if RFs else None,
        }

    return {
        "total": total,
        "valid": len(valid_w),
        "up_n": len(up_w),
        "dn_n": len(dn_w),
        "up_med_F": ug["med_F"],
        "up_med_R": ug["med_R"],
        "up_med_G": ug["med_G"],
        "up_med_RF": ug["med_RF"],
        "dn_med_F": dg["med_F"],
        "dn_med_R": dg["med_R"],
        "dn_med_G": dg["med_G"],
        "dn_med_RF": dg["med_RF"],
        "asym_diff": asym_diff,
        "slope": slope,
        "by_bucket": by_bucket,
    }


# ── 打印与导出 ───────────────────────────────────────────────────────

def print_scale_table(all_stats, f_thresh):
    """打印跨尺度对比表。"""
    print()
    print("=" * 100)
    print(f"多尺度非对称性对比（|F|≥{f_thresh:.0f}%）")
    print("=" * 100)
    print(f"{'L':<4}{'总窗':>6}{'有效':>6}{'涨n':>5}{'跌n':>5}"
          f"{'F涨中位':>9}{'R涨中位':>9}{'G涨中位':>9}{'R/F涨':>8}"
          f"{'F跌中位':>9}{'R跌中位':>9}{'G跌中位':>9}{'R/F跌':>8}"
          f"{'|G涨|-|G跌|':>12}{'斜率':>8}")
    print("-" * 100)
    for L in WINDOWS:
        s = all_stats[(L, f_thresh)]
        print(f"{L:<4}{s['total']:>6}{s['valid']:>6}"
              f"{s['up_n']:>5}{s['dn_n']:>5}"
              f"{(s['up_med_F'] or float('nan')):>+9.2f}"
              f"{(s['up_med_R'] or float('nan')):>+9.2f}"
              f"{(s['up_med_G'] or float('nan')):>+9.2f}"
              f"{(s['up_med_RF'] or float('nan')):>8.3f}"
              f"{(s['dn_med_F'] or float('nan')):>+9.2f}"
              f"{(s['dn_med_R'] or float('nan')):>+9.2f}"
              f"{(s['dn_med_G'] or float('nan')):>+9.2f}"
              f"{(s['dn_med_RF'] or float('nan')):>8.3f}"
              f"{(s['asym_diff'] if s['asym_diff'] is not None else float('nan')):>+12.3f}"
              f"{(s['slope'] if s['slope'] is not None else float('nan')):>8.3f}")
    print("-" * 100)


def print_bucket_detail(all_stats, f_thresh):
    """打印各尺度下的幅度档细分。"""
    print()
    print("=" * 100)
    print(f"各尺度 × 幅度档细分（|F|≥{f_thresh:.0f}%）")
    print("=" * 100)
    for L in WINDOWS:
        s = all_stats[(L, f_thresh)]
        bb = s["by_bucket"]
        print(f"\n【{L} 天窗口】")
        print(f"  {'幅度档':<20}{'n':>4}{'F中位%':>10}{'R中位%':>10}"
              f"{'G中位pp':>10}{'R/F中位':>9}")
        print("  " + "-" * 70)
        for b in BUCKET_ORDER:
            if b not in bb:
                continue
            d = bb[b]
            print(f"  {b:<20}{d['n']:>4}"
                  f"{d['med_F']:>+10.2f}{d['med_R']:>+10.2f}"
                  f"{d['med_G']:>+10.2f}"
                  f"{(d['med_RF'] if d['med_RF'] is not None else float('nan')):>9.3f}")
        print("  " + "-" * 70)


def export_summary_csv(all_stats, path):
    """导出微观多尺度汇总.csv。"""
    fields = [
        "行类型", "窗口长度", "F阈值_pct",
        "总窗口数", "有效窗口数", "上涨窗口数", "下跌窗口数",
        "上涨_F中位_pct", "上涨_R中位_pct", "上涨_G中位_pp", "上涨_RF中位",
        "下跌_F中位_pct", "下跌_R中位_pct", "下跌_G中位_pp", "下跌_RF中位",
        "非对称差_G涨减G跌_pp", "OLS斜率_R~F",
        "幅度档", "档_n", "档_F中位_pct", "档_R中位_pct", "档_G中位_pp", "档_RF中位",
    ]
    rows = []
    for L in WINDOWS:
        for f_thresh in [2.0, 8.0]:
            s = all_stats[(L, f_thresh)]
            rows.append({
                "行类型": "汇总",
                "窗口长度": L,
                "F阈值_pct": f_thresh,
                "总窗口数": s["total"],
                "有效窗口数": s["valid"],
                "上涨窗口数": s["up_n"],
                "下跌窗口数": s["dn_n"],
                "上涨_F中位_pct": _r(s["up_med_F"]),
                "上涨_R中位_pct": _r(s["up_med_R"]),
                "上涨_G中位_pp": _r(s["up_med_G"]),
                "上涨_RF中位": _r(s["up_med_RF"]),
                "下跌_F中位_pct": _r(s["dn_med_F"]),
                "下跌_R中位_pct": _r(s["dn_med_R"]),
                "下跌_G中位_pp": _r(s["dn_med_G"]),
                "下跌_RF中位": _r(s["dn_med_RF"]),
                "非对称差_G涨减G跌_pp": _r(s["asym_diff"]),
                "OLS斜率_R~F": _r(s["slope"]),
                "幅度档": "", "档_n": "", "档_F中位_pct": "",
                "档_R中位_pct": "", "档_G中位_pp": "", "档_RF中位": "",
            })
            for b in BUCKET_ORDER:
                if b not in s["by_bucket"]:
                    continue
                d = s["by_bucket"][b]
                rows.append({
                    "行类型": "细分",
                    "窗口长度": L,
                    "F阈值_pct": f_thresh,
                    "总窗口数": "", "有效窗口数": "",
                    "上涨窗口数": "", "下跌窗口数": "",
                    "上涨_F中位_pct": "", "上涨_R中位_pct": "",
                    "上涨_G中位_pp": "", "上涨_RF中位": "",
                    "下跌_F中位_pct": "", "下跌_R中位_pct": "",
                    "下跌_G中位_pp": "", "下跌_RF中位": "",
                    "非对称差_G涨减G跌_pp": "", "OLS斜率_R~F": "",
                    "幅度档": b,
                    "档_n": d["n"],
                    "档_F中位_pct": _r(d["med_F"]),
                    "档_R中位_pct": _r(d["med_R"]),
                    "档_G中位_pp": _r(d["med_G"]),
                    "档_RF中位": _r(d["med_RF"]),
                })
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\n已写出: {path}  （{len(rows)} 行）")


def export_scale_curve(all_stats, path):
    """导出尺度曲线.csv — 供可视化。"""
    rows = []
    for f_thresh in [2.0, 8.0]:
        for L in WINDOWS:
            s = all_stats[(L, f_thresh)]
            rows.append({
                "F阈值_pct": f_thresh,
                "窗口长度": L,
                "有效窗口数": s["valid"],
                "上涨窗口数": s["up_n"],
                "下跌窗口数": s["dn_n"],
                "G涨中位_pp": _r(s["up_med_G"]),
                "G跌中位_pp": _r(s["dn_med_G"]),
                "非对称差_pp": _r(s["asym_diff"]),
                "OLS斜率": _r(s["slope"]),
                "R涨中位_pct": _r(s["up_med_R"]),
                "R跌中位_pct": _r(s["dn_med_R"]),
                "R涨_RF中位": _r(s["up_med_RF"]),
                "R跌_RF中位": _r(s["dn_med_RF"]),
            })
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"已写出: {path}  （{len(rows)} 行）")


def _r(v):
    """Round for CSV export, None -> ''."""
    return round(v, 4) if v is not None else ""


# ── 主入口 ───────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="多尺度微观窗口非对称性分析")
    ap.add_argument("--max-stale", type=int, default=7,
                    help="端点取价允许的最大滞后天数，默认 7")
    args = ap.parse_args()

    # ── 加载数据 ──
    bars, fdates = load_futures(FUT_FILE, "close")
    cities = county_panel()
    start = min(cities)
    i0 = next(i for i, x in enumerate(fdates) if x >= start)
    bars, fdates = bars[i0:], fdates[i0:]

    clean_px, clean_lag, clean_basis = load_clean_prov()
    locf = LOCFSeries(clean_px, clean_lag, clean_basis)

    print("=" * 100)
    print("多尺度微观窗口非对称性分析")
    print("=" * 100)
    print(f"期货: {os.path.basename(FUT_FILE)}  {len(bars)} 根  "
          f"{fdates[0]} ~ {fdates[-1]}")
    print(f"原料观测: 清洗版 {len(clean_px)} 期")
    print(f"窗口长度: {WINDOWS}")
    print(f"端点最大滞后: {args.max_stale} 天")

    # ── 对每个窗口长度计算窗口 ──
    all_windows = {}
    for L in WINDOWS:
        wins = compute_windows(bars, fdates, locf, L, args.max_stale)
        all_windows[L] = wins
        print(f"  L={L}: {len(wins)} 个窗口（有效 |F|≥8%: "
              f"{sum(1 for w in wins if abs(w['F']) >= 8.0)}）")

    # ── 计算两种阈值下的非对称性统计 ──
    all_stats = {}
    for f_thresh in [2.0, 8.0]:
        for L in WINDOWS:
            all_stats[(L, f_thresh)] = asymmetry_stats(
                all_windows[L], f_thresh=f_thresh)

    # ── 打印对比表 ──
    print_scale_table(all_stats, f_thresh=2.0)
    print_scale_table(all_stats, f_thresh=8.0)
    print_bucket_detail(all_stats, f_thresh=8.0)

    # ── 非对称性摘要 ──
    print()
    print("=" * 100)
    print("非对称性摘要：|G_up| − |G_dn| 随窗口长度的变化")
    print("=" * 100)
    print("  含义：> 0 → 下跌时原料比期货跌得少（原料抗跌）；")
    print("        < 0 → 上涨时原料比期货涨得少（原料滞后）")
    print()
    for f_thresh in [2.0, 8.0]:
        print(f"  |F| ≥ {f_thresh:.0f}%:")
        print(f"    {'L':<4}{'涨|G|':>8}{'跌|G|':>8}{'差值':>10}{'样本(涨/跌)':>14}")
        print("    " + "-" * 46)
        for L in WINDOWS:
            s = all_stats[(L, f_thresh)]
            ug = abs(s["up_med_G"]) if s["up_med_G"] is not None else float("nan")
            dg = abs(s["dn_med_G"]) if s["dn_med_G"] is not None else float("nan")
            diff = s["asym_diff"] if s["asym_diff"] is not None else float("nan")
            print(f"    {L:<4}{ug:>8.3f}{dg:>8.3f}{diff:>+10.3f}"
                  f"   {s['up_n']}/{s['dn_n']}")
        print()

    # ── 导出 CSV ──
    summary_path = os.path.join(DATA, "微观多尺度汇总.csv")
    curve_path = os.path.join(DATA, "尺度曲线.csv")
    export_summary_csv(all_stats, summary_path)
    export_scale_curve(all_stats, curve_path)

    # ── 逐窗口明细导出 ──
    detail_path = os.path.join(DATA, "微观多尺度明细.csv")
    with open(detail_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["窗口长度", "起日", "止日", "F_pct", "R_pct", "G_pp",
                     "A", "幅度档", "lag0", "lag1", "basket_lag", "basis_mix"])
        for L in WINDOWS:
            for x in all_windows[L]:
                w.writerow([
                    L, x["d0"], x["d1"],
                    round(x["F"], 4), round(x["R"], 4), round(x["G"], 4),
                    (round(x["A"], 4) if x["A"] is not None else ""),
                    x["bucket"] or "",
                    x["lag0"], x["lag1"], x["basket_lag"],
                    1 if x["basis_mix"] else 0,
                ])
    total = sum(len(all_windows[L]) for L in WINDOWS)
    print(f"已写出: {detail_path}  （{total} 条逐窗口明细）")

    print()
    print("完成。")


if __name__ == "__main__":
    main()
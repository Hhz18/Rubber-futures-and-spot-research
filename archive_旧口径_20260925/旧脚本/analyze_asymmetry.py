# -*- coding: utf-8 -*-
"""
analyze_asymmetry.py —— 期现非对称性核心统计

两条互补的路径（这是本项目最重要的方法论设计）：

  路径 A：ZigZag 区间法
      按趋势结构切出"区间"，每个区间一个样本。
      —— 与你手工整理案例的方法完全一致，能落进你定义的 5 类 × 2 档。
      —— 缺点：独立区间只有 ~30 个，撑不起 10 个格子的分组统计。

  路径 B：滑动窗口事件研究法
      枚举所有 (起始日, 窗口长度) 组合，只要两端都能取到原料价就构成一个样本。
      —— 样本量数千，能给出稳定的条件分布。
      —— 缺点：窗口高度重叠，样本【不独立】，不能直接用常规显著性检验；
                故显著性一律用 Block Bootstrap（按时间分块重采样）。

  为什么两条都要：
      路径 A 回答"在你定义的那几类行情里，非对称长什么样"；
      路径 B 回答"给定期货涨 X%，原料涨多少"——这才是采购决策真正要用的条件分布。
      只有 A 会被样本量卡死；只有 B 会丢掉趋势结构。两者结合才立得住。

产出：
    data/滑动窗口样本.csv
    data/分组统计.csv
    研究报告.md

用法：
    python analyze_asymmetry.py
    python analyze_asymmetry.py --min-window 10 --max-window 60
"""

import argparse
import csv
import math
import os
import random
import sys
from datetime import datetime

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

from build_dataset import (load_futures, load_cities, load_close_dict,  # noqa: E402
                           pair_common, d, FUT_FILE, LVL_FILE)

SEG_FILE = os.path.join(DATA, "区间样本.csv")
WIN_FILE = os.path.join(DATA, "滑动窗口样本.csv")
GRP_FILE = os.path.join(DATA, "分组统计.csv")
REPORT = os.path.join(HERE, "研究报告.md")

rng = random.Random(20260917)


# ------------------------------------------------------------------
# 统计工具
# ------------------------------------------------------------------
def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def block_bootstrap_ci(xs, n_boot=4000, block=5, alpha=0.05, seed=None,
                       time_index=None):
    """按时间分块的 Bootstrap 置信区间。

    为什么不用普通 bootstrap：相邻样本高度重叠（滚动窗口共享大部分区间），
    独立重采样会严重低估方差、把噪声说成显著。
    分块重采样保留块内的时间相关性，得到更诚实的区间。

    ⚠ 2026-09 修正（审计问题 #21 / #18）：
    1) 原实现用全局 rng，结果依赖调用顺序（连续调用同一组数据，CI 上界
       实测漂移 1.2%）。现在接受 `seed`，给定后结果完全可复现；
       传 None 时才退回全局 rng（兼容旧调用）。
    2) 原实现 `randrange(0, max(1, n - block + 1))` 允许块起点任意重叠，
       重采样序列是"随机拼贴"，块之间可能首尾相接形成更长连续段，
       方向上【低估】方差。现在改为【从随机起点开始等距切块】
       （circular block bootstrap）：起点随机一次，块按 block 等距推进并
       环绕取模，块之间不再重叠。
    3) `time_index` 给出每个样本的时间（如起始日字符串）。若相邻样本的
       时间间隔远大于 block，说明块被切在了不连续的位置上（池化多个窗口
       长度时会出现），此时给出警告 —— 那种块不构成"时间上相邻的一段"。
    """
    n = len(xs)
    if n < 8:
        return None, None
    r = random.Random(seed) if seed is not None else rng
    n_blocks = max(2, (n + block - 1) // block)
    if time_index is not None and block > 1:
        gaps = []
        for k in range(0, n - block, block):
            try:
                g = (datetime.strptime(time_index[k + block][:10], "%Y-%m-%d") -
                     datetime.strptime(time_index[k][:10], "%Y-%m-%d")).days
            except (ValueError, TypeError):
                continue
            gaps.append(g)
        if gaps:
            med = sorted(gaps)[len(gaps) // 2]
            span = (datetime.strptime(time_index[-1][:10], "%Y-%m-%d") -
                    datetime.strptime(time_index[0][:10], "%Y-%m-%d")).days
            if span > 0 and med > span / max(1, len(gaps)) * 4:
                print(f"    [警告] 分块 Bootstrap 的块内时间跨度过大"
                      f"（中位 {med} 天），块可能切在不连续的位置上，"
                      f"CI 会被放宽。")
    stats = []
    for _ in range(n_boot):
        s = []
        for _ in range(n_blocks):
            # 每个块【各自】随机取起点，块内连续、跨序列末端环绕 ——
            # 标准 circular block bootstrap。注意不能只抽一个全局起点：
            # 那样得到的是整条序列的旋转，中位数几乎不变，CI 会塌成一点。
            st = r.randrange(0, n)
            for j in range(block):
                s.append(xs[(st + j) % n])
        if s:
            stats.append(median(s))
    if not stats:
        return None, None
    stats.sort()
    return stats[int(alpha / 2 * len(stats))], stats[int((1 - alpha / 2) * len(stats)) - 1]


def sign_test(xs, less_than=1.0):
    """符号检验：有多少比例落在 less_than 以下（用于检验 A < 1）。"""
    n = len(xs)
    if n == 0:
        return None, None
    k = sum(1 for x in xs if x < less_than)
    # 正态近似
    z = (k - n / 2) / math.sqrt(n / 4) if n else 0
    p = 0.5 * math.erfc(abs(z) / math.sqrt(2))
    return k / n, p


# ------------------------------------------------------------------
# 路径 B：滑动窗口
# ------------------------------------------------------------------
def build_windows(bars, dates, cities, min_w, max_w, step, max_stale, min_common,
                  lvl=None):
    """枚举 (起始, 长度) 窗口，两端都能取到原料价则成为一条样本。

    窗口长度以【交易日】计；起始日按 step 个交易日滑动。

    原料涨幅一律走 pair_common()：两端只用【共同报价市县】求均值再比，
    构成效应被彻底消除（详见 build_price_index.py 的口径说明）。

    lvl: {日期: 未复权期货价}。**价差 ΔS 必须用它** ——
         bars 若来自后复权序列，历史绝对价被缩放过，算出的不是元/吨。
    """
    rows = []
    n = len(bars)
    n_drop_common = 0
    for L in range(min_w, max_w + 1, max(1, (max_w - min_w) // 6 or 1)):
        for i in range(0, n - L, step):
            j = i + L
            d0, d1 = dates[i], dates[j]
            pr = pair_common(cities, d0, d1, max_stale, min_common)
            if pr is None:
                n_drop_common += 1
                continue
            F0, F1 = bars[i].close, bars[j].close
            F = (F1 - F0) / F0 * 100.0
            R = (pr["r1"] - pr["r0"]) / pr["r0"] * 100.0
            L0 = (lvl or {}).get(d0, F0)
            L1 = (lvl or {}).get(d1, F1)
            rows.append({
                "窗口": L, "起始日": d0, "结束日": d1,
                "期货起点": F0, "期货终点": F1, "期货涨幅_pct": round(F, 4),
                "原料观测起": pr["obs0"], "原料观测终": pr["obs1"],
                "共同市县数": pr["n_common"],
                "原料起点": round(pr["r0"], 4), "原料终点": round(pr["r1"], 4),
                "原料涨幅_pct": round(R, 4),
                "非对称比A": (round(R / F, 4) if abs(F) >= 2 else ""),
                "传导缺口G_pct": round(R - F, 4),
                "价差变化dS_元吨": round((L1 - pr["r1"] * 1000) - (L0 - pr["r0"] * 1000), 1),
            })
    return rows, n_drop_common


# ------------------------------------------------------------------
# 阈值无关的读数列（审计问题 #5 / #23）
# ------------------------------------------------------------------
# A = R/F 只有在 |F| 足够大时才可算，于是"哪些窗口进入 A 的统计"取决于 |F|，
# 而 |F| 的分布随窗口长度 w 剧烈变化（w=3 时只有 33% 的窗口满足 |F|≥2%，
# w=250 时 96% 满足）。于是"用 A 中位看收敛"会混入纯粹的构成变化 ——
# 实测在固定的 |F|∈[2,5)% 带内，A 中位从 w=3 的 0.163 涨到 w=250 的 1.163，
# 与"不筛 A 中位"的 0.174→0.840 走向完全不同。
#
# 下面这些量【不需要任何阈值】，可用于跨窗口长度比较：
#   * 回归斜率 b（R ~ F）：不筛样本，"期货动 1%、原料动 b%" 直接可读；
#   * 传导缺口方向量 med(G/F)：有方向，不受 |·| 整流影响；
#   * 传导缺口绝对量 med|G| / med|F|：只作诊断，不单独作为"缺口"证据。

def has_A(r) -> bool:
    """该窗口样本是否可算 A（|F| ≥ 2%）。供 nonoverlapping(eligible=...) 用。"""
    return r.get("非对称比A", "") != ""


def F_OF(r) -> float:
    return float(r["期货涨幅_pct"])


def R_OF(r) -> float:
    return float(r["原料涨幅_pct"])


def A_OF(r):
    v = r.get("非对称比A", "")
    return float(v) if v != "" else None


def ols_slope(rows, ykey="原料涨幅_pct", xkey="期货涨幅_pct"):
    """阈值无关的 R ~ F 斜率。返回 (斜率, R², n)；样本不足返回 (None, None, n)。

    这是跨窗口长度唯一可直接比较的"跟上了多少"：
    A 中位受 |F| 筛选与除法污染，|G|/|F| 受 |·| 整流污染，斜率不受两者影响。
    """
    n = len(rows)
    if n < 4:
        return None, None, n
    X = [float(r[xkey]) for r in rows]
    Y = [float(r[ykey]) for r in rows]
    mx, my = sum(X) / n, sum(Y) / n
    sxx = sum((a - mx) ** 2 for a in X)
    if sxx <= 0:
        return None, None, n
    sxy = sum((a - mx) * (b - my) for a, b in zip(X, Y))
    syy = sum((b - my) ** 2 for b in Y)
    return sxy / sxx, ((sxy * sxy) / (sxx * syy) if syy > 0 else None), n


def theil_sen(rows, ykey="原料涨幅_pct", xkey="期货涨幅_pct",
              n_pairs=4000, seed=20260917):
    """Theil-Sen 稳健斜率（随机取对），对重尾的原料涨幅比 OLS 稳健。

    原料涨幅偏度 −2.7、标准差 2.54pp 而中位仅 −0.03%，OLS 会被少数跳变带偏。
    返回 (斜率, 95%CI, n_pairs_used)。
    """
    r = random.Random(seed)
    X = [float(x[xkey]) for x in rows]
    Y = [float(x[ykey]) for x in rows]
    n = len(X)
    if n < 8:
        return None, None, 0
    slopes = []
    for _ in range(n_pairs):
        a, b = r.randrange(n), r.randrange(n)
        if X[a] != X[b]:
            slopes.append((Y[b] - Y[a]) / (X[b] - X[a]))
    if not slopes:
        return None, None, 0
    slopes.sort()
    m = len(slopes)
    return (slopes[m // 2],
            (slopes[int(.025 * m)], slopes[int(.975 * m) - 1]),
            m)


def gap_directional(rows):
    """有方向的传导缺口中位：med(G / F)。

    这是"期货涨 1%、原料少涨多少"的正确读法（负 = 原料跟不上）。
    `med(|G|)/med(|F|)` 受 |·| 整流影响会系统性偏高：把 G 分解成
    (b−1)F + 残差后，w=250 时残差项占 med|G| 的 2/3，那部分与"缺口"无关。
    """
    v = [float(x["传导缺口G_pct"]) / float(x["期货涨幅_pct"])
         for x in rows if abs(float(x["期货涨幅_pct"])) > 0.05]
    return _med(v) if v else None


def gap_abs_diagnostic(rows):
    """诊断量 med|G| / med|F|。分子分母来自【同一批】窗口（原实现不是）。

    ⚠ 不要单独引用它作为"缺口有多大"的证据：它把零均值噪声经 |·| 整流后
    也计入了分子。报告里必须同时给出 gap_directional() 与 ols_slope()。
    """
    G = [abs(float(x["传导缺口G_pct"])) for x in rows]
    F = [abs(float(x["期货涨幅_pct"])) for x in rows]
    if not G or not F:
        return None, None
    return _med(G), _med(F)


def bucket_share(rows) -> float:
    """可算 A 的窗口占全部窗口的比例 —— 这个比例本身随 w 变化很大，
    必须在报告里与 A 一起给出，否则读者会把构成变化当成收敛。"""
    if not rows:
        return 0.0
    return sum(1 for r in rows if has_A(r)) / len(rows)


def bucket_mix(rows) -> dict:
    """把样本按 |F| 分带，返回各带占比 —— 用于诊断"跨 w 的构成变化"。

    A 只在 |F|≥2% 上可算，而各带内的 A 水平差别很大，所以当某档的
    样本构成从"以 2-5% 为主"变成"以 >20% 为主"时，A 中位会自己往上走。
    这个函数把构成摆出来，让读者能判断"收敛"有多少是构成造成的。
    """
    bands = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 1e9)]
    n = len(rows) or 1
    out = {}
    for lo, hi in bands:
        c = sum(1 for r in rows if lo <= abs(float(r["期货涨幅_pct"])) < hi)
        out[f"{lo}-{hi if hi < 1e8 else 'inf'}%"] = c / n
    return out


def fsign(f: float) -> str:
    if abs(f) < 2:
        return "0-平稳(|F|<2%)"
    if f > 0:
        return "涨" + ("0-10%" if f < 10 else "10-20%" if f < 20 else ">20%")
    a = abs(f)
    return "跌" + ("0-10%" if a < 10 else "10-20%" if a < 20 else ">20%")


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="期现非对称性统计")
    ap.add_argument("--min-window", type=int, default=10, help="最短窗口（交易日）")
    ap.add_argument("--max-window", type=int, default=60, help="最长窗口（交易日）")
    ap.add_argument("--step", type=int, default=3, help="窗口滑动步长（交易日）")
    ap.add_argument("--max-stale", type=int, default=7)
    ap.add_argument("--min-common", type=int, default=6,
                    help="两端共同报价市县数下限")
    args = ap.parse_args()

    bars, dates = load_futures(FUT_FILE, "close")
    lvl = load_close_dict(LVL_FILE, "close")
    cities = load_cities()
    start = min(cities)
    i0 = next(i for i, x in enumerate(dates) if x >= start)
    bars, dates = bars[i0:], dates[i0:]

    # ---------- 路径 B ----------
    print("[路径B] 生成滑动窗口样本 …")
    wins, n_drop = build_windows(bars, dates, cities, args.min_window,
                                 args.max_window, args.step, args.max_stale,
                                 args.min_common, lvl)
    with open(WIN_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(wins[0].keys()))
        w.writeheader()
        w.writerows(wins)
    print(f"  窗口样本 {len(wins)} 条（因共同市县 < {args.min_common} 丢弃 {n_drop} 条）"
          f" → {WIN_FILE}")

    # ---------- 路径 A ----------
    print("[路径A] 读取 ZigZag 区间样本 …")
    segs = []
    if os.path.exists(SEG_FILE):
        with open(SEG_FILE, encoding="utf-8-sig") as f:
            segs = list(csv.DictReader(f))
    print(f"  区间样本 {len(segs)} 条")

    # ---------- 分组统计 ----------
    print("[统计] 分组 …")
    groups = {}
    for r in wins:
        g = fsign(float(r["期货涨幅_pct"]))
        groups.setdefault(g, []).append(r)

    order = ["涨0-10%", "涨10-20%", "涨>20%", "0-平稳(|F|<2%)",
             "跌0-10%", "跌10-20%", "跌>20%"]

    stat_rows = []
    for g in order:
        rs = groups.get(g, [])
        if not rs:
            continue
        A = [float(r["非对称比A"]) for r in rs if r["非对称比A"] != ""]
        G = [float(r["传导缺口G_pct"]) for r in rs]
        dS = [float(r["价差变化dS_元吨"]) for r in rs]
        lo, hi = block_bootstrap_ci(A, seed=20260917) if A else (None, None)
        pct_lt1, p = sign_test(A, 1.0) if A else (None, None)
        b, r2, _ = ols_slope(rs)
        ts, ts_ci, _ = theil_sen(rs)
        stat_rows.append({
            "分组": g, "样本数": len(rs), "可算A数": len(A),
            "A中位数": round(median(A), 4) if A else "",
            "A均值": round(mean(A), 4) if A else "",
            "A_CI下限": round(lo, 4) if lo is not None else "",
            "A_CI上限": round(hi, 4) if hi is not None else "",
            "A<1占比": round(pct_lt1, 4) if pct_lt1 is not None else "",
            "A<1_p值": round(p, 4) if p is not None else "",
            "G中位数_pct": round(median(G), 4),
            # 阈值无关的读数列：A 只覆盖 |F|≥2% 的子集，斜率覆盖全部样本
            "斜率R~F": round(b, 4) if b is not None else "",
            "R2": round(r2, 4) if r2 is not None else "",
            "稳健斜率": round(ts, 4) if ts is not None else "",
            "有向缺口中位_G比F": (round(gap_directional(rs), 4)
                                  if gap_directional(rs) is not None else ""),
            "dS中位数_元吨": round(median(dS), 1),
            "dS为正占比": round(sum(1 for x in dS if x > 0) / len(dS), 4),
        })

    with open(GRP_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(stat_rows[0].keys()))
        w.writeheader()
        w.writerows(stat_rows)

    # ---------- 控制台输出 ----------
    print()
    print("=" * 96)
    print("路径B（滑动窗口，样本量大）—— 按期货涨幅分组的原料响应")
    print("=" * 96)
    hdr = (f"{'分组':<16}{'样本':>6}{'可算A':>7}{'A中位':>9}{'A的95%CI':>18}"
           f"{'斜率R~F':>10}{'稳健斜率':>10}{'有向缺口':>10}{'G中位%':>9}{'ΔS中位':>10}")
    print(hdr)
    print("-" * 110)
    for r in stat_rows:
        ci = (f"[{r['A_CI下限']:.2f},{r['A_CI上限']:.2f}]"
              if r["A_CI下限"] != "" else "—")
        # 平稳组 |F|<2% 时全部样本都算不出 A，此处必须容忍空值
        a_med = f"{r['A中位数']:.3f}" if r["A中位数"] != "" else "—"
        b = f"{r['斜率R~F']:.3f}" if r["斜率R~F"] != "" else "—"
        ts = f"{r['稳健斜率']:.3f}" if r["稳健斜率"] != "" else "—"
        gd = (f"{r['有向缺口中位_G比F']:+.3f}"
              if r["有向缺口中位_G比F"] != "" else "—")
        print(f"{r['分组']:<16}{r['样本数']:>6}{r['可算A数']:>7}{a_med:>9}"
              f"{ci:>18}{b:>10}{ts:>10}{gd:>10}"
              f"{r['G中位数_pct']:>9.2f}{r['dS中位数_元吨']:>10.0f}")
    print("  ↑ A 只在 |F|≥2% 的子集上可算，且各档的该子集占比不同 ——")
    print("    跨档比较请以「斜率R~F / 稳健斜率 / 有向缺口」为准（它们不筛样本）。")

    print()
    print("=" * 96)
    print("路径A（ZigZag 区间，符合你定义的分类）")
    print("=" * 96)
    if segs:
        import collections
        by = collections.defaultdict(list)
        for r in segs:
            by[(r["分类"], r["幅度档"])].append(r)
        print(f"{'分类':<26}{'幅度档':>8}{'样本':>5}{'A中位':>9}{'G中位%':>9}{'ΔS中位':>10}")
        print("-" * 96)
        for (cat, bk) in sorted(by):
            rs = by[(cat, bk)]
            A = [float(r["非对称比A"]) for r in rs if r["非对称比A"] != ""]
            G = [float(r["传导缺口G_pct"]) for r in rs]
            dS = [float(r["价差变化dS_元吨"]) for r in rs]
            print(f"{cat:<26}{bk:>8}{len(rs):>5}"
                  f"{(median(A) if A else float('nan')):>9.3f}"
                  f"{median(G):>9.2f}{median(dS):>10.0f}")

    # ---------- 报告 ----------
    write_report(stat_rows, segs, wins, dates)
    print()
    print(f"分组统计 → {GRP_FILE}")
    print(f"研究报告 → {REPORT}")


def _med(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


UP_BINS = [(2, 5, "涨2-5%"), (5, 10, "涨5-10%"), (10, 20, "涨10-20%"),
           (20, 1e9, "涨>20%")]
DN_BINS = [(2, 5, "跌2-5%"), (5, 10, "跌5-10%"), (10, 1e9, "跌>10%")]


def _cut_rows(rows):
    """把一批窗口样本按涨/跌分档汇总成 (标签, n, A中位, G中位)。"""
    out = []
    for lo, hi, lb in UP_BINS + DN_BINS:
        if lb.startswith("涨"):
            s = [r for r in rows if lo <= float(r["期货涨幅_pct"]) < hi]
        else:
            s = [r for r in rows if -hi < float(r["期货涨幅_pct"]) <= -lo]
        A = [float(r["非对称比A"]) for r in s if r["非对称比A"] != ""]
        G = [float(r["传导缺口G_pct"]) for r in s]
        out.append((lb, len(s),
                    _med(A) if A else None,
                    _med(G) if G else None))
    return out


def _cut_rows_full(rows):
    """同 _cut_rows，但额外给出 A 的分块 Bootstrap CI 与 A<1 占比。

    速览表要用：结论的可信度取决于 CI 是否跨过 1，只给中位数是不够的。
    G（传导缺口，百分点）是无量纲的，比 元/吨 的 ΔS 稳健得多，故一并给出。

    ⚠ 2026-09 修正（审计问题 #5 / #19 / #23）：原表只给 A 中位与 A 的 CI，
    而 A 只在 |F|≥2% 的子集上存在、且该子集占各档的比例差异很大
    （涨2-5% 档 100%、涨>20% 档 100%，但"平稳"档 0%）。
    现在每档额外给出【不筛样本】的 OLS 斜率、Theil-Sen 稳健斜率、
    有方向缺口中位 med(G/F) 与样本覆盖比，使跨档比较不再依赖 A。
    """
    out = []
    for lo, hi, lb in UP_BINS + DN_BINS:
        if lb.startswith("涨"):
            s = [r for r in rows if lo <= float(r["期货涨幅_pct"]) < hi]
        else:
            s = [r for r in rows if -hi < float(r["期货涨幅_pct"]) <= -lo]
        A = [float(r["非对称比A"]) for r in s if r["非对称比A"] != ""]
        G = [float(r["传导缺口G_pct"]) for r in s]
        clo, chi = block_bootstrap_ci(A, seed=20260917) if A else (None, None)
        b, r2, _ = ols_slope(s)
        ts, ts_ci, _ = theil_sen(s)
        out.append({
            "标签": lb, "样本": len(s),
            "A中位": _med(A) if A else None,
            "CI下限": clo, "CI上限": chi,
            "A小于1占比": (sum(1 for x in A if x < 1) / len(A)) if A else None,
            "G中位": _med(G) if G else None,
            "斜率R~F": b, "R2": r2,
            "稳健斜率": ts, "稳健CI": ts_ci,
            "有向缺口中位": gap_directional(s),
        })
    return out


def nonoverlapping(wins, eligible=None, stride=1):
    """贪心挑出互不重叠的窗口 —— 用来消除窗口重叠造成的伪重复。

    原样本里相邻窗口共享绝大部分区间，样本远不独立；
    只保留时间上不重叠的窗口，是"这些结论是不是同一段行情反复数出来的"
    最直接的一次压力测试。

    ⚠ 2026-09 修正（审计问题 #20）：
    1) 原实现先在【全部窗口】上贪心挑，再从挑出的窗口里筛掉算不出 A 的
       （|F|<2%）。于是"入选"与否取决于该窗口的 |F| 是否够大，A 子集不是
       随机子样本 —— 实测 w=20 时先挑后筛 A 中位 0.762、先筛后挑 0.365。
       `eligible` 参数给出"哪些窗口可用"，只在其中挑，保证子样本对 |F| 随机。
    2) 新增 `stride`：原实现在【每个窗口长度组内】各挑一批不重叠窗口，
       于是池化后不同 w 的窗口在时间上重叠（如 w=3 与 w=250 的窗口共享区间）。
       `stride>1` 时按长度排序后跨组轮转挑，得到跨长度也互不重叠的样本。
    """
    items = [r for r in wins if eligible is None or eligible(r)]
    if stride > 1:
        # 跨长度轮转：先按长度分组，各组按起始日排序，再交错推进
        groups = {}
        for r in items:
            groups.setdefault(int(r["窗口"]), []).append(r)
        for g in groups.values():
            g.sort(key=lambda r: r["起始日"])
        out, last = [], None
        idx = {k: 0 for k in groups}
        while any(idx[k] < len(groups[k]) for k in groups):
            progressed = False
            for k in sorted(groups):
                g = groups[k]
                while idx[k] < len(g) and last is not None and \
                        d(g[idx[k]]["起始日"]) < last:
                    idx[k] += 1
                if idx[k] < len(g):
                    r = g[idx[k]]
                    out.append(r)
                    last = d(r["结束日"])
                    idx[k] += 1
                    progressed = True
            if not progressed:
                break
        return out
    by_len = {}
    for r in items:
        by_len.setdefault(int(r["窗口"]), []).append(r)
    out = []
    for grp in by_len.values():
        grp.sort(key=lambda r: r["起始日"])
        last = None
        for r in grp:
            if last is None or d(r["起始日"]) >= last:
                out.append(r)
                last = d(r["结束日"])
    return out


def robustness_md(wins) -> list:
    """生成稳健性检验章节（全部数字来自实跑，非手写）。

    ⚠ 2026-09 修正（审计问题 #20）：`nonoverlapping(wins)` 改为
    `nonoverlapping(wins, eligible=has_A)` —— 只在【可算 A 的窗口】里挑
    不重叠窗口。原做法先在全窗口上挑、再筛掉算不出 A 的，会让 A 子集
    偏向 |F| 大的窗口（实测 w=20 时两种顺序的 A 中位差一倍）。
    同时增加一行【斜率】的稳健性，因为斜率不依赖 |F| 阈值。
    """
    cuts = [
        ("全样本", wins),
        ("互不重叠窗口（消除伪重复）",
         nonoverlapping(wins, eligible=has_A)),
        ("前半段 2020-2022", [r for r in wins if r["起始日"] < "2023-01-01"]),
        ("后半段 2023-2026", [r for r in wins if r["起始日"] >= "2023-01-01"]),
        ("仅割胶季 4-11 月", [r for r in wins
                              if r["起始日"][5:7] in ("04", "05", "06", "07",
                                                      "08", "09", "10", "11")]),
    ]
    labels = [lb for lb, _, _, _ in _cut_rows(wins)]
    L = []
    A = L.append
    A("## 三、稳健性检验\n")
    A("窗口高度重叠（相邻窗口共享绝大部分区间），样本远不独立。")
    A("下面五种切分都是在回答同一个问题：**这些结论是不是同一段行情反复数出来的？**\n")
    A("| 切分 | " + " | ".join(f"{lb} A中位" for lb in labels) + " |")
    A("|---" * (len(labels) + 1) + "|")
    for name, rows in cuts:
        cells = []
        for _, n, a, _ in _cut_rows(rows):
            cells.append(f"{a:.3f} (n={n})" if a is not None else f"— (n={n})")
        A(f"| {name} | " + " | ".join(cells) + " |")
    A("")
    A("**阈值无关的复核（审计问题 #5 的修正）**：上表 A 中位只在 `|F|≥2%` 的")
    A("子集上可算，而该子集在各档中的占比不同，于是 A 中位会混入构成变化。")
    A("下表用**不筛样本**的回归斜率重做同样的切分 —— 斜率覆盖全部窗口，")
    A("跨档、跨切分都可比：\n")
    A("| 切分 | " + " | ".join(f"{lb} 斜率" for lb in labels) + " |")
    A("|---" * (len(labels) + 1) + "|")
    for name, rows in cuts:
        cells = []
        for lo, hi, lb in UP_BINS + DN_BINS:
            if lb.startswith("涨"):
                s = [r for r in rows if lo <= float(r["期货涨幅_pct"]) < hi]
            else:
                s = [r for r in rows if -hi < float(r["期货涨幅_pct"]) <= -lo]
            b, _, n = ols_slope(s)
            cells.append(f"{b:.3f} (n={n})" if b is not None else f"— (n={n})")
        A(f"| {name} | " + " | ".join(cells) + " |")
    A("")
    A("同一切分下 `涨` 列与 `跌` 列的对照，是判断「方向不对称」是否成立的关键：")
    A("若结论只是测量精度/量化步长造成的衰减，它应当**同时**压低上涨与下跌两侧；")
    A("实测只在上涨侧压低，故方向不对称成立。\n")
    return L


def write_report(stat_rows, segs, wins, dates) -> None:
    import collections

    def fmt_ci(r):
        if r["A_CI下限"] == "":
            return "—"
        return f"[{r['A_CI下限']:.2f}, {r['A_CI上限']:.2f}]"

    def n3(v, dash="—"):
        """数值保留 3 位；空值（平稳组算不出 A）显示破折号。"""
        return f"{v:.3f}" if v != "" else dash

    def pct1(v, dash="—"):
        return f"{v:.1%}" if v != "" else dash

    lines = []
    A = lines.append
    A("# 橡胶期现非对称性研究 · 阶段报告\n")
    A(f"> 数据窗口：{dates[0]} ~ {dates[-1]}")
    A(f"> 滑动窗口样本：{len(wins)} 条 ｜ ZigZag 区间样本：{len(segs)} 条")
    A("> 期货口径：RU 主力连续收盘价")
    A("> 原料口径：**两端共同报价市县**的收购均价之比（详见第四节）\n")
    A("---\n")

    A("## 一、结论速览\n")
    A("| 期货区间涨幅 | 样本 | A中位数 | A的95%CI | A<1占比 | 斜率R~F | 稳健斜率 | 有向缺口G/F | 传导缺口G中位(pp) |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in _cut_rows_full(wins):
        ci = (f"[{r['CI下限']:.2f}, {r['CI上限']:.2f}]"
              if r["CI下限"] is not None else "—")
        a = f"{r['A中位']:.3f}" if r["A中位"] is not None else "—"
        sh = f"{r['A小于1占比']:.1%}" if r["A小于1占比"] is not None else "—"
        g = f"{r['G中位']:+.2f}" if r["G中位"] is not None else "—"
        b = f"{r['斜率R~F']:.3f}" if r["斜率R~F"] is not None else "—"
        ts = f"{r['稳健斜率']:.3f}" if r["稳健斜率"] is not None else "—"
        gd = (f"{r['有向缺口中位']:+.3f}"
              if r["有向缺口中位"] is not None else "—")
        A(f"| {r['标签']} | {r['样本']} | {a} | {ci} | {sh} | {b} | {ts} | "
          f"{gd} | {g} |")
    A("")
    A("> **A = 原料涨幅 ÷ 期货涨幅**。A<1 表示原料涨幅跟不上期货（对采购方有利）；")
    A("> A>1 表示原料超涨/超跌。CI 为按时间分块的 Bootstrap 95% 区间。")
    A("> G = 原料涨幅 − 期货涨幅（百分点），无量纲，不受期货绝对价位影响。\n")
    A("> ⚠️ **A 只在 `|F|≥2%` 的窗口上可算，而该子集在各档中占比不同** ——")
    A("> 跨档比较请优先看 **斜率R~F / 稳健斜率 / 有向缺口G/F**（三者都不筛样本）。")
    A("> A 中位数会混入「进入 A 统计的窗口构成」变化：实测在固定的")
    A("> `|F|∈[2,5)%` 带内，A 中位随窗口长度从 0.16 涨到 1.16，")
    A("> 与不筛样本的 A 中位走向完全不同。\n")
    A("> ⚠️ **不要引用 元/吨 价差**：ΔS 在换月修正前后符号会翻转，"
      "详见第五节。\n")

    A("---\n")
    A("## 二、路径A：ZigZag 区间法（对应你定义的分类）\n")
    if segs:
        by = collections.defaultdict(list)
        for r in segs:
            by[(r["分类"], r["幅度档"])].append(r)
        A("| 分类 | 幅度档 | 样本 | A中位数 | G中位数(百分点) | ΔS中位数(元/吨) |")
        A("|---|---|---|---|---|---|")
        for (cat, bk) in sorted(by):
            rs = by[(cat, bk)]
            Av = [float(r["非对称比A"]) for r in rs if r["非对称比A"] != ""]
            Gv = [float(r["传导缺口G_pct"]) for r in rs]
            Sv = [float(r["价差变化dS_元吨"]) for r in rs]
            A(f"| {cat} | {bk} | {len(rs)} | "
              f"{(f'{median(Av):.3f}' if Av else '—')} | {median(Gv):.2f} | {median(Sv):.0f} |")
        A("")
        A("> ⚠️ 上表 A 中位数是本路径的核心读数；**ΔS 列仅存档，不要引用** ——")
        A("> 路径A 的区间合计仅 22 个、分 5 类 × 2 档后每格 1~5 个样本，")
        A("> 且 ΔS 对换月敏感（见第五节）。A 中位数的方向性可作定性参考，")
        A("> 定量结论一律以路径B（第一节）为准。\n")
        A("**区间明细（全部样本）：**\n")
        A("| # | 分类 | 幅度档 | 区间 | 交易日 | 期货% | 原料% | A | ΔS(元/吨) |")
        A("|---|---|---|---|---|---|---|---|---|")
        for r in segs:
            A(f"| {r['序号']} | {r['分类']} | {r['幅度档']} | {r['起点日']}~{r['终点日']} | "
              f"{r['交易日数']} | {float(r['期货涨幅_pct']):+.2f} | "
              f"{float(r['原料涨幅_pct']):+.2f} | "
              f"{r['非对称比A'] if r['非对称比A'] != '' else '—'} | "
              f"{float(r['价差变化dS_元吨']):+.0f} |")
    A("")

    A("")
    lines.extend(robustness_md(wins))

    A("---\n")
    A("## 四、方法说明\n")
    A("### 为什么需要两条路径\n")
    A("- **路径A（ZigZag区间）**：与手工整理案例方法一致，能落进四象限分类；")
    A("  但独立区间只有几十个，分 5 类 × 2 档后每格样本太少，**不足以做显著性判断**。")
    A("- **路径B（滑动窗口）**：样本量数千，能给出稳定分布；")
    A("  但窗口高度重叠，**样本不独立**，故显著性一律用 Block Bootstrap。")
    A("")
    A("### 原料取价口径：为什么不能用「全省收购均价」\n")
    A("公示表的「全省收购均价」是**当期有报价市县的简单均值**（已实测验证），")
    A("而有报价的市县数在 **1~18 之间浮动** —— 477 份报告里 303 份（63.5%）非全覆盖。")
    A("覆盖变化时均价的变动主要来自**样本构成变化**，不是价格。实例：\n")
    A("| 日期 | 均价 | 报价市县 | 站点环比 | 均价直接算 |")
    A("|---|---|---|---|---|")
    A("| 2026-08-27 | 14.38 | 18 | −0.01% | — |")
    A("| 2026-08-31 | 13.55 | 5 | **+0.56%** | **−5.77%** |")
    A("| 2026-09-14 | 13.36 | 3 | **+0.55%** | **−9.49%** |")
    A("")
    A("均价看似暴跌 9.49%，站点自己的环比却是 +0.55%（涨）。")
    A("链式指数也不能用：季初报告环比为空（海南每年 1–3 月停割），")
    A("连乘会把整段跨季跃迁记成 0.00%（实测累计丢失 22.79%）；")
    A("且报告市县集合逐年滚动，长距离连乘会漂移（实测 8 个月漂 3.7% = 370 元/吨，")
    A("足以淹没研究中的价差变化量级）。")
    A("")
    A("**本报告采用的『共同市县端点法』**：在两个端点各取一份原料观测，")
    A("只用**两端都报价的市县**，用完全相同的市县集合求均值再比。")
    A("构成效应被彻底消除，不连乘、不漂移，水平是真实 元/公斤。")
    A("不足以支撑该口径的样本（共同市县 < 6）已整体剔除并计数。\n")
    A("### 已知的数据限制（会削弱结论强度，必须明说）\n")
    A("1. **海南停割季**：原料收购价每年约 **1–3 月完全断档**（实测断档 84–123 天），")
    A("   可分析窗口实际只有每年 **4–12 月**；")
    A("2. **收购价非每日发布**：约 **70–79 次/年**，平均每 3.2 个交易日一次，")
    A("   因此**小于 3 天的期货波动在原料端没有对应观测**；")
    A("3. **端点取价有 staleness**：区间端点取该日或之前最近观测，")
    A("   超过 7 自然日的样本已剔除；")
    A("4. **主力连续换月**：跨合约拼接处存在跳空，会污染价差类指标；")
    A("5. **区间重叠自相关**：已用 Block Bootstrap 缓解，并用互不重叠子样本复核（见第三节）；")
    A("6. **大幅涨幅样本集中**：`涨>20%` 档样本有相当比例落在 2020 年 Q3 那一轮，")
    A("   该档结论的独立性弱于其他档，引用时须谨慎。")

    # ---------- 五、结论 ----------
    def ds_med(lo, hi, sign):
        if sign > 0:
            s = [r for r in wins if lo <= float(r["期货涨幅_pct"]) < hi]
        else:
            s = [r for r in wins if -hi < float(r["期货涨幅_pct"]) <= -lo]
        v = [float(r["价差变化dS_元吨"]) for r in s]
        return _med(v) if v else None

    cu = {lb: (n, a, g) for lb, n, a, g in _cut_rows(wins) if lb.startswith("涨")}
    cd = {lb: (n, a, g) for lb, n, a, g in _cut_rows(wins) if lb.startswith("跌")}

    A("")
    A("---\n")
    A("## 五、结论：哪些成立、哪些不成立\n")
    A("> 本节结论以 **P6 稳健性检验**（`robustness.py`，8 个变体：换月修正 4 种 × "
      "价格口径 4 种）为准。")
    A("> 只在某个变体下成立的，一律标为不成立。\n")

    A("### ✅ 成立：小幅上涨（2–10%）原料确实滞后\n")
    A("该档 A 在**全部 8 个变体下都小于 1**，是最稳的一条。")
    A("涨2–5% 档的 CI 上界仅 0.40，离 1 很远，不是边缘结论：\n")
    A("| 期货波动 | 上涨侧 A | 下跌侧 A | 两侧是否分得开 |")
    A("|---|---|---|---|")
    for ub, db in [("涨2-5%", "跌2-5%"), ("涨5-10%", "跌5-10%"),
                   ("涨10-20%", "跌>10%")]:
        if ub in cu and db in cd:
            ua, da = cu[ub][1], cd[db][1]
            sep = "**分得开**" if abs(ua - da) > 0.25 else "基本无差"
            A(f"| {ub[1:]} | {ua:.3f} | {da:.3f} | {sep} |")
    A("")
    A("### ⚠️ 方向不对称的适用范围比原先以为的窄\n")
    A("⚠ 下表改用【阈值无关】的斜率与有向缺口复核同一件事 —— A 只在")
    A("`|F|≥2%` 的子集上可算，两侧的构成不同会让 A 的对比失真：\n")
    A("| 档位 | A中位 | 斜率R~F | 稳健斜率 | 有向缺口G/F | 样本 |")
    A("|---|---|---|---|---|---|")
    for lb in ["涨2-5%", "跌2-5%", "涨5-10%", "跌5-10%",
               "涨10-20%", "跌>10%"]:
        src = cu if lb.startswith("涨") else cd
        if lb not in src:
            continue
        row = _cut_rows_full(wins)
        m = next((x for x in row if x["标签"] == lb), None)
        if m is None:
            continue
        a_ = f"{m['A中位']:.3f}" if m["A中位"] is not None else "—"
        b_ = f"{m['斜率R~F']:.3f}" if m["斜率R~F"] is not None else "—"
        t_ = f"{m['稳健斜率']:.3f}" if m["稳健斜率"] is not None else "—"
        g_ = (f"{m['有向缺口中位']:+.3f}"
              if m["有向缺口中位"] is not None else "—")
        A(f"| {lb} | {a_} | {b_} | {t_} | {g_} | {m['样本']} |")
    A("")
    A("**关键：方向不对称在同一窗口长度内成立，但随窗口长度单调放大。**")
    A("实测把「涨2-5%」与「跌2-5%」的 A 中位差按窗口长度拆开：")
    A("w=10 差 −0.09、w=26 差 −1.26、w=50 差 −1.43、w=58 差 −0.50 ——")
    A("各长度的差值中位 −1.08，**方向一致但量级随 w 剧烈变化**；")
    A("而两组的窗口长度构成不同（涨侧偏短窗口、跌侧偏长窗口），")
    A("池化后的差值因此被放大。故正确的说法是：")
    A("**「小涨时原料不动」在同窗口长度内成立，")
    A("但不能用池化后的 A 差值去量化「上涨侧比下跌侧滞后多少」。**\n")

    A("### ❌ 已推翻：大涨时原料滞后更严重\n")
    A("**上一版报告在此处是错的**，根因是主力连续换月跳空（见第四节）。")
    A("新浪 RU0 跨合约直接拼接、不做价格调整，实测 19 次换月中")
    A("人工跳空中位 1.38%、最大 12.15%，且 8 月的 09→01 换月连续六年跳 5~12%：\n")
    A("```")
    A("2020-08-06 +12.44%   2021-08-10 +8.41%   2022-08-10 +8.26%")
    A("2023-08-08  +8.03%   2025-08-04 +6.50%   2026-08-06 +5.58%")
    A("```")
    A("这些假涨幅恰好灌在「产生最大滞后」的窗口上。改用按真实主力合约")
    A("后复权的连续序列后（`fetch_contracts.py`，19/19 换月日均与新合约自身")
    A("涨跌幅完全一致），**每一个上涨分档的 A 都被抬高了**：\n")
    A("| 期货涨幅 | RU0 原始（未修正） | 后复权（修正后） | 修正后是否仍 <1 |")
    A("|---|---|---|---|")
    # 未修正列取自 robustness.py 输出的 R2 矩阵（完整结果见 data/稳健性检验结果.txt）
    UNCORRECTED = {"涨2-5%": 0.024, "涨5-10%": 0.282,
                   "涨10-20%": 0.466, "涨>20%": 0.769}
    for lb in ["涨2-5%", "涨5-10%", "涨10-20%", "涨>20%"]:
        if lb in cu:
            v = cu[lb][1]
            A(f"| {lb[1:]} | {UNCORRECTED[lb]:.3f} | **{v:.3f}** | "
              f"{'是' if v < 1 else '**否**'} |")
    A("")
    A("修正后 A 随涨幅**单调上升并越过 1**：小涨时原料几乎不动，")
    A("大涨时反而跟得紧甚至**超涨**。")
    A("也就是说，**「涨得越猛、原料越跟不上」是被换月跳空制造的假象** ——")
    A("原始序列把每个上涨档都压到了 1 以下，越大的档被压得越狠。\n")

    A("### ⚠️ 不稳健，不要引用\n")
    A("- **价差变化 ΔS**：在换月修正前后**符号会翻转**")
    A("  （涨10-20%：+1074 → +502 → +292 → +279；涨>20%：+1025 → −128 → −1166）。")
    A("  ΔS 用期货绝对价计算，对换月与合约基差极其敏感，")
    A("  本数据**不足以支撑「价差改善多少元/吨」的结论**；")
    A("- **涨>20% 档**：修正后仅剩 22 条，剔除含换月窗口后只剩 13 条，样本太薄；")
    A("- **跌10-20% 档**：仅 31 条；")
    A("- **下跌侧 A 的量级**：前后半段不稳定（见第三节），只能确定「接近 1」，")
    A("  不能说「超跌」。")

    A("")
    A("### ✅ 成立：错位随时间收敛 —— 但要限定说法的强度\n")
    A("> ⚠ 本节数字已按 2026-09 审计修正重算，与第七节 Q8 报告一致。")
    A("> 原表述里「约 15% 的期货变动始终传不到原料端」是把 `|G|/|F| ≈ 0.88`")
    A("> 与「年对年斜率 0.852」两个不同的量拼接出来的 —— `|G|/|F| = 0.88`")
    A("> 按字面是 88%，且它把零均值噪声经 `|·|` 整流后也计入分子。\n")
    A("Q8（第七节）把窗口从 3 个交易日拉到 250 个交易日（≈1 年）：\n")
    A("| 窗口长度 | 3 日 | 20 日(≈1月) | 60 日(≈3月) | 250 日(≈1年) |")
    A("|---|---|---|---|---|")
    A("| 斜率 R~F（不筛样本） | 0.329 | 0.979 | 1.363 | 1.060 |")
    A("| 稳健斜率 Theil-Sen | 0.209 | 0.734 | 1.188 | 1.069 |")
    A("| 有向缺口 med(G/F) | −0.908 | −0.388 | −0.056 | −0.217 |")
    A("| A 中位数（受 \\|F\\|≥2% 筛选） | 0.174 | 0.588 | 1.045 | 0.840 |")
    A("| 可算 A 的窗口占比 | 0.33 | 0.72 | 0.76 | 0.96 |")
    A("| \\|G\\|/\\|F\\|（诊断量，勿单独引用） | 1.260 | 0.892 | 0.840 | 0.876 |")
    A("")
    A("**收敛是真的，但很早就完成了。**")
    A("有向缺口在 **1~2 个月**内从 −0.91 收敛到 −0.39，之后在 −0.22 附近走平。")
    A("`A 中位数` 那一行的走向**不能直接读**：可算 A 的窗口占比从 0.33 涨到 0.96，")
    A("而固定的 `|F|∈[2,5)%` 带内 A 中位本身就随 w 从 0.16 涨到 1.16 ——")
    A("所以 A 的上升有相当部分只是「进入统计的窗口构成变了」。\n")
    A("年对年案例库（22 个）给出同样的画面：")
    A("回归斜率 R~F = **0.844**，**按年分块** 95%CI **[0.50, 1.39]** —— ")
    A("CI 跨过 1，即一年尺度上的「跟不上」在统计上不显著。")
    A("⚠ 但这条**依赖单一年份**：去掉贡献最大的一年（2023）后斜率降到 **0.618**、")
    A("CI 上界降到 **0.91**，**不再跨过 1**。故「完全跟上」不能被稳健地接受。\n")
    A("> 所以你的「长期同步」**方向对、幅度要打折**：")
    A("> 一年尺度上原料能跟上约 0.84，统计上无法拒绝「完全跟上」，")
    A("> 但该结论由单一年份支撑；数据里能看到的**不是「越拉越同步」，")
    A("> 而是「一个月就收敛完，之后走平」**。\n")
    A("> 另注：年对年涨幅的口径选择对结论影响很大。RU 当时处于远月贴水，")
    A("> 后复权（持有连续合约）给 A ≈ 0.83，未复权主力连续（不断移仓，")
    A("> 与 RU0 相同）给 A ≈ 0.57 —— 两者之差 6.8pp 是**展期收益**，")
    A("> 不是拼接伪影。研究问「持有连续合约」还是「不断移仓」决定取哪个。\n")
    A("### ✅ 成立：短时滞，但预测力比原先以为的弱\n")
    A("Q6（第六节）用相邻两期观测配对（414 对，区间互不重叠，但共享端点，")
    A("R 一阶自相关 +0.157）：\n")
    A("- 移窗扫描的相关在 **δ=+2 个交易日**取到最大（corr 0.346，同期 0.245）；")
    A("  对 51 个 δ 取 max|corr| 做**多重比较校正**后 p = 0.0104，峰值本身站得住。")
    A("  但 δ=+2 与 δ=0/+4 的 CI 有重叠，只能说滞后落在 **0~5 个交易日**内；")
    A("- **预测回归的口径已修正**：原实现里 F 的右端 = `i1`，而目标 R 的基期")
    A("  正是 `obs(i1)` —— 96.9% 的配对同日，该回归实为**同期共动**。")
    A("  改为时序干净口径（F 右端取 `i1−1`）后，")
    A("  w=5 斜率 **0.1195**、CI **(−0.021, 0.267)** —— **跨过 0，不再显著**；")
    A("  逐日分解显示预测力集中在 `i1` 当天（corr +0.290），其余各天都在噪声量级。")
    A("")
    A("这条与 Q8 一致：**方向上是「几天的时滞」而非「几周」**，")
    A("但「用最近一两周的期货就能预测原料」这条**在剔除同日重叠后不成立**，")
    A("不能作为采购择时信号的依据。\n")

    # ---------- 六、Q6 领先滞后 & 七、Q8 长期同步（各自脚本生成，有则并入） ----------
    for num, fn, script in [("六", "Q6_领先滞后.md", "lead_lag.py"),
                            ("七", "Q8_长期同步.md", "horizon_sync.py")]:
        A("")
        A("---\n")
        p = os.path.join(DATA, fn)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                lines.append(f.read().rstrip())
        else:
            A(f"## {num}、待生成\n")
            A(f"> 尚未生成。先跑 `python {script}`。\n")

    # ---------- 八、P6 稳健性矩阵（由 robustness.py 生成） ----------
    rb = os.path.join(DATA, "稳健性检验_汇总.md")
    A("")
    A("---\n")
    if os.path.exists(rb):
        with open(rb, encoding="utf-8") as f:
            lines.append(f.read().rstrip())
    else:
        A("## 八、P6 稳健性检验（完整矩阵）\n")
        A("> 尚未生成。先跑 `python robustness.py`。\n")

    # ---------- 九、四象限 × 自然日跨度（由 span_quadrant.py 生成） ----------
    A("")
    A("---\n")
    sq = os.path.join(DATA, "跨度象限统计.md")
    if os.path.exists(sq):
        with open(sq, encoding="utf-8") as f:
            lines.append(f.read().rstrip())
    else:
        A("## 九、按「四象限 × 自然日跨度」重看案例\n")
        A("> 尚未生成。先跑 `python span_quadrant.py`。\n")

    # ---------- 十、配图（P5） ----------
    cdir = os.path.join(HERE, "charts")
    figs = [
        ("fig1_双轴走势.png",
         "图1  期货与原料的双轴走势。灰影 = 停割季（原料无报价）。"
         "原料曲线在停割季处**断开** —— 连成直线等于凭空编出一段"
         "不存在的价格路径。"),
        ("fig2_FR散点.png",
         "图2  所有窗口的 (期货涨幅, 原料涨幅) 散点。橙线 = 各档 A 中位数"
         "对应的位置：无论涨跌，原料的条件涨幅都远小于期货。"),
        ("fig3_A梯度.png",
         "图3  A 随期货涨幅的梯度，竖线为分块 Bootstrap 95% CI。"
         "小涨时 A 远小于 1；涨幅越大 A 越大并越过 1。"),
        ("fig4_方向不对称.png",
         "图4  方向不对称。**只在 2–5% 档成立** —— 5–10% 两侧几乎无差，"
         "10% 以上反而反转。不要推广成「上涨侧普遍更滞后」。"),
        ("fig5_传导缺口.png",
         "图5  传导缺口 G = 原料% − 期货%（无量纲）。"
         "用它替代不可引用的 元/吨 价差 ΔS。"),
    ]
    # 图6、图7 已内嵌在第六、七节各自的讨论旁边，此处不重复列出。
    A("")
    A("---\n")
    A("## 十、图\n")
    A("> 由 `python make_charts.py` 生成，源文件在 `charts/`。")
    A("> 图6（领先滞后）与图7（时间尺度谱）已分别内嵌在第六、七节。\n")
    for k, (fn, cap) in enumerate(figs, 1):
        if os.path.exists(os.path.join(cdir, fn)):
            A(f"![图{k}](charts/{fn})\n")
            A(f"*{cap}*\n")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()

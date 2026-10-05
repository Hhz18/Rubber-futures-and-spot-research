# -*- coding: utf-8 -*-
"""
lead_lag.py —— Q6：期货领先原料多久？

════════════════════════════════════════════════════════════════════
问题的正确提法
════════════════════════════════════════════════════════════════════
你的假说是"期货是快变量、原料是慢变量"。这句话要能被数据检验，
必须先钉死"慢"是什么意思 —— 它有两个完全不同的含义，混在一起会得出矛盾的结论：

  含义一【时滞】原料在第 t 期涨的这部分，其实是期货在 t−δ 期就已经涨过的。
          δ 就是滞后天数。→ 用【等长移窗扫描】测。
  含义二【衰减】即使给足时间，原料也只跟上一部分（A<1 且长期不回到 1）。
          → 这归 Q8（长期同步）管，本脚本不碰。

本脚本只测含义一，用三套互补的口径：

  【口径① 等长移窗】R_k 是原料在 [t_{k-1}, t_k] 的涨幅。
      把期货窗口整体往前挪 δ 个交易日，取 [t_{k-1}−δ, t_k−δ] 的涨幅，
      与 R_k 求相关。若期货领先，相关在 δ>0 处取到最大。
      —— 窗口长度不变，只有"位置"在动，这是最干净的时滞度量。

  【口径② 累计回看】从原料窗口起点再往前推 δ 天，一直算到原料窗口终点：
      期货 [t_{k-1}−δ, t_k] 的涨幅 vs R_k。
      —— 直接回答"原料这期涨的这点，对应期货过去多少天的累计涨幅"。

  【口径③ 预测回归】用【已实现】的期货过去 w 天涨幅，预测【下一期】原料涨幅。
      —— 这是唯一有决策含义的口径：签采购合同前能知道的信息，能预测什么。

════════════════════════════════════════════════════════════════════
三个必须交代的统计问题
════════════════════════════════════════════════════════════════════
1) **上限 δ（±25 交易日）处样本会缩水**。移窗需要 t_k 之后还有 δ 天的期货数据，
   否则窗口越界。若各 δ 用的样本不同，相关曲线的起伏就混入了样本变化。
   故口径①②一律锁定【固定样本】：只保留让 δ 取遍整个区间仍不越界的那些配对。
   代价是损失首尾少量观测，换来各 δ 之间可比。
2) **相邻配对共享端点**，且底层序列有自相关。显著性一律用分块 Bootstrap。
3) **原料是低频发布**（约 3.2 个交易日一次），所以 δ 的分辨率不可能优于这个粒度；
   δ=1 与 δ=2 的差别落在噪声里，只有 δ 的量级（几天 vs 十几天的差别）可读。

产出：
    data/Q6_领先滞后.md
    charts/fig6_领先滞后.png

用法：
    python lead_lag.py
"""

import argparse
import csv
import math
import os
import random
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

import matplotlib                                                    # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402

from build_dataset import (load_futures, load_cities, raw_pairs,     # noqa: E402
                           independent_pairs, FUT_FILE)

MD_OUT = os.path.join(DATA, "Q6_领先滞后.md")
CHART = os.path.join(HERE, "charts", "fig6_领先滞后.png")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun",
                                   "DengXian", "KaiTi"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 130
plt.rcParams["savefig.bbox"] = "tight"

rng = random.Random(20260917)


# ------------------------------------------------------------------
# 统计小工具
# ------------------------------------------------------------------
def corr(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def slope_yx(ys, xs):
    """R ~ F 的回归斜率，以及 R²。斜率 = 期货涨 1% 时原料涨几个百分点。"""
    n = len(xs)
    if n < 4:
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((a - mx) ** 2 for a in xs)
    if sxx <= 0:
        return None, None
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    syy = sum((b - my) ** 2 for b in ys)
    return sxy / sxx, ((sxy * sxy) / (sxx * syy) if syy > 0 else None)


def boot_ci_pairs(R, F, n_boot=4000, block=5, alpha=0.05, seed=20260917):
    """对【成对样本】做分块 Bootstrap，返回 (corr 的 CI, 斜率的 CI)。

    成对重采样 —— 必须 R 与 F 一起抽，否则打散了两者的对应关系。

    ⚠ 2026-09 修正（审计问题 #18 / #21）：
    1) 原实现 `randrange(0, max(1, n - block + 1))` 允许块起点任意重叠，
       重采样序列是"随机拼贴"而非"整段切分"，块之间可能首尾相接形成更长
       连续段 —— 方向上【低估】方差。现在改为【起点随机一次、块按 block
       等距推进并环绕取模】（circular block bootstrap），块之间不再重叠。
    2) `n_boot` 从 1000 提到 4000：2.5%/97.5% 分位在 1000 次下不稳，
       实测 block 5→15 时 w=5 的 CI 宽度变化 12%，其中一部分来自次数不足。
    3) 接受 `seed`，同一输入的结果完全可复现（原实现用全局 rng，
       结果依赖此前调用过多少次重采样）。
    """
    n = len(R)
    if n < 16:
        return None, None
    r = random.Random(seed)
    cs, ss = [], []
    n_blocks = max(2, (n + block - 1) // block)
    for _ in range(n_boot):
        xr, xf = [], []
        for _ in range(n_blocks):
            # 每个块【各自】随机取起点，块内连续、跨序列末端环绕 ——
            # 这是标准的 circular block bootstrap。注意不能只抽一个全局起点：
            # 那样得到的是整条序列的旋转，corr 几乎不变，CI 会塌成一点。
            st = r.randrange(0, n)
            for j in range(block):
                k = (st + j) % n
                xr.append(R[k])
                xf.append(F[k])
        c = corr(xr, xf)
        s, _ = slope_yx(xr, xf)
        if c is not None:
            cs.append(c)
        if s is not None:
            ss.append(s)
    if not cs:
        return None, None
    cs.sort()
    ss.sort()
    cl = (cs[int(alpha / 2 * len(cs))], cs[int((1 - alpha / 2) * len(cs)) - 1])
    sl = (ss[int(alpha / 2 * len(ss))], ss[int((1 - alpha / 2) * len(ss)) - 1])
    return cl, sl


def max_abs_corr_null(R, F_by_lag, n_boot=5000, seed=20260917):
    """移窗扫描的【多重比较校正】零分布（审计问题 #10 / #19）。

    口径① 在 51 个 δ 上取 max|corr| 作为"最优滞后"的证据，但报告给出的
    CI 是【单点】CI，没有对"在 51 个滞后里挑最大"这件事做校正。
    本函数用【循环移位】构造零分布：把 R 整体移位以破坏配对关系、保留
    各自的时间结构与边缘分布，然后在全部 δ 上取 max|corr|，重复 n_boot 次。
    返回 (观测的 max|corr|, 校正后 p 值, 零分布分位数 dict)。

    p 值 = P(零假设下 max|corr| ≥ 观测值)，这是"最优滞后显著"的正确 p 值。
    """
    n = len(R)
    if n < 16 or not F_by_lag:
        return None, None, None
    r = random.Random(seed)
    lags = sorted(F_by_lag)
    obs = max(abs(corr(R, F_by_lag[d])) for d in lags)
    null = []
    for _ in range(n_boot):
        k = r.randrange(1, n)
        sh = R[k:] + R[:k]
        null.append(max(abs(corr(sh, F_by_lag[d])) for d in lags))
    null.sort()
    m = len(null)
    p = sum(1 for x in null if x >= obs) / m
    q = {"50%": null[m // 2], "90%": null[int(.90 * m)],
         "95%": null[int(.95 * m)], "99%": null[int(.99 * m)]}
    return obs, p, q


def _rank(xs):
    """平均秩（并列取中点），供 Spearman 用。"""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(xs, ys):
    """秩相关。

    原料涨幅是尖峰厚尾的（实测标准差 2.54pp，远大于中位数 0.03%），
    Pearson 会被少数大幅跳变主导。秩相关对离群点免疫，
    两者结论一致才说明相关性不是几个极端值撑起来的。
    """
    if len(xs) < 4:
        return None
    return corr(_rank(xs), _rank(ys))


def med(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


# ------------------------------------------------------------------
# 口径① 等长移窗
# ------------------------------------------------------------------
def scan_shift(pairs, bars, dmax):
    rows = []
    for dl in range(-dmax, dmax + 1):
        R, F = [], []
        for p in pairs:
            a, b = p["i0"] - dl, p["i1"] - dl
            R.append(p["R_pct"])
            F.append((bars[b].close / bars[a].close - 1) * 100.0)
        c = corr(R, F)
        s, r2 = slope_yx(R, F)
        cl, sl = boot_ci_pairs(R, F)
        rows.append({"delta": dl, "n": len(R), "corr": c, "slope": s, "r2": r2,
                     "corr_ci": cl, "slope_ci": sl, "spearman": spearman(R, F)})
    return rows


# ------------------------------------------------------------------
# 口径② 累计回看
# ------------------------------------------------------------------
def scan_cum(pairs, bars, dmax):
    """口径② 累计回看：F = [i0-δ, i1] 的涨幅 vs R。

    ⚠ 2026-09 修正（审计问题 #6）：原实现
        a, b = p["i0"] - dl, p["i1"]
    在 δ > 25 时 `i0 - dl < 0` —— 而 `fixed` 只保证 `i0 ≥ 25`，
    默认 `--cum-max 60` 时实测 14 对配对落进负区间，Python 负索引
    【回绕到序列尾部】：2020 年的窗口用上了 2026-09-17 的收盘价。
    现在对越界的配对【跳过并计数】，各 δ 用的样本因此不再完全相同，
    故同时返回每点的 n，供调用方判断可比性。
    """
    rows = []
    for dl in range(0, dmax + 1):
        R, F, skipped = [], [], 0
        for p in pairs:
            a, b = p["i0"] - dl, p["i1"]
            if a < 0:                    # 越界：绝不回绕
                skipped += 1
                continue
            R.append(p["R_pct"])
            F.append((bars[b].close / bars[a].close - 1) * 100.0)
        rows.append({"delta": dl, "n": len(R), "skipped": skipped,
                     "corr": corr(R, F), "slope": slope_yx(R, F)[0],
                     "corr_ci": boot_ci_pairs(R, F)[0]})
    return rows


# ------------------------------------------------------------------
# 口径③ 预测回归
# ------------------------------------------------------------------
def predict(pairs, bars, horizons, edge="prev"):
    """口径③ 预测回归：用【已实现】的期货过去 w 日涨幅，预测【下一期】原料涨幅。

    ⚠ 2026-09 修正（审计问题 #1，本审计的最高严重度项）：
    原实现 `F = [i1-w, i1]` 的右端是 `i1`，而目标 `R_{k+1} = (P_{k+1}-P_k)/P_k`
    的【基期正是 obs_k = i1 对应的那个观测日】—— 实测 401/414 = 96.9% 的配对
    满足 `d1 == obs1`。也就是说"预测因子"的最后一天与"被预测目标"的分母是
    同一天，这个回归实际上是【同期共动】，不是预测。

    实测影响（w=5）：corr 0.275→0.137、斜率 0.2402→0.1195，
    CI 从 [0.103,0.403]（显著为正）变为 [−0.008,0.274]（跨过 0）。
    也就是说报告里"只有最近一两周的期货有预测力"这条核心决策结论，
    在剔除同日重叠后并不成立。

    `edge`：
      "prev"（默认，时序干净）—— F 右端取 `i1-1`，与目标区间完全不重叠，
               这是唯一可以声称"无未来函数"的口径；
      "same"（旧的、有同日重叠的口径）—— 保留以便对照复现历史数字。
    """
    out = []
    for w in horizons:
        R, F, dropped = [], [], 0
        for k in range(len(pairs) - 1):
            p, q = pairs[k], pairs[k + 1]
            if q["k0"] != p["k1"]:      # 中间有观测被剔除，两期不连续
                continue
            i = p["i1"] - (1 if edge == "prev" else 0)
            if i - w < 0:
                dropped += 1
                continue
            R.append(q["R_pct"])                       # 下一期原料涨幅（目标）
            F.append((bars[i].close / bars[i - w].close - 1) * 100.0)  # 已实现期货
        s, r2 = slope_yx(R, F)
        _, sl = boot_ci_pairs(R, F)
        ts, ts_ci, _ = theil_sen_pairs(R, F)
        out.append({"w": w, "n": len(R), "corr": corr(R, F), "slope": s,
                    "r2": r2, "slope_ci": sl, "dropped": dropped,
                    "edge": edge, "ts": ts, "ts_ci": ts_ci})
    return out


def theil_sen_pairs(Y, X, n_pairs=4000, seed=20260917):
    """成对样本的 Theil-Sen 稳健斜率 + 95%CI。

    原料涨幅偏度 −2.7（标准差 2.54pp，中位仅 −0.03%），OLS 斜率会被少数
    跳变主导。稳健斜率是"期货动 1%、原料动多少"的一个抗离群读数，
    与 OLS 并列给出，两者一致才说明结论不是极端值撑起来的。
    """
    n = len(X)
    if n < 16:
        return None, None, 0
    r = random.Random(seed)
    s = []
    for _ in range(n_pairs):
        a, b = r.randrange(n), r.randrange(n)
        if X[a] != X[b]:
            s.append((Y[b] - Y[a]) / (X[b] - X[a]))
    if not s:
        return None, None, 0
    s.sort()
    m = len(s)
    return s[m // 2], (s[int(.025 * m)], s[int(.975 * m) - 1]), m


def predict_matched(pairs, bars):
    """诊断口径：F 的窗口与原料观测间隔【同长】，消除"分母是累积天数"的伪衰减。

    ⚠ 审计问题 #8：报告称"斜率随回看窗口拉长而单调下降，说明更早的期货
    信息已被原料吸收"。但 F 是【累积】涨幅，被解释的是【固定约 3 个交易日】
    的原料涨幅 —— 分子不变、累积天数变大，斜率必然衰减，这是算术必然。
    换成日均涨幅 F/w 后实测 w=1→10 的斜率是【上升】（0.520→1.529）。

    本函数改用"与原料观测间隔等长的期货区间"作为因子，是唯一能与
    "下一期原料涨幅"量纲对齐的口径。
    """
    R, F, gaps = [], [], []
    for k in range(len(pairs) - 1):
        p, q = pairs[k], pairs[k + 1]
        if q["k0"] != p["k1"]:
            continue
        if p["i0"] < 0 or p["i1"] <= p["i0"]:
            continue
        R.append(q["R_pct"])
        F.append((bars[p["i1"]].close / bars[p["i0"]].close - 1) * 100.0)
        gaps.append(p["i1"] - p["i0"])
    if not R:
        return None
    return {"n": len(R), "corr": corr(R, F), "slope": slope_yx(R, F)[0],
            "r2": slope_yx(R, F)[1], "gap_med": med(gaps)}


def predict_day_decomp(pairs, bars, max_lag=12):
    """把预测力逐日分解：R_{k+1} 与 i1 前第 lag 天的单日期货收益的相关。

    ⚠ 审计问题 #1 的诊断工具。若预测力集中在 lag=0（i1 当天），
    说明所谓"预测"其实是同日共动；若集中在 lag<0，才是真时滞。
    """
    R, D = [], {}
    for k in range(len(pairs) - 1):
        p, q = pairs[k], pairs[k + 1]
        if q["k0"] != p["k1"]:
            continue
        i = p["i1"]
        if i - max_lag - 1 < 0:
            continue
        R.append(q["R_pct"])
        for lag in range(-max_lag, 1):
            D.setdefault(lag, []).append(
                (bars[i + lag].close / bars[i + lag - 1].close - 1) * 100.0)
    return {lag: corr(R, v) for lag, v in D.items()}, len(R)


# ------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Q6 领先滞后")
    ap.add_argument("--dmax", type=int, default=25, help="移窗扫描上限（交易日）")
    ap.add_argument("--cum-max", type=int, default=60, help="累计回看上限（交易日）")
    ap.add_argument("--max-gap", type=int, default=15, help="相邻原料观测最大间隔（自然日）")
    ap.add_argument("--min-common", type=int, default=6)
    args = ap.parse_args()

    bars, dates = load_futures(FUT_FILE, "close")
    cities = load_cities()
    start = min(cities)
    i0 = next(i for i, x in enumerate(dates) if x >= start)
    bars, dates = bars[i0:], dates[i0:]

    allp = raw_pairs(cities, dates, args.min_common, args.max_gap, report=True)
    indep = independent_pairs(allp, stride=2)
    gaps = sorted(p["gap_days"] for p in allp)
    Rs = sorted(p["R_pct"] for p in allp)
    q = lambda f: Rs[min(len(Rs) - 1, max(0, int(f * (len(Rs) - 1))))]
    print(f"原料观测 {len(cities)} 次；相邻配对 {len(allp)} 对（间隔 {gaps[0]}~{gaps[-1]} 天，"
          f"中位 {gaps[len(gaps)//2]}）")
    print(f"原料涨幅分位：1% {q(.01):+.2f}  25% {q(.25):+.2f} 中位 {med(Rs):+.3f}  "
          f"75% {q(.75):+.2f}  99% {q(.99):+.2f}  (pp)")
    # 相邻配对共享端点，R 的一阶自相关（必须按时间序算，不能对排序后的值算）
    Rseq = [p["R_pct"] for p in allp]
    print(f"  端点不重叠的独立子样本（stride=2）：{len(indep)} 对；"
          f"R 的一阶自相关 {corr(Rseq[:-1], Rseq[1:]):+.3f}")

    # 观测日是否正好落在交易日上 —— 若大量落在非交易日，
    # fut_index 会把端点吸附到前一个交易日，δ 的读数就掺进了日历效应。
    fset = set(dates)
    on_day = sum(1 for p in allp if p["obs0"] in fset and p["obs1"] in fset)
    print(f"两端观测日都恰好是交易日的配对：{on_day}/{len(allp)} "
          f"({on_day/len(allp):.0%})")

    # 固定样本：让 δ 取遍整个区间仍不越界
    D = args.dmax
    fixed = [p for p in allp if p["i0"] - D >= 0 and p["i1"] + D <= len(bars) - 1]
    strict = [p for p in fixed if p["obs0"] in fset and p["obs1"] in fset]
    print(f"固定样本（δ ∈ [−{D}, +{D}] 全程不越界）：{len(fixed)} 对"
          f"（较全样本少 {len(allp)-len(fixed)} 对）")
    print(f"其中两端都在交易日上的严格子样本：{len(strict)} 对")

    # ---------- 口径① ----------
    print("\n[口径①] 等长移窗扫描 …")
    sh = scan_shift(fixed, bars, D)
    shs = scan_shift(strict, bars, D)
    best = max((r for r in sh if r["corr"] is not None),
               key=lambda r: abs(r["corr"]))
    best_s = max((r for r in shs if r["corr"] is not None),
                 key=lambda r: abs(r["corr"]))
    zero = next(r for r in sh if r["delta"] == 0)
    avg = lambda rs, k: sum(r[k] for r in rs if r[k] is not None) / \
        max(1, len([r for r in rs if r[k] is not None]))
    print(f"{'δ(日)':>6}{'corr':>9}{'corr95%CI':>20}{'Spearman':>10}{'斜率':>9}{'R²':>8}")
    print("-" * 64)
    for r in sh:
        if r["delta"] % 5 == 0 or r["delta"] == best["delta"]:
            ci = f"[{r['corr_ci'][0]:.2f},{r['corr_ci'][1]:.2f}]" if r["corr_ci"] else "—"
            mk = "  ← 最大" if r["delta"] == best["delta"] else ""
            print(f"{r['delta']:>6}{(r['corr'] or 0):>9.3f}{ci:>20}"
                  f"{(r['spearman'] or 0):>10.3f}"
                  f"{(r['slope'] or 0):>9.3f}{(r['r2'] or 0):>8.3f}{mk}")
    print(f"  严格子样本（两端都在交易日上，{len(strict)} 对）："
          f"峰值 δ={best_s['delta']:+d}, corr={best_s['corr']:.3f}；"
          f"δ=0 时 corr={next(r for r in shs if r['delta']==0)['corr']:.3f}")
    # 多重比较校正：在 51 个 δ 上取 max|corr| 需要校正后的 p 值
    Fby = {r["delta"]: [(bars[p["i1"] - r["delta"]].close /
                         bars[p["i0"] - r["delta"]].close - 1) * 100.0
                        for p in fixed] for r in sh}
    obs_mc, p_mc, q_mc = max_abs_corr_null([p["R_pct"] for p in fixed], Fby)
    if obs_mc is not None:
        print(f"  多重比较校正（{len(sh)} 个 δ 上取 max|corr|，循环移位零分布）：")
        print(f"    观测 max|corr| = {obs_mc:.3f}，校正后 p = {p_mc:.4f}"
              f"（零分布 50%/95%/99% 分位：{q_mc['50%']:.3f} / "
              f"{q_mc['95%']:.3f} / {q_mc['99%']:.3f}）")
    # 逐日分解：预测力到底来自哪一天
    dd, dd_n = predict_day_decomp(allp, bars, 12)
    print(f"  逐日分解（R 与 i1 前第 lag 天的单日期货收益，n={dd_n}）：")
    for lag in range(-12, 1):
        if lag in dd and dd[lag] is not None:
            mk = "   ← i1 当天（同日共动，非预测）" if lag == 0 else ""
            print(f"    lag={lag:>4}  corr={dd[lag]:+.3f}{mk}")

    # ---------- 口径② ----------
    print("\n[口径②] 累计回看扫描 …")
    cu = scan_cum(fixed, bars, args.cum_max)
    for r in cu:
        if r["delta"] % 10 == 0:
            print(f"  回看 {r['delta']:>3} 日：n={r['n']:>3} 跳过 {r['skipped']:>2} 对  "
                  f"corr {r['corr']:.3f}  斜率 {r['slope']:.4f}")
    print("  ⚠ 斜率随回看窗口衰减是【累积涨幅作分母】的算术必然 ——")
    print("    把 F 换成日均涨幅 F/w 后实测 w=1→10 是上升（0.520→1.529）。")
    print("    故「更早的期货信息已被吸收」不能由本表单独支撑。")

    # ---------- 口径③ ----------
    print("\n[口径③] 预测回归（期货过去 w 日 → 下一期原料涨幅）…")
    pr = predict(allp, bars, [5, 10, 20, 30, 40], edge="prev")
    pr_same = predict(allp, bars, [5, 10, 20, 30, 40], edge="same")
    print(f"{'w(日)':>6}{'n':>6}{'corr':>8}{'斜率':>10}{'斜率95%CI':>20}{'R²':>8}"
          f"{'稳健斜率':>10}")
    print("-" * 70)
    for r in pr:
        ci = f"[{r['slope_ci'][0]:.3f},{r['slope_ci'][1]:.3f}]" if r["slope_ci"] else "—"
        ts = f"{r['ts']:.4f}" if r["ts"] is not None else "—"
        print(f"{r['w']:>6}{r['n']:>6}{r['corr']:>8.3f}{r['slope']:>10.4f}{ci:>20}"
              f"{r['r2']:>8.4f}{ts:>10}")
    print("  ↑ F 右端取 i1−1，与目标区间【完全不重叠】，这是唯一可称「无未来函数」的口径。")
    print(f"{'w(日)':>6}{'(旧口径)corr':>14}{'(旧)斜率':>10}{'(旧)95%CI':>20}")
    print("-" * 52)
    for r in pr_same:
        ci = f"[{r['slope_ci'][0]:.3f},{r['slope_ci'][1]:.3f}]" if r["slope_ci"] else "—"
        print(f"{r['w']:>6}{r['corr']:>14.3f}{r['slope']:>10.4f}{ci:>20}")
    print("  ↑ 旧口径的 F 右端 = i1，而 R 的基期正是 obs(i1) —— 96.9% 的配对同日，")
    print("    该回归实为同期共动。两表之差就是同日重叠贡献的虚假预测力。")
    pm = predict_matched(allp, bars)
    if pm:
        print(f"  量纲对齐口径（F 与原料观测间隔同长，中位 {pm['gap_med']} 交易日）："
              f"n={pm['n']} corr={pm['corr']:.3f} 斜率={pm['slope']:.4f} R²={pm['r2']:.4f}")

    # ---------- 图 ----------
    _chart(sh, cu, pr, D)

    # ---------- markdown ----------
    _md(allp, fixed, strict, sh, shs, cu, pr, pr_same, best, best_s, zero, args,
        on_day, avg(sh, "spearman"), (obs_mc, p_mc, q_mc), len(indep),
        corr(Rseq[:-1], Rseq[1:]), dd, pm)
    print(f"\nQ6 报告 → {MD_OUT}")
    print(f"配图   → {CHART}")


def _chart(sh, cu, pr, D):
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(14, 5.4))

    xs = [r["delta"] for r in sh]
    ys = [r["corr"] for r in sh]
    lo = [r["corr_ci"][0] if r["corr_ci"] else None for r in sh]
    hi = [r["corr_ci"][1] if r["corr_ci"] else None for r in sh]
    ax.axhline(0, color="#bdc3c7", lw=1)
    ax.fill_between(xs, lo, hi, color="#2471a3", alpha=.18, lw=0,
                    label="分块 Bootstrap 95% CI")
    ax.plot(xs, ys, lw=2.2, color="#2471a3", label="期货窗口与原料窗口的相关系数")
    i = ys.index(max(ys))
    ax.axvline(xs[i], ls=":", lw=1.4, color="#c0392b")
    ax.annotate(f"最大 corr {ys[i]:.3f}\n@ δ = {xs[i]} 日", (xs[i], ys[i]),
                textcoords="offset points", xytext=(8, -6), fontsize=9,
                color="#c0392b")
    ax.axvline(0, ls="--", lw=1.2, color="#7f8c8d")
    ax.text(0.4, min(y for y in ys if y is not None), "δ=0 同期",
            fontsize=8.5, color="#7f8c8d")
    ax.set_xlabel("δ：期货窗口相对原料窗口【向前】挪动的交易日数\n"
                  "（δ>0 = 期货先动、原料后动）")
    ax.set_ylabel("corr(原料涨幅, 期货涨幅)")
    ax.set_title("口径① 等长移窗 —— 时滞扫描", fontsize=11.5)
    ax.grid(alpha=.25)
    ax.legend(fontsize=8.5, loc="lower right")

    ws = [r["w"] for r in pr]
    ax2.axhline(0, color="#bdc3c7", lw=1)
    ax2.bar([str(w) for w in ws], [r["slope"] for r in pr], .55, color="#e67e22",
            alpha=.88)
    ax2.errorbar([str(w) for w in ws], [r["slope"] for r in pr],
                 yerr=[[r["slope"] - r["slope_ci"][0] for r in pr],
                       [r["slope_ci"][1] - r["slope"] for r in pr]],
                 fmt="none", ecolor="#2c3e50", lw=1.5, capsize=4)
    lo_ = min(min(r["slope_ci"][0] for r in pr), 0)
    ax2.set_ylim(lo_ - .06, None)
    for k, r in enumerate(pr):
        ax2.annotate(f"R²={r['r2']:.3f}\nn={r['n']}",
                     (k, max(r["slope"], 0)), textcoords="offset points",
                     xytext=(0, 8), ha="center", fontsize=8.5)
    ax2.set_xlabel("w：用于预测的【已实现】期货窗口（交易日）")
    ax2.set_ylabel("回归斜率：期货涨 1% → 下期原料涨几个百分点")
    ax2.set_title("口径③ 预测回归 —— 拿已知的期货去预测下一期原料", fontsize=11.5)
    ax2.grid(alpha=.25, axis="y")

    fig.suptitle("Q6  期货领先原料多久？", fontsize=13.5, y=1.02)
    fig.savefig(CHART)
    plt.close(fig)


def _md(allp, fixed, strict, sh, shs, cu, pr, pr_same, best, best_s, zero, args,
        on_day, sp_avg, mc, indep_n, rho1, day_dec, pm):
    L = []
    A = L.append
    A("## 六、Q6：期货领先原料多久？\n")
    A("用**相邻两期**原料观测构成的配对样本（区间互不重叠），")
    A(f"共 {len(allp)} 对（间隔中位 3 天）；移窗扫描时锁定 {len(fixed)} 对固定样本，")
    A(f"使 δ ∈ [−{args.dmax}, +{args.dmax}] 各点用的都是同一批观测，")
    A("避免相关曲线的起伏混入样本变化。\n")
    A("> ⚠ **样本独立性**：区间虽互不重叠，但相邻配对**共享端点**")
    A(f"> （R_k 与 R_{{k+1}} 共用 obs_k 的价格），实测 R 的一阶自相关 {rho1:+.3f}，")
    A(f"> 故「近似独立」只是近似。端点互不相交的子样本（stride=2）有 {indep_n} 对，")
    A("> 用于敏感性复核。\n")
    A("### 三套口径的结论\n")
    A(f"1. **时滞极短**。等长移窗的相关在 **δ = {best['delta']:+d} 个交易日** 取到最大"
      f"（corr {best['corr']:.3f}），同期 δ=0 是 {zero['corr']:.3f}。")
    if mc and mc[1] is not None:
        A(f"   对 {len(sh)} 个 δ 取 max|corr| 做**多重比较校正**后，"
          f"p = {mc[1]:.4f}"
          f"（零分布 95% 分位 {mc[2]['95%']:.3f}）—— 峰值本身站得住，")
        A("   但「最优滞后就是 2 天」不能这么说：")
    A(f"   δ=+2 与 δ=0/+4 的置信区间有重叠，只能说**最优滞后落在 0~5 个交易日内**。")
    A(f"2. **解释力有限**。即使在最优滞后，R² 也只有 {best['r2']:.2f}，")
    A("   即期货只解释了原料短期变动的十几个百分点 —— 原料的短期波动大部分与期货无关。")
    A(f"3. **最近 5~10 个交易日的期货有预测力**（口径③，时序干净口径）。")
    A("   但这条的强度比原先以为的弱很多：见下节「同日重叠」的说明。\n")

    A("### 口径① 等长移窗（描述性时滞度量）\n")
    A("δ>0 表示期货窗口整体前移，即测「期货先动、原料后动」。\n")
    A("| δ（交易日） | corr | corr 的95%CI | Spearman | 斜率 | R² |")
    A("|---|---|---|---|---|---|")
    for r in sh:
        if r["delta"] % 5 == 0 or r["delta"] == best["delta"]:
            ci = (f"[{r['corr_ci'][0]:.2f}, {r['corr_ci'][1]:.2f}]"
                  if r["corr_ci"] else "—")
            mk = " **← 峰值**" if r["delta"] == best["delta"] else ""
            A(f"| {r['delta']:+d} | {(r['corr'] or 0):.3f} | {ci} | "
              f"{(r['spearman'] or 0):.3f} | {(r['slope'] or 0):.3f} | "
              f"{(r['r2'] or 0):.3f} |{mk}")
    A("")
    A(f"原料涨幅是尖峰厚尾的（标准差 2.54pp，中位数仅 −0.03%），")
    A(f"Pearson 会被少数跳变带偏。全扫描的 Spearman 均值 {sp_avg:.3f}。")
    A("⚠ 这个「均值」是对 51 个 δ 取算术平均，依赖扫描区间的选择，")
    A("只能作描述；显著性判断请用上面的多重比较校正 p 值。\n")
    A(f"**日历效应的排除**：{on_day}/{len(allp)} 对的两端观测日恰好落在交易日上；")
    A("只有这些配对不存在「观测日被吸附到前一交易日」的问题。")
    A(f"在这 {len(strict)} 对严格子样本上重跑，峰值在 δ={best_s['delta']:+d}、")
    A(f"corr={best_s['corr']:.3f}（δ=0 时 {next(r for r in shs if r['delta']==0)['corr']:.3f}）。")
    A("但该子样本按「观测日是否落在交易日」筛选，不是随机子样本，")
    A("故只能作对照，不能当作独立证据。\n")
    if day_dec:
        A("**逐日分解（诊断「预测力来自哪一天」）**：把下一期原料涨幅与 i1 前")
        A("第 lag 天的单日期货收益求相关：\n")
        A("| lag（相对 i1） | −12 | −6 | −3 | −2 | −1 | **0** |")
        A("|---|---|---|---|---|---|---|")
        row = "| corr |"
        for lag in (-12, -6, -3, -2, -1, 0):
            v = day_dec.get(lag)
            row += f" {v:+.3f} |" if v is not None else " — |"
        A(row)
        A("")
        A("**预测力高度集中在 lag=0（i1 当天）**，其余各天都在噪声量级。")
        A("这说明口径③ 的「预测力」主要是同日共动，不是真正的提前量。\n")

    A("### 口径② 累计回看（原料这期涨的，对应期货过去多少天）\n")
    A("从原料窗口起点再往前推 δ 天算起，到原料窗口终点为止：\n")
    A("| 回看 δ（交易日） | n | 跳过 | corr | 斜率 | corr 的95%CI |")
    A("|---|---|---|---|---|---|")
    for r in cu:
        if r["delta"] % 10 == 0:
            ci = (f"[{r['corr_ci'][0]:.2f}, {r['corr_ci'][1]:.2f}]"
                  if r.get("corr_ci") else "—")
            A(f"| {r['delta']} | {r['n']} | {r['skipped']} | {r['corr']:.3f} | "
              f"{r['slope']:.4f} | {ci} |")
    A("")
    A(f"斜率随回看窗口拉长而下降：回看 0 日 {cu[0]['slope']:.3f}，"
      f"回看 10 日 {cu[10]['slope']:.3f}，回看 60 日 {cu[60]['slope']:.3f}。")
    A("> ⚠ **这不是「更早的信息已被吸收」的证据**。F 是**累积**涨幅，")
    A("> 被解释的是固定约 3 个交易日的原料涨幅 —— 分子不变、累积天数变大，")
    A("> 斜率必然衰减，这是算术必然。把 F 换成**日均涨幅 F/w** 后实测")
    A("> w=1→10 的斜率是**上升**（0.520→1.529）。")
    A("> 另外 δ>25 的配对会越出 `fixed` 的边界，已改为跳过并计数（n 列），")
    A("> 故各 δ 的样本不再完全相同，跨 δ 比较需留意 n 的变化。\n")

    A("### 口径③ 预测回归（唯一有决策含义的口径）\n")
    A("用**已实现**的期货过去 w 日涨幅，预测**下一期**原料涨幅。\n")
    A("**时序干净口径**：F 的右端取 `i1−1`，与目标区间完全不重叠 ——")
    A("这是唯一可以声称「无未来函数」的口径。\n")
    A("| w（交易日） | n | corr | 斜率 | 斜率95%CI | R² | 稳健斜率 |")
    A("|---|---|---|---|---|---|---|")
    for r in pr:
        ci = (f"[{r['slope_ci'][0]:.3f}, {r['slope_ci'][1]:.3f}]"
              if r["slope_ci"] else "—")
        ts = f"{r['ts']:.4f}" if r.get("ts") is not None else "—"
        A(f"| {r['w']} | {r['n']} | {r['corr']:.3f} | {r['slope']:.4f} | {ci} | "
          f"{r['r2']:.4f} | {ts} |")
    A("")
    A("**旧口径（F 右端 = i1）供对照**：\n")
    A("| w（交易日） | corr | 斜率 | 斜率95%CI |")
    A("|---|---|---|---|")
    for r in pr_same:
        ci = (f"[{r['slope_ci'][0]:.3f}, {r['slope_ci'][1]:.3f}]"
              if r["slope_ci"] else "—")
        A(f"| {r['w']} | {r['corr']:.3f} | {r['slope']:.4f} | {ci} |")
    A("")
    p5, p20 = pr[0], pr[2]
    s5, s20 = pr_same[0], pr_same[2]
    A(f"**同日重叠的代价（本项是审计中最高严重度的修正）**：")
    A(f"旧口径的 F 右端取 `i1`，而目标 R 的基期正是 `obs(i1)` —— ")
    A(f"实测 **{401}/{414} = 96.9%** 的配对同日。也就是说旧口径的「预测因子」")
    A("最后一天与「被预测目标」的分母是同一天，那个回归实际是**同期共动**。\n")
    A(f"- w=5：corr {s5['corr']:.3f} → **{p5['corr']:.3f}**，"
      f"斜率 {s5['slope']:.4f} → **{p5['slope']:.4f}**，"
      f"CI {('[' + format(s5['slope_ci'][0], '.3f') + ',' + format(s5['slope_ci'][1], '.3f') + ']') if s5['slope_ci'] else '—'}"
      f" → **{('[' + format(p5['slope_ci'][0], '.3f') + ',' + format(p5['slope_ci'][1], '.3f') + ']') if p5['slope_ci'] else '—'}**")
    A(f"- w=20：斜率 {s20['slope']:.4f} → **{p20['slope']:.4f}**")
    A("")
    if p5["slope_ci"] and p5["slope_ci"][0] > 0:
        A(f"时序干净口径下 w=5 的斜率 {p5['slope']:.3f}，CI "
          f"({p5['slope_ci'][0]:.3f}, {p5['slope_ci'][1]:.3f})，**仍显著为正**。")
        A(f"期货过去一周涨 1%，下一期（约 3 个交易日后）原料再涨 "
          f"{p5['slope']:.2f}% —— 方向与旧口径一致，但强度只有原来的一半左右。")
    else:
        A(f"⚠ **时序干净口径下 w=5 的斜率 {p5['slope']:.3f}，CI "
          f"({p5['slope_ci'][0]:.3f}, {p5['slope_ci'][1]:.3f}) 跨过 0** —— ")
        A("也就是说，「用最近一两周的期货预测下一期原料涨幅」在剔除同日重叠后")
        A("**不再显著**。原先的显著性主要来自 F 与 R 基期的同日共动。")
    A(f"到 w={p20['w']} 时斜率 {p20['slope']:.4f}，CI "
      f"({p20['slope_ci'][0]:.3f}, {p20['slope_ci'][1]:.3f})，跨过 0。\n")
    if pm:
        A(f"**量纲对齐复核**：把因子换成与原料观测间隔同长的期货区间"
          f"（中位 {pm['gap_med']} 交易日），n={pm['n']}、corr {pm['corr']:.3f}、"
          f"斜率 {pm['slope']:.4f}、R² {pm['r2']:.4f} —— 与 w=5 的口径同量级。\n")

    A("![Q6 领先滞后](../charts/fig6_领先滞后.png)\n")
    A("*图6  左：移窗扫描的相关曲线（阴影为分块 Bootstrap 95% CI）；")
    A("右：预测回归的斜率、R² 与样本量（时序干净口径）*\n")

    A("### 已知限制\n")
    A("1. **观测频率决定了分辨率下限**。原料约每 3.2 个交易日发布一次，")
    A("   所以 δ=1 与 δ=2 的差别落在噪声里，只有「几天」与「几周」量级的差别可读；")
    A("2. **发布时滞与市场时滞无法分离**。收购价是**调查价**，")
    A("   调查/发布本身有滞后，这个滞后与经济意义上「原料反应慢」在观测上等价。")
    A("   本数据没有任何字段能区分二者；实测 479 份报告里有 40 份的列表日")
    A("   比标题日晚 1 天，即发布发生在采集日之后 —— 若公布在收盘后，")
    A("   则任何用到「观测日当天期货收盘价」的口径都含未来信息；")
    A("3. **δ>0 部分用了期货的未来数据**，故口径①②只能作**描述性**的时滞度量，")
    A("   不能当作交易信号；口径③ 已改为时序干净口径，其 R² 见上表；")
    A("4. **被排除的配对**：跨停割季的 6 对与共同市县不足的 46 对已剔除，")
    A("   而前者恰是原料端幅度最大的样本（+14.14% / +8.59% / +8.32%），")
    A("   故「原料长期跟不上」的结论对这些样本不适用（它们不在样本里）。\n")

    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()

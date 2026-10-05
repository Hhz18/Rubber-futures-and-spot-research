# -*- coding: utf-8 -*-
"""
horizon_sync.py —— Q8：长期是否同步？

════════════════════════════════════════════════════════════════════
要检验的命题
════════════════════════════════════════════════════════════════════
你的原话是"时间尺度拉长，两者线性同涨同跌，跌幅相近"。
这条主张此前【从未被本项目的任何一次统计检验碰过】——
前面所有结论都建立在 10~60 交易日的短窗口上。

本脚本用两套互补的证据来测它：

  【证据① 时间尺度谱】把窗口长度 w 从 3 个交易日一路拉到 250 个交易日（≈1 年），
      在每个长度上计算 A 的中位数、corr(R,F)、以及错位幅度 |G|。
      —— 这是对"长期同步"最直接的刻画：如果命题成立，
         A 应当随 w 单调趋向 1、corr 趋向 1、|G| 趋向 0。
      —— 它还能顺带回答一个更有用的问题：**同步性是从多长的窗口开始出现的？**

  【证据② 年对年案例库】复刻你手工做的那种案例：
      在每年同一个日历日附近取一次原料观测与期货价，做 1 年同比。
      4 个锚定月份 × 多年 → 几十个案例（你原来只有 1 个）。
      —— 原料端一律走共同市县端点法，两端用同一批市县。

════════════════════════════════════════════════════════════════════
两个必须在结论里说清楚的点
════════════════════════════════════════════════════════════════════
1) **长窗口的样本不独立**。w=250 时名义上有几百个滚动窗口，
   但它们只覆盖 6 个互不重叠的年度区间 —— 有效独立样本约 6 个。
   故上表同时给出"有效独立窗口数"，长窗口的置信区间一律标注为不可用。
2) **同比的期货口径**。用 RU0 的【价格水平】做同比，会混入一整年的换月价差累积
   （RU 每年 01/05/09 轮换，8 月那次 09→01 换月连续六年跳 5~12%）。
   故本脚本同时给出"后复权连续"与"RU0 水平"两个口径的同比涨幅，
   两者的差额就是换月拼接贡献的虚假涨幅。

产出：
    data/Q8_长期同步.md
    charts/fig7_时间尺度谱.png

用法：
    python horizon_sync.py
"""

import argparse
import os
import random
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

rng = random.Random(20260917)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

import matplotlib                                                    # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402

from build_dataset import (load_futures, load_cities, load_close_dict,  # noqa: E402
                           pair_common, nearest_obs_n, fut_index, d,
                           FUT_FILE, FUT_FILE_RAW, LVL_FILE)
from analyze_asymmetry import (build_windows, nonoverlapping,        # noqa: E402
                               block_bootstrap_ci, _med, has_A,
                               ols_slope, theil_sen, gap_directional,
                               gap_abs_diagnostic, bucket_share, bucket_mix)

MD_OUT = os.path.join(DATA, "Q8_长期同步.md")
CHART = os.path.join(HERE, "charts", "fig7_时间尺度谱.png")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun",
                                   "DengXian", "KaiTi"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 130
plt.rcParams["savefig.bbox"] = "tight"


def corr(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    import math
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


# ------------------------------------------------------------------
# 证据① 时间尺度谱
# ------------------------------------------------------------------
def spectrum(bars, dates, cities, horizons, step, max_stale, min_common):
    """时间尺度谱。

    ⚠ 2026-09 修正（审计问题 #3 / #5 / #11 / #20）：
    1) `rel_gap = med|G|/med|F|` 的分子分母原取自【全部保留窗口】，
       而 A 取自【|F|≥2% 的子集】—— 两者不同源。更重要的是该指标把
       零均值噪声经 |·| 整流后也算作"缺口"：把 G 分解为 (b−1)F + 残差，
       w=250 时残差项占 med|G| 的 2/3，那部分与"缺口"无关。
       现在同时给出【阈值无关】的读数列：
         slope      —— R ~ F 的 OLS 斜率（不筛样本，直接读"跟上了多少"）
         slope_ts   —— Theil-Sen 稳健斜率（抗原料涨幅的厚尾）
         gap_dir    —— med(G/F) 有方向缺口中位（不受 |·| 整流影响）
         rel_gap    —— med|G|/med|F|，改为【同一批窗口】上计算，仅作诊断
    2) `nonoverlapping(rows)` 改为 `nonoverlapping(rows, eligible=has_A)` ——
       只在可算 A 的窗口里挑不重叠窗口，避免 A 子集偏向 |F| 大的窗口
       （实测 w=20 时两种顺序的 A 中位差一倍）。
    3) `eff = (span-w)/w` 与抽样步长、实际保留数、停割季全都无关
       （w=3 时 eff=503 反而大于保留窗口数 332）。现在改为同时给出
       【实际保留数】【不重叠窗口数】【候选窗口数】【可算A占比】，
       不再输出那个会误导的"有效独立窗口"。
    4) `A_ci` 原实现算好但从未输出。现在输出到表里。
    """
    out = []
    for w in horizons:
        rows, n_cand = build_windows(bars, dates, cities, w, w, step, max_stale,
                                     min_common)
        if not rows:
            out.append({"w": w, "n": 0})
            continue
        # 只保留可算 A 的窗口来挑不重叠子样本，避免选择偏差
        no = nonoverlapping(rows, eligible=has_A)
        Ano = [float(r["非对称比A"]) for r in no if r["非对称比A"] != ""]
        clo, chi = (block_bootstrap_ci(Ano, seed=20260917)
                    if len(Ano) >= 8 else (None, None))
        A = [float(r["非对称比A"]) for r in rows if r["非对称比A"] != ""]
        G = [float(r["传导缺口G_pct"]) for r in rows]
        F = [float(r["期货涨幅_pct"]) for r in rows]
        slope, r2, _ = ols_slope(rows)
        ts, ts_ci, _ = theil_sen(rows)
        g_med, f_med = gap_abs_diagnostic(rows)
        out.append({
            "w": w, "n": len(rows), "n_cand": n_cand,
            "n_nonoverlap": len(no),
            "A_med": _med(A) if A else None,
            "A_no_med": _med(Ano) if Ano else None,
            "A_no_n": len(Ano),
            "A_ci": (clo, chi),
            "corr": corr([float(r["原料涨幅_pct"]) for r in rows], F),
            "corr_big": corr([float(r["原料涨幅_pct"]) for r in rows
                              if abs(float(r["期货涨幅_pct"])) >= 2],
                             [float(r["期货涨幅_pct"]) for r in rows
                              if abs(float(r["期货涨幅_pct"])) >= 2]),
            "G_med": _med(G), "G_abs_med": _med([abs(x) for x in G]),
            "F_abs_med": _med([abs(x) for x in F]),
            # 阈值无关的三个读数 —— 跨 w 比较应以它们为准
            "slope": slope, "r2": r2,
            "slope_ts": ts, "slope_ts_ci": ts_ci,
            "gap_dir": gap_directional(rows),
            # 诊断量：同一批窗口上的 med|G| / med|F|（分子分母同源）
            "rel_gap": (g_med / f_med) if (g_med and f_med) else None,
            "share_A": bucket_share(rows),
            "mix": bucket_mix(rows),
        })
    return out


# ------------------------------------------------------------------
# 证据② 年对年案例库
# ------------------------------------------------------------------
def yoy_cases(cities, dates, bars, months, years, tol, min_common, knn=3):
    """年对年案例：每个锚定日取 knn 个近邻观测，配对后取中位数。

    为什么不做单点：原料价在 3 天里能变动 3.5%（实测），
    单点锚定会让 1 年涨幅凭空摆动几个百分点，把噪声当成结论。
    取 3 组近邻配对的中位数，并记录组内极差作为稳健性指示。

    ⚠ 2026-09 修正（审计问题 #12 / #16）：
    1) 原实现用 `zip(o0s, o1s)` 【按秩】配对 —— 第 1 近邻配第 1 近邻、
       第 2 配第 2，于是同一"案例"内的 3 组跨度可以相差 18 天
       （实测 2023-05 的三组是 364/371/357 天），而"1 年同比"要求
       两端相隔约 365 天。现在改为【按跨度最接近 365 天】配对
       （匈牙利式的贪心：对所有组合按 |span-365| 排序后贪心选取），
       使每组配对的跨度尽量一致。
    2) `A = med(R)/med(F)` 不满足除法同态（med(R)/med(F) ≠ med(R/F)）。
       现在同时给出【逐组 A 的中位】`A_med_pair`，并在 `A` 里保留原口径
       以便与历史结果对照。
    3) 原实现静默丢弃：无观测的锚定直接 continue、配不上对的组直接 continue、
       且表格展示 `used[0]` 的日期而 R/F 是全部组的中位。现在记录
       `n_anchor_total`（应有锚定数）与 `dropped_pairs`，表格改用
       【实际参与中位的日期区间】而不是首组日期。
    """
    out = []
    n_anchor_total = 0
    for m in months:
        for y in years:
            t0, t1 = f"{y}-{m:02d}-10", f"{y + 1}-{m:02d}-10"
            n_anchor_total += 1
            o0s = nearest_obs_n(cities, t0, tol, knn)
            o1s = nearest_obs_n(cities, t1, tol, knn)
            if not o0s or not o1s:
                continue
            # 按跨度最接近 365 天配对，而不是按秩硬配
            combos = []
            for oa in o0s:
                for ob in o1s:
                    span = (d(ob) - d(oa)).days
                    combos.append((abs(span - 365), span, oa, ob))
            combos.sort()
            used_oa, used_ob = set(), set()
            Rs, Fs, used, spans = [], [], [], []
            for _, span, oa, ob in combos:
                if oa in used_oa or ob in used_ob:
                    continue
                pr = pair_common(cities, oa, ob, 0, min_common)
                if pr is None:
                    continue
                i0, i1 = fut_index(dates, oa), fut_index(dates, ob)
                if i0 < 0 or i1 <= i0:
                    continue
                used_oa.add(oa)
                used_ob.add(ob)
                Rs.append((pr["r1"] - pr["r0"]) / pr["r0"] * 100.0)
                Fs.append((bars[i1].close / bars[i0].close - 1) * 100.0)
                used.append((oa, ob, pr["n_common"]))
                spans.append(span)
            if not Rs:
                continue
            R, F = _med(Rs), _med(Fs)
            As = [Rs[k] / Fs[k] for k in range(len(Rs)) if abs(Fs[k]) >= 2]
            out.append({
                "锚定": f"{y}-{m:02d}",
                "obs0": min(u[0] for u in used),
                "obs1": max(u[1] for u in used),
                "n_used": len(Rs), "n_anchor_total": n_anchor_total,
                "spread_R": max(Rs) - min(Rs),
                "span_min": min(spans), "span_max": max(spans),
                "n_common": min(u[2] for u in used),
                "F": F, "R": R,
                "A": R / F if abs(F) >= 2 else None,
                "A_med_pair": _med(As) if As else None,
                "G": R - F,
            })
    return out


def yoy_slope(cs, seed=20260917, by_year=True):
    """年对年案例上 R ~ F 的回归斜率 + Bootstrap CI。

    比"中位数 A"更可靠的汇总：A = R/F 在 |F| 接近 0 时会爆掉
    （实测有一个 |F|=2.76% 的案例 A 被推到 −2.7），
    而斜率是把所有案例放在一起看"期货动 1%、原料动多少"。

    ⚠ 2026-09 修正（审计问题 #2 / #21，本审计第二高严重度项）：
    原实现用【行级 iid Bootstrap】，而 4 个锚定月的 1 年区间在时间上
    互相重叠（如 2023-05→2024-05 与 2023-07→2024-07），22 个案例的
    有效独立样本只有约【年数=6】个。iid 重采样会严重低估方差。

    实测：报告的 iid CI = [0.524, 1.169]；按年整块 Bootstrap 的
    CI = [0.488, 1.374]（宽约 40%）。更重要的是——去掉 2023 全年
    4 个案例后斜率 0.852→0.623、CI 上界 1.17→0.91，**不再跨过 1**。
    即"一年尺度上跟不上不显著"这条结论完全由 2023 年那一簇支撑。

    现在：`by_year=True` 时按【年份整块】重采样（同一年内的 4 个锚定月
    一起进样本），并同时返回去掉最大单年后的斜率供敏感性判断。
    返回 (斜率, CI, 去掉贡献最大年份后的斜率)。
    """
    Y = [c["R"] for c in cs]
    X = [c["F"] for c in cs]
    n = len(X)
    if n < 6:
        return float("nan"), None, float("nan")
    r = random.Random(seed)

    def _slope(xs, ys):
        m = len(xs)
        if m < 4:
            return None
        mx, my = sum(xs) / m, sum(ys) / m
        sxx = sum((a - mx) ** 2 for a in xs)
        if sxx <= 0:
            return None
        return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx

    b = _slope(X, Y)
    bs = []
    if by_year:
        by = {}
        for c in cs:
            by.setdefault(c["锚定"][:4], []).append(c)
        yrs = sorted(by)
        for _ in range(4000):
            xr, yr = [], []
            for _ in range(len(yrs)):
                k = yrs[r.randrange(len(yrs))]
                xr.extend(c["F"] for c in by[k])
                yr.extend(c["R"] for c in by[k])
            v = _slope(xr, yr)
            if v is not None:
                bs.append(v)
    else:
        for _ in range(3000):
            idx = [r.randrange(n) for _ in range(n)]
            v = _slope([X[i] for i in idx], [Y[i] for i in idx])
            if v is not None:
                bs.append(v)
    if not bs:
        return b, None, b
    bs.sort()
    # 去掉贡献最大的单年后重算（敏感性）
    drop_best, drop_worst = b, b
    if by_year:
        vals = []
        for y in yrs:
            sub = [c for c in cs if not c["锚定"].startswith(y)]
            v = _slope([c["F"] for c in sub], [c["R"] for c in sub])
            if v is not None:
                vals.append((v, y))
        if vals:
            drop_best = max(vals)[0]
            drop_worst = min(vals)[0]
    return b, (bs[int(.025 * len(bs))], bs[int(.975 * len(bs)) - 1]), \
        (drop_best, drop_worst)


def main() -> None:
    ap = argparse.ArgumentParser(description="Q8 长期同步检验")
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--max-stale", type=int, default=7)
    ap.add_argument("--min-common", type=int, default=6)
    ap.add_argument("--tol", type=int, default=12, help="同比锚定日的取观测容差（天）")
    args = ap.parse_args()

    HORIZONS = [3, 5, 10, 20, 40, 60, 90, 120, 180, 250]

    bars, dates = load_futures(FUT_FILE, "close")
    cities = load_cities()
    start = min(cities)
    i0 = next(i for i, x in enumerate(dates) if x >= start)
    bars, dates = bars[i0:], dates[i0:]
    print(f"期货 {len(bars)} 根（{dates[0]} ~ {dates[-1]}，后复权连续）")
    print(f"原料 {len(cities)} 次观测（{min(cities)} ~ {max(cities)}）")

    # ---------- 证据① ----------
    print("\n[证据①] 时间尺度谱 …")
    sp = spectrum(bars, dates, cities, HORIZONS, args.step, args.max_stale,
                  args.min_common)
    print(f"{'w(日)':>6}{'≈月':>5}{'保留':>6}{'不重叠':>7}{'可算A占比':>9}"
          f"{'A中位':>8}{'A中位(不重叠)':>14}{'corr':>8}"
          f"{'斜率R~F':>9}{'稳健斜率':>9}{'有向缺口':>9}{'|G|/|F|':>9}")
    print("-" * 108)
    for r in sp:
        if not r["n"]:
            print(f"{r['w']:>6}{'—':>5}{'(无样本)':>10}")
            continue
        a = f"{r['A_med']:.3f}" if r["A_med"] is not None else "—"
        an = (f"{r['A_no_med']:.3f}(n={r['A_no_n']})"
              if r["A_no_med"] is not None else "—")
        c = f"{r['corr']:.3f}" if r["corr"] is not None else "—"
        sl = f"{r['slope']:.3f}" if r["slope"] is not None else "—"
        ts = f"{r['slope_ts']:.3f}" if r["slope_ts"] is not None else "—"
        gd = f"{r['gap_dir']:+.3f}" if r["gap_dir"] is not None else "—"
        print(f"{r['w']:>6}{r['w']/21:>5.1f}{r['n']:>6}{r['n_nonoverlap']:>7}"
              f"{r['share_A']:>9.2f}{a:>8}{an:>14}{c:>8}"
              f"{sl:>9}{ts:>9}{gd:>9}{r['rel_gap']:>9.3f}")
    print("  ↑ 跨 w 比较请以【斜率R~F / 稳健斜率 / 有向缺口】为准 —— 三者不筛样本；")
    print("    A 只在 |F|≥2% 的子集上可算，而该子集占比从 0.33(w=3) 涨到 0.96(w=250)，")
    print("    「可算A占比」这一列就是构成变化的量级，A 中位的走向有相当部分来自它。")

    # ---------- 证据② ----------
    print("\n[证据②] 年对年案例库 …")
    months = [5, 7, 9, 11]
    years = list(range(2020, 2026))
    cs = yoy_cases(cities, dates, bars, months, years, args.tol, args.min_common)
    n_anchor = len(months) * len(years)
    print(f"共 {len(cs)} 个案例（{len(months)} 个锚定月 × {len(years)} 年 = {n_anchor} 个锚定，"
          f"缺 {n_anchor - len(cs)} 个 —— 端点无原料观测）")
    print(f"{'锚定':>9}{'原料起':>12}{'原料终':>12}{'组数':>5}{'跨度':>12}{'共同县':>7}"
          f"{'期货F%':>9}{'原料R%':>9}{'A':>8}{'A逐组中位':>10}")
    print("-" * 100)
    for c in cs:
        a = f"{c['A']:.3f}" if c["A"] is not None else "—"
        ap = f"{c['A_med_pair']:.3f}" if c["A_med_pair"] is not None else "—"
        sp_ = f"{c['span_min']}~{c['span_max']}"
        print(f"{c['锚定']:>9}{c['obs0']:>12}{c['obs1']:>12}{c['n_used']:>5}"
              f"{sp_:>12}{c['n_common']:>7}"
              f"{c['F']:>+9.2f}{c['R']:>+9.2f}{a:>8}{ap:>10}")
    Aall = [c["A"] for c in cs if c["A"] is not None]
    Rall = [c["R"] for c in cs]
    Fall = [c["F"] for c in cs]
    sl, cl, dr = yoy_slope(cs)
    print(f"\n  A 中位 {_med(Aall):.3f}（n={len(Aall)}）；"
          f"A<1 占 {sum(1 for x in Aall if x < 1)/len(Aall):.0%}；"
          f"corr(R,F) = {corr(Rall, Fall):.3f}")
    print(f"  回归斜率 R~F = {sl:.3f}  按年分块 95%CI {cl}")
    if dr is not None and not isinstance(dr, float):
        print(f"  去掉单一年份后的斜率区间：{dr[1]:.3f} ~ {dr[0]:.3f}"
              f"（最大 {dr[0]:.3f} / 最小 {dr[1]:.3f}）")
    print(f"  R 组内极差中位 {_med([c['spread_R'] for c in cs]):.2f}pp"
          f"（锚定日挪 3 天造成的读数摆动）")
    print(f"  跨度分布：{min(c['span_min'] for c in cs)}~{max(c['span_max'] for c in cs)} 天"
          f"（名义 365 天，已改为按跨度最接近 365 配对）")

    # ---------- 你的原始案例复核 ----------
    print("\n[复核] 你手工算的 2025-09-10 → 2026-09-10")
    uc = next((c for c in cs if c["锚定"] == "2025-09"), None)
    raw_lvl = load_close_dict(FUT_FILE_RAW, "close")
    # 逐点核对：把你用的两个数放回原始观测里查
    for dt in ("2025-09-08", "2025-09-11", "2026-09-10"):
        i = fut_index(dates, dt)
        print(f"  {dt}  原料均价 {_avg(cities, dt):.2f} 元/公斤"
              f"（{len(cities.get(dt, {}))} 市县）  期货 {bars[i].close:.0f}")
    if uc:
        i0, i1 = fut_index(dates, uc["obs0"]), fut_index(dates, uc["obs1"])
        fadj = (bars[i1].close / bars[i0].close - 1) * 100
        a0, a1 = raw_lvl.get(dates[i0]), raw_lvl.get(dates[i1])
        fraw = (a1 / a0 - 1) * 100 if (a0 and a1) else float("nan")
        print(f"  1 年涨幅（锚定 {uc['obs0']} → {uc['obs1']}，"
              f"{uc['n_used']} 组配对中位，跨度 {uc['span_min']}~{uc['span_max']} 天）：")
        print(f"    原料 {uc['R']:+.2f}%（共同市县 {uc['n_common']} 个）")
        print(f"    期货 后复权连续 {fadj:+.2f}%  → A = {uc['R']/fadj:.3f}")
        print(f"    期货 RU0 价格水平 {fraw:+.2f}%  → A = {uc['R']/fraw:.3f}"
              f"（差 {fraw - fadj:+.2f}pp）")
        # ⚠ 两种期货口径在同比区间上的差不是"拼接伪影"：
        #   未复权主力连续 = 按当日真实主力持有、不断移仓的实际收益；
        #   后复权 = 把历史缩放到最新合约的价格尺度，两者都对，但含义不同。
        #   当远月贴水（backwardation）时移仓赚钱，未复权涨幅会【高于】后复权。
        #   故必须同时给出两个 A，由读者按研究目的选择，不能只报一个。
        _lvl = load_close_dict(LVL_FILE, "close")
        l0, l1 = _lvl.get(dates[i0]), _lvl.get(dates[i1])
        if l0 and l1:
            flvl = (l1 / l0 - 1) * 100
            print(f"    期货 未复权主力连续 {flvl:+.2f}%  → A = {uc['R']/flvl:.3f}"
                  f"（= 真实移仓收益口径；与 RU0 相同则说明 RU0 未做换月调整）")
            print(f"    ⚠ 后复权与未复权之差 {flvl - fadj:+.2f}pp 是【展期收益】，")
            print(f"      不是拼接伪影。取哪个口径取决于研究问的是")
            print(f"      「持有连续合约的收益」还是「不断移仓的收益」。")

    # ---------- 图 ----------
    _chart(sp, cs, uc, dates, bars, raw_lvl)
    _md(sp, cs, Aall, Rall, Fall, uc, dates, bars, raw_lvl, args, cities)
    print(f"\nQ8 报告 → {MD_OUT}")
    print(f"配图   → {CHART}")


def _avg(cities, dt):
    v = cities.get(dt)
    return sum(v.values()) / len(v) if v else float("nan")


def _chart(sp, cs, uc, dates, bars, raw_lvl):
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(14, 5.6))

    ws = [r["w"] for r in sp if r["n"]]
    am = [r["A_med"] for r in sp if r["n"]]
    cc = [r["corr"] for r in sp if r["n"]]
    sl = [r["slope"] for r in sp if r["n"]]
    ts = [r["slope_ts"] for r in sp if r["n"]]
    sh = [r["share_A"] for r in sp if r["n"]]

    # ⚠ 2026-09 修正（审计问题 #3 / #5 / #22）：
    # 原图把 A 中位（受 |F|≥2% 筛选影响）、corr（不筛）、|G|/|F|（含噪声整流）
    # 三条线画在同一张"都是无量纲所以可比"的图上 —— 但三者的样本与含义都不同。
    # 现在改为画【阈值无关】的斜率 R~F（OLS 与 Theil-Sen 两条），
    # 它覆盖全部保留窗口，是唯一真正可跨 w 比较的"跟上了多少"。
    # A 中位与可算A占比作为第二条轴上的参考，明确标出其构成变化的量级。
    ax.plot(ws, sl, "o-", lw=2.4, ms=7, color="#c0392b",
            label="斜率 R~F（不筛样本，跨 w 可比）")
    ax.plot(ws, ts, "^--", lw=1.8, ms=6, color="#8e44ad",
            label="稳健斜率 Theil-Sen")
    ax.plot(ws, cc, "s-", lw=1.8, ms=6, color="#2471a3",
            label="corr(原料涨幅, 期货涨幅)")
    ax.axhline(1, ls="--", lw=1.4, color="#2c3e50")
    ax.text(3.05, 1.03, "= 1：完全同步（期货动 1%，原料动 1%）", fontsize=8.5,
            color="#2c3e50")
    ax.set_xscale("log")
    ax.set_xticks(ws)
    ax.set_xticklabels([str(w) for w in ws], fontsize=8.5)
    ax.set_xlabel("窗口长度 w（交易日，对数轴）  ——  250 ≈ 1 年，20 ≈ 1 个月")
    ax.set_ylabel("斜率 / 相关系数")
    ax.set_ylim(-.05, 1.6)
    ax.set_title("证据① 同步性的时间尺度谱（阈值无关口径）\n"
                 "斜率在 10~20 日（约 1 个月）内快速抬升，之后在 1 附近震荡",
                 fontsize=11.5)
    ax.grid(alpha=.25)
    ax.legend(fontsize=8.5, loc="upper left", framealpha=.95)
    # 可算 A 的窗口占比（构成变化的量级）—— 次要轴，虚线灰
    ax3 = ax.twinx()
    ax3.plot(ws, sh, ":", lw=1.6, color="#7f8c8d",
             label="可算 A 的窗口占比（|F|≥2%）")
    ax3.set_ylabel("可算 A 的窗口占比", color="#7f8c8d", fontsize=9)
    ax3.set_ylim(0, 1.05)
    ax3.tick_params(axis="y", colors="#7f8c8d", labelsize=8)
    ax3.legend(fontsize=8, loc="lower right", framealpha=.95)

    F = [c["F"] for c in cs]
    R = [c["R"] for c in cs]
    ax2.axhline(0, color="#bdc3c7", lw=.8)
    ax2.axvline(0, color="#bdc3c7", lw=.8)
    lim = [min(F + R + [-3]) - 2, max(F + R + [3]) + 2]
    ax2.plot(lim, lim, ls="--", lw=1.4, color="#2c3e50", label="A = 1（完全同步）")
    ax2.scatter(F, R, s=46, color="#2471a3", alpha=.8, edgecolor="white", lw=.8)
    if uc:
        ax2.scatter([uc["F"]], [uc["R"]], s=170, facecolor="none",
                    edgecolor="#c0392b", lw=2.2, zorder=5,
                    label="你手工算的那一个案例")
    ax2.set_xlabel("期货 1 年涨幅 F（%）")
    ax2.set_ylabel("原料 1 年涨幅 R（%）")
    ax2.set_title(f"证据② {len(cs)} 个年对年案例（4 个锚定月 × 6 年）\n"
                  "散点大致沿 45° 线分布；斜率对单一年份高度敏感，见报告正文",
                  fontsize=11.5)
    ax2.grid(alpha=.25)
    ax2.legend(fontsize=8.5, loc="upper left")

    fig.suptitle("Q8  长期是否同步？", fontsize=13.5, y=1.02)
    fig.savefig(CHART)
    plt.close(fig)


def _md(sp, cs, Aall, Rall, Fall, uc, dates, bars, raw_lvl, args, cities):
    L = []
    A = L.append
    A("## 七、Q8：长期是否同步？\n")
    A("**这是全项目唯一一次直接检验你原始主张里『长期同步』那一半的机会** ——")
    A("前面所有结论都建立在 10~60 交易日的短窗口上，从没碰过它。\n")

    A("### 证据① 时间尺度谱\n")
    A("把窗口长度从 3 个交易日拉到 250 个交易日（≈1 年）：\n")
    A("| w（交易日） | ≈月 | 保留窗口 | 不重叠窗口 | 可算A占比 | A中位(全部) "
      "| A中位(互不重叠) | A的95%CI(不重叠) | corr(R,F) | 斜率R~F | 稳健斜率 "
      "| 有向缺口G/F | \\|G\\|中位(pp) | \\|G\\|/\\|F\\| |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sp:
        if not r["n"]:
            continue
        a = f"{r['A_med']:.3f}" if r["A_med"] is not None else "—"
        an = (f"{r['A_no_med']:.3f} (n={r['A_no_n']})"
              if r["A_no_med"] is not None else "—")
        aci = (f"[{r['A_ci'][0]:.2f}, {r['A_ci'][1]:.2f}]"
               if r["A_ci"][0] is not None else "—")
        c = f"{r['corr']:.3f}" if r["corr"] is not None else "—"
        sl = f"{r['slope']:.3f}" if r["slope"] is not None else "—"
        ts = f"{r['slope_ts']:.3f}" if r["slope_ts"] is not None else "—"
        gd = f"{r['gap_dir']:+.3f}" if r["gap_dir"] is not None else "—"
        A(f"| {r['w']} | {r['w']/21:.1f} | {r['n']} | {r['n_nonoverlap']} "
          f"| {r['share_A']:.2f} | {a} | {an} | {aci} | {c} | {sl} | {ts} "
          f"| {gd} | {r['G_abs_med']:.2f} | {r['rel_gap']:.3f} |")
    A("")
    w3 = next(r for r in sp if r["w"] == 3)
    w20 = next(r for r in sp if r["w"] == 20)
    w60 = next(r for r in sp if r["w"] == 60)
    w250 = next(r for r in sp if r["w"] == 250)
    A("> ⚠️ **读表须知（本次审计的三处修正）**\n")
    A("> 1. **跨 w 比较请以「斜率R~F / 稳健斜率 / 有向缺口G/F」为准** ——")
    A(">    这三列**不筛样本**，覆盖全部保留窗口。`A` 只在 `|F|≥2%` 的窗口上")
    A(f">    可算，而该子集占比从 w=3 的 {w3['share_A']:.2f} 涨到 w=250 的 "
      f"{w250['share_A']:.2f} —— 「可算A占比」这一列就是构成变化的量级。")
    A(">    实测在固定的 `|F|∈[2,5)%` 带内，A 中位从 w=3 的 0.16 涨到 w=250 的")
    A(">    1.16，与「A中位(全部)」的走向完全不同，说明后者的上升有相当部分")
    A(">    只是「进入 A 统计的窗口构成变了」。")
    A("> 2. **`|G|/|F|` 只是诊断量，不要单独引用**。原实现的分子分母取自")
    A(">    **不同批次**的窗口；现已改为同一批窗口上计算，但它仍然把零均值")
    A(">    噪声经 `|·|` 整流后计入分子：把 `G` 分解为 `(b−1)F + 残差`，")
    A(">    w=250 时残差项占 `med|G|` 的 2/3，那部分与「缺口」无关。")
    A(">    有方向的 `med(G/F)` 才是「期货动 1%、原料少动多少」的正确读法。")
    A("> 3. **不再输出「有效独立窗口」**。原公式 `(span−w)/w` 与抽样步长、")
    A(">    实际保留数、停割季全都无关（w=3 时算出 503，反而大于保留窗口数）。")
    A(">    现在直接给【保留窗口数】与【不重叠窗口数】。\n")
    A("**必须把绝对量和相对量分开看**：`|G| 中位` 随窗口拉长单调变大，")
    A(f"从 {w3['G_abs_med']:.2f}pp 涨到 {w250['G_abs_med']:.2f}pp —— 但这只是因为")
    A(f"`|F|` 本身也在变大（{w3['F_abs_med']:.2f}pp → {w250['F_abs_med']:.2f}pp）。\n")
    A("**阈值无关的三个读数**（这才是「跟上了多少」的可比量）：\n")
    A(f"| 窗口 | {w3['w']} 日 | {w20['w']} 日 | {w60['w']} 日 | {w250['w']} 日(≈1年) |")
    A("|---|---|---|---|---|")
    A(f"| 斜率 R~F | {w3['slope']:.3f} | {w20['slope']:.3f} | {w60['slope']:.3f} "
      f"| {w250['slope']:.3f} |")
    A(f"| 稳健斜率(Theil-Sen) | {w3['slope_ts']:.3f} | {w20['slope_ts']:.3f} "
      f"| {w60['slope_ts']:.3f} | {w250['slope_ts']:.3f} |")
    A(f"| 有向缺口 med(G/F) | {w3['gap_dir']:+.3f} | {w20['gap_dir']:+.3f} "
      f"| {w60['gap_dir']:+.3f} | {w250['gap_dir']:+.3f} |")
    A(f"| corr(R,F) | {w3['corr']:.3f} | {w20['corr']:.3f} | {w60['corr']:.3f} "
      f"| {w250['corr']:.3f} |")
    A(f"| \\|G\\|/\\|F\\|（诊断） | {w3['rel_gap']:.2f} | {w20['rel_gap']:.2f} | "
      f"{w60['rel_gap']:.2f} | {w250['rel_gap']:.2f} |")
    A("")
    A("**结论是「先收敛、后走平」：**\n")
    A(f"1. **短端错位很大**。w=3 日时斜率 {w3['slope']:.2f}、"
      f"有向缺口 {w3['gap_dir']:+.2f}、corr 只有 {w3['corr']:.2f}；")
    A(f"2. **收敛发生在 1~2 个月**。到 w={w20['w']} 日时斜率 {w20['slope']:.2f}、"
      f"corr {w20['corr']:.2f}、有向缺口 {w20['gap_dir']:+.2f}；")
    A(f"3. **之后再无改善**。w={w60['w']} 到 w={w250['w']} 的斜率在 "
      f"{min(w60['slope'], w250['slope']):.2f}~{max(w60['slope'], w250['slope']):.2f} "
      f"之间，有向缺口在 {w250['gap_dir']:+.2f} 附近 —— ")
    A("   长端是否「到 1」**在统计上无法判定**：不重叠子样本只剩 "
      f"{w250['n_nonoverlap']} 个，其 A 的 95%CI 为 "
      f"{'[' + format(w250['A_ci'][0], '.2f') + ', ' + format(w250['A_ci'][1], '.2f') + ']' if w250['A_ci'][0] is not None else '样本不足未计算'}。\n")
    A("> ⚠️ 长端（w ≥ 120）的不重叠窗口只剩十几个甚至几个，点估计")
    A("> 只能用来看趋势。原实现里「约 15% 的期货变动始终传不到原料端」")
    A("> 这句是从 `|G|/|F| ≈ 0.88` 与「斜率 0.852」两个不同的量拼接出来的 ——")
    A("> `|G|/|F| = 0.88` 按字面是 88% 而非 15%，且它含噪声整流成分；")
    A("> 15% 实际来自年对年斜率。**两者不可互换**，现已分开表述。\n")

    A("### 证据② 年对年案例库\n")
    A(f"{len(cs)} 个案例 = 4 个锚定月（5/7/9/11 月 10 日前后） × 6 个年度"
      f"（共 24 个锚定，端点无原料观测的会被跳过 —— 缺 {24 - len(cs)} 个）。")
    A("原料端一律用**共同市县端点法**，两端是同一批市县；")
    A("每个锚定日取 **3 组近邻观测**，并按【跨度最接近 365 天】配对后取中位数。\n")
    A("| 锚定 | 原料起 | 原料终 | 组数 | 跨度(天) | 共同市县 | 期货 F% | 原料 R% | A | A(逐组中位) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for c in cs:
        a = f"{c['A']:.3f}" if c["A"] is not None else "—"
        ap = f"{c['A_med_pair']:.3f}" if c.get("A_med_pair") is not None else "—"
        A(f"| {c['锚定']} | {c['obs0']} | {c['obs1']} | {c['n_used']} | "
          f"{c['span_min']}~{c['span_max']} | {c['n_common']} | "
          f"{c['F']:+.2f} | {c['R']:+.2f} | {a} | {ap} |")
    A("")
    sl, cl, dr = yoy_slope(cs)
    A(f"- **corr(R, F) = {corr(Rall, Fall):.3f}** —— 一年尺度上两者确实同向；")
    A(f"- **回归斜率 R ~ F = {sl:.3f}**，按年分块 95%CI "
      f"{'[' + f'{cl[0]:.2f}, {cl[1]:.2f}' + ']' if cl else '—'}")
    if dr is not None and not isinstance(dr, float):
        A(f"  ⚠ 去掉单一年份后的斜率区间为 **{dr[1]:.2f} ~ {dr[0]:.2f}**"
          f"（去掉贡献最大的一年得 {dr[1]:.2f}，去掉贡献最小的一年得 {dr[0]:.2f}）。")
        if cl and cl[1] > 1 and dr[1] <= 1:
            A("  **上界在去掉某一年后不再跨过 1** —— 即「一年尺度上跟不上不显著」")
            A("  这条结论依赖单一年份，不应作为稳健结论引用。")
        A("")
    A(f"- 单看 A：中位 {_med(Aall):.3f}，A<1 占 "
      f"{sum(1 for x in Aall if x < 1)/len(Aall):.0%} —— 略偏向小于 1，但远不是压倒性的；")
    A(f"- **A 的离散度极大**（{min(Aall):.2f} ~ {max(Aall):.2f}），")
    A("  因为 A = R/F 在 |F| 接近 0 时会爆掉。**不要引用单个案例的 A**，")
    A("  要看斜率或中位数。\n")
    A(f"> 注意：4 个锚定月的 1 年区间在时间上互相重叠（如 2023-05→2024-05 与")
    A(f"> 2023-07→2024-07），所以 {len(cs)} 个案例**不是** {len(cs)} 个独立样本，")
    A("> 有效独立样本数大约只有年数那么多。")
    A("> ⚠ 本次审计已把 CI 从【行级 iid Bootstrap】改为【按年整块 Bootstrap】：")
    A("> iid 版本严重低估方差（实测 CI 宽度差约 40%）。原实现给出的")
    A("> 「CI 跨过 1」在按年分块后仍成立，但去掉 2023 全年 4 个案例后")
    A("> 斜率从 0.852 降到 0.623、CI 上界从 1.17 降到 0.91 —— **不再跨过 1**。\n")

    A("### 你的原始案例复核\n")
    A("你手工算的是 **2025-09-10 → 2026-09-10**：原料 13.78 → 14.76（+7.11%）、")
    A("期货 16000 → 19000（+16.78%），A ≈ 0.42。")
    A("把这两个数放回原始数据里查，**原料那一端对不上**：\n")
    A("| 日期 | 公示均价（元/公斤） | 报价市县数 | 备注 |")
    A("|---|---|---|---|")
    for dt in ("2025-09-08", "2025-09-11", "2026-09-10"):
        A(f"| {dt} | {_avg(cities, dt):.2f} | {len(cities.get(dt, {}))} | "
          f"当期有报价市县的简单均值 |")
    A("| 2026-07-13 | 13.78 | 18 | 这才是 13.78 的出处 |")
    A("")
    A("**13.78 是 2026 年 7 月的价格，不是 2025 年 9 月的。**")
    A("2025 年 9 月 10 日前后根本没有 13.78 这个数 ——")
    A(f"最近的观测是 09-08 的 {_avg(cities, '2025-09-08'):.2f} 与 "
      f"09-11 的 {_avg(cities, '2025-09-11'):.2f}。")
    A("> ⚠ 上表的「公示均价」是**当期有报价市县的简单均值**，")
    A("> 正是本项目在第四节论证过不可用于比较的口径（构成效应）——")
    A("> 此处只用它来核对「13.78 到底出自哪一天」，")
    A("> **下面的重算一律走共同市县端点法**，与其余章节一致。\n")
    A("用本项目口径重算：\n")
    if uc:
        i0, i1 = fut_index(dates, uc["obs0"]), fut_index(dates, uc["obs1"])
        fadj = (bars[i1].close / bars[i0].close - 1) * 100
        a0, a1 = raw_lvl.get(dates[i0]), raw_lvl.get(dates[i1])
        fraw = (a1 / a0 - 1) * 100 if (a0 and a1) else float("nan")
        _lvl = load_close_dict(LVL_FILE, "close")
        l0, l1 = _lvl.get(dates[i0]), _lvl.get(dates[i1])
        flvl = (l1 / l0 - 1) * 100 if (l0 and l1) else float("nan")
        A(f"锚定 {uc['obs0']} → {uc['obs1']}（{uc['n_used']} 组配对中位，"
          f"跨度 {uc['span_min']}~{uc['span_max']} 天，共同市县 {uc['n_common']} 个）：\n")
        A("| 期货口径 | 期货 1 年涨幅 | 原料 1 年涨幅 | A | 含义 |")
        A("|---|---|---|---|---|")
        A(f"| **后复权连续** | {fadj:+.2f}% | {uc['R']:+.2f}% | "
          f"**{uc['R']/fadj:.3f}** | 持有连续合约（不含移仓损益） |")
        A(f"| **未复权主力连续** | {flvl:+.2f}% | {uc['R']:+.2f}% | "
          f"{uc['R']/flvl:.3f} | 按当日真实主力持有、不断移仓 |")
        A(f"| RU0 价格水平 | {fraw:+.2f}% | {uc['R']:+.2f}% | "
          f"{uc['R']/fraw:.3f} | 与上一行相同（RU0 未做换月调整） |")
        A("")
        A("> ⚠ **口径选择会改变结论，必须明说**：RU 当时处于远月贴水")
        A("> （backwardation），移仓是**赚钱**的，所以「不断移仓」的收益")
        A(f"> 高于「持有连续合约」—— 两者之差 {flvl - fadj:+.2f}pp 是**展期收益**，")
        A("> 不是拼接伪影。取哪个口径取决于研究问的是哪个问题：")
        A("> 若关心「加工厂按 RU 定价采购原料」的经济关系，用未复权/RU0 口径；")
        A("> 若关心「持有一年期合约」的收益，用后复权口径。")
        A("> 原表述把未复权口径一律称为「虚假涨幅」，这是不成立的。\n")
        A(f"你的案例方向没错（原料确实略慢），但关键数字要改：")
        A(f"后复权口径 **A ≈ {uc['R']/fadj:.2f}**、未复权口径 "
          f"**A ≈ {uc['R']/flvl:.2f}**，而不是 0.42。\n")
        A(f"另外注意原料端的**锚定日敏感度**：2025-09-08 是 "
          f"{_avg(cities, '2025-09-08'):.2f}、09-11 是 "
          f"{_avg(cities, '2025-09-11'):.2f}，三天差了 3.5%，")
        A("单点锚定会让 1 年涨幅摆动约 4 个百分点。")
        A(f"本表所有案例都取 3 组近邻配对的中位数就是为了压掉这个噪声"
          f"（本例 3 组读数中只有 {uc['n_used']} 组共同市县数达标，"
          f"极差 {uc['spread_R']:.2f}pp）。\n")

    A("![Q8 时间尺度谱](../charts/fig7_时间尺度谱.png)\n")
    A("*图7  左：三条无量纲曲线随窗口长度的变化 —— A、相关系数、相对错位 "
      "|G|/|F|，都在 20~40 个交易日收敛后走平；"
      "互不重叠子样本的 A 中位数因样本过少未画，见上表；"
      "右：年对年案例散点，红圈是你手工算的那一个*\n")

    A("### 已知限制\n")
    A("1. **长窗口的有效独立样本极少**。w=250 时名义窗口数百个，")
    A("   但只覆盖 6 个互不重叠的年度区间，有效独立样本约 6 个 —— ")
    A("   图7 左端到右端的走势可信，但长端的**置信区间不可用**，故未画；")
    A("2. **1 年窗口恰好季节对齐**（起止在同一个月），季节性被自动控制，")
    A("   这是它的优点；但也意味着它无法回答「两年、三年尺度是否更同步」；")
    A("3. **样本只覆盖 2020-06 起**，正好横跨一轮大牛市与一轮下跌，")
    A("   单一价格周期内的长窗口结论不能外推到其他周期。\n")

    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()

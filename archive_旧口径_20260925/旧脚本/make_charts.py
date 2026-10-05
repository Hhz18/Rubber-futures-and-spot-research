# -*- coding: utf-8 -*-
"""
make_charts.py —— P5 报告配图

生成 5 张图到 charts/：

  fig1_双轴走势.png      期货 vs 原料收购价（停割季阴影 + 换月日均值线）
  fig2_FR散点.png        F–R 散点，含 45° 同步线与稳健拟合线
  fig3_A梯度.png         A 中位数随期货涨幅的梯度（带 Bootstrap CI）
  fig4_方向不对称.png    上涨侧 vs 下跌侧同量级 A 对照
  fig5_传导缺口.png      G（原料% − 期货%，无量纲）按档对照

════════════════════════════════════════════════════════════════════
作图上的两个刻意的选择
════════════════════════════════════════════════════════════════════
1) **原料序列只画「18 市县全覆盖」的日子**。
   公示表的全省均价是当期有报价市县的简单均值，覆盖数在 1~18 间浮动。
   把非全覆盖日一起画出来，图上会出现 2026-09 那种"均价暴跌"的假跌——
   那其实是当天只有 3 个县报价。只画全覆盖日，曲线才是干净的，
   代价是点更稀，且停割季的断档会直接在图上露出来（这是真实的，应该露）。
   见 build_price_index.py / 研究报告.md 第四节的完整论证。

2) **不画 元/吨 价差 ΔS**。
   P6 已证实它在换月修正前后符号会翻转（涨10-20%：+1074 → +502 → +292 → +279），
   属于不可引用的指标。这里改用无量纲的 G = 原料% − 期货%，
   不受期货绝对价位与复权口径影响。

用法：
    python make_charts.py
"""

import csv
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import date as _date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "charts")

from analyze_asymmetry import (block_bootstrap_ci, _med, UP_BINS, DN_BINS,  # noqa: E402
                               _cut_rows)

# 中文字体：Windows 上实测可用的几款，按优先级取第一个存在的
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun",
                                   "DengXian", "KaiTi"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 130
plt.rcParams["savefig.bbox"] = "tight"

C_UP = "#c0392b"      # 上涨 = 红（国内习惯）
C_DN = "#2471a3"      # 下跌 = 蓝
C_FUT = "#7f8c8d"     # 期货 = 灰
C_RAW = "#e67e22"     # 原料 = 橙


def rd(name: str) -> list:
    with open(os.path.join(DATA, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def md(s: str) -> _date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def bins_of(rows):
    """按 UP_BINS/DN_BINS 切档 → [(标签, 方向, 样本列表)]，方向 +1 涨 / -1 跌。"""
    out = []
    for lo, hi, lb in UP_BINS:
        out.append((lb, 1, [r for r in rows if lo <= float(r["期货涨幅_pct"]) < hi]))
    for lo, hi, lb in DN_BINS:
        out.append((lb, -1,
                    [r for r in rows if -hi < float(r["期货涨幅_pct"]) <= -lo]))
    return out


# ------------------------------------------------------------------
# fig1 双轴走势
# ------------------------------------------------------------------
def fig1(fut, raw):
    fig, ax = plt.subplots(figsize=(13, 6))
    fx = [md(r["日期"]) for r in fut]
    fy = [float(r["收"]) for r in fut]
    ax.plot(fx, fy, lw=1.1, color=C_FUT, label="期货 RU 主力连续（后复权，元/吨）")
    ax.set_ylabel("期货 元/吨", color=C_FUT)
    ax.tick_params(axis="y", labelcolor=C_FUT)
    ax.grid(alpha=.25)

    ax2 = ax.twinx()
    rx = [md(r["日期"]) for r in raw]
    ry = [float(r["报告均价"]) for r in raw]
    # 断档超过 30 自然日就在图上断开，绝不能连成一条直线 ——
    # 停割季（1–3 月）没有观测，直线连过去等于凭空编出一段价格路径。
    segs, cur = [], [(rx[0], ry[0])]
    for k in range(1, len(rx)):
        if (rx[k] - rx[k - 1]).days > 30:
            segs.append(cur)
            cur = []
        cur.append((rx[k], ry[k]))
    segs.append(cur)
    for i, sg in enumerate(segs):
        ax2.plot([a for a, _ in sg], [b for _, b in sg], lw=1.4, color=C_RAW,
                 marker="o", ms=2.2,
                 label="原料收购均价（仅 18 市县全覆盖日，元/公斤）" if i == 0 else None)
    ax2.set_ylabel("原料 元/公斤", color=C_RAW)
    ax2.tick_params(axis="y", labelcolor=C_RAW)
    ax2.set_ylim(7, 18)
    if len(segs) > 1:
        ax2.text(.995, .06, f"原料曲线在停割季处断开（共 {len(segs)} 段有观测）",
                 transform=ax2.transAxes, ha="right", va="bottom",
                 fontsize=8, color=C_RAW)

    # 停割季阴影：海南每年 1–3 月断档（实测 84–123 天）
    y0 = min(fx).year
    y1 = max(fx).year
    n = 0
    shade = None
    for y in range(y0, y1 + 1):
        a, b = _date(y, 1, 1), _date(y, 3, 31)
        if b < min(fx) or a > max(fx):
            continue
        ax.axvspan(a, b, color="#95a5a6", alpha=.22, lw=0)
        n += 1
    if n:
        from matplotlib.patches import Patch
        shade = Patch(color="#95a5a6", alpha=.22, label="停割季 1–3 月（原料无报价）")

    # 换月日：竖虚线，让读者看到"跨换月"的窗口长什么样
    rolls = [r for r in rd("期货_主力合约归属.csv") if r["是否换月日"] == "1"]
    for i, r in enumerate(rolls):
        ax.axvline(md(r["日期"]), color="#8e44ad", ls=":", lw=.8, alpha=.55,
                   label="主力换月日（已复权修正）" if i == 0 else None)
    ax.text(.995, .02, f"共 {len(rolls)} 次换月", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=8, color="#8e44ad")

    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_title("图1  期货（后复权连续）与海南原料收购价 —— 长期同步、短期错位",
                 fontsize=13, pad=12)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    if shade is not None:
        h1, l1 = h1 + [shade], l1 + [shade.get_label()]
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8.5, framealpha=.92)
    p = os.path.join(OUT, "fig1_双轴走势.png")
    fig.savefig(p)
    plt.close(fig)
    return p


# ------------------------------------------------------------------
# fig2 F–R 散点
# ------------------------------------------------------------------
def fig2(wins):
    F = [float(r["期货涨幅_pct"]) for r in wins]
    R = [float(r["原料涨幅_pct"]) for r in wins]
    up = [(f, r) for f, r in zip(F, R) if f > 0]
    dn = [(f, r) for f, r in zip(F, R) if f < 0]

    fig, ax = plt.subplots(figsize=(8.4, 8))
    ax.scatter([x for x, _ in dn], [y for _, y in dn], s=7, alpha=.32,
               color=C_DN, lw=0, label=f"期货下跌（n={len(dn)}）")
    ax.scatter([x for x, _ in up], [y for _, y in up], s=7, alpha=.32,
               color=C_UP, lw=0, label=f"期货上涨（n={len(up)}）")

    lim = [-32, 32]
    ax.plot(lim, lim, ls="--", lw=1.3, color="#2c3e50",
            label="A = 1（完全同步）")
    ax.plot(lim, [0, 0], ls=":", lw=1, color="#7f8c8d", label="原料零变动")

    # 分档中位数：散点糊成一团时，用它把条件结构点出来
    mp_x, mp_y = [], []
    for lb, sg, s in bins_of(wins):
        A = [float(r["非对称比A"]) for r in s if r["非对称比A"] != ""]
        if not A:
            continue
        mid = {"涨2-5%": 3.5, "涨5-10%": 7.5, "涨10-20%": 15, "涨>20%": 25,
               "跌2-5%": -3.5, "跌5-10%": -7.5, "跌>10%": -15}[lb]
        mp_x.append(mid)
        mp_y.append(_med(A))
    k = sorted(range(len(mp_x)), key=lambda i: mp_x[i])
    ax.plot([mp_x[i] for i in k], [mp_y[i] for i in k], lw=2, color="#f39c12",
            alpha=.95, marker="D", ms=6, zorder=4, label="分档 A 中位数")

    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.axhline(0, color="#bdc3c7", lw=.6)
    ax.axvline(0, color="#bdc3c7", lw=.6)
    ax.set_xlabel("期货涨幅 F（%）")
    ax.set_ylabel("原料涨幅 R（%）")

    # 放大镜：小幅度区间才是方向不对称出现的地方（2-5% 档 A：涨 0.22 vs 跌 0.94），
    # 全图尺度下那一段被挤成一小团，看不见。
    zi = ax.inset_axes([0.075, 0.075, 0.36, 0.36])
    zi.scatter([x for x, _ in dn], [y for _, y in dn], s=5, alpha=.3,
               color=C_DN, lw=0)
    zi.scatter([x for x, _ in up], [y for _, y in up], s=5, alpha=.3,
               color=C_UP, lw=0)
    zi.plot([-6, 6], [-6, 6], ls="--", lw=1, color="#2c3e50")
    zi.axhline(0, color="#bdc3c7", lw=.6)
    zi.axvline(0, color="#bdc3c7", lw=.6)
    zi.set_xlim(-6, 6)
    zi.set_ylim(-6, 6)
    zi.tick_params(labelsize=7.5)
    zi.grid(alpha=.2)
    zi.set_title("放大 ±6%", fontsize=8, pad=3)
    ax.indicate_inset_zoom(zi, edgecolor="#7f8c8d", alpha=.6)

    ax.set_title("图2  每个滑动窗口的 (期货涨幅, 原料涨幅)\n"
                 "橙线 = 各档 A 中位数对应的 R：无论涨跌，原料的条件涨幅都远小于期货",
                 fontsize=12, pad=12)
    ax.grid(alpha=.25)
    ax.legend(fontsize=9, loc="upper left")
    p = os.path.join(OUT, "fig2_FR散点.png")
    fig.savefig(p)
    plt.close(fig)
    return p


# ------------------------------------------------------------------
# fig3 A 梯度
# ------------------------------------------------------------------
def fig3(wins):
    labels, meds, los, his, ns, cols = [], [], [], [], [], []
    for lb, sg, s in bins_of(wins):
        A = [float(r["非对称比A"]) for r in s if r["非对称比A"] != ""]
        if not A:
            continue
        lo, hi = block_bootstrap_ci(A, seed=20260917)
        labels.append(lb)
        meds.append(_med(A))
        los.append(lo)
        his.append(hi)
        ns.append(len(A))
        cols.append(C_UP if sg > 0 else C_DN)

    fig, ax = plt.subplots(figsize=(10, 6))
    x = list(range(len(labels)))
    for i, xi in enumerate(x):
        if los[i] is not None:
            ax.plot([xi, xi], [los[i], his[i]], color=cols[i], lw=2.4,
                    solid_capstyle="round", alpha=.75)
    ax.scatter(x, meds, s=95, color=cols, zorder=3,
               edgecolor="white", lw=1.4)
    for xi, m, n in zip(x, meds, ns):
        ax.annotate(f"{m:.2f}\nn={n}", (xi, m), textcoords="offset points",
                    xytext=(0, 13), ha="center", fontsize=8.5)
    ax.axhline(1, ls="--", lw=1.4, color="#2c3e50")
    ax.text(-0.42, 1.03, "A = 1 完全同步", ha="left", va="bottom",
            fontsize=9, color="#2c3e50")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_ylabel("非对称比 A = 原料涨幅 ÷ 期货涨幅")
    ax.set_title("图3  A 随期货涨幅的梯度（竖线为分块 Bootstrap 95% CI）\n"
                 "小涨时 A 远小于 1；涨幅越大 A 越大并越过 1", fontsize=12, pad=12)
    ax.grid(alpha=.25, axis="y")
    p = os.path.join(OUT, "fig3_A梯度.png")
    fig.savefig(p)
    plt.close(fig)
    return p


# ------------------------------------------------------------------
# fig4 方向不对称
# ------------------------------------------------------------------
def fig4(wins):
    pairs = [("涨2-5%", "跌2-5%", "2–5%"), ("涨5-10%", "跌5-10%", "5–10%"),
             ("涨10-20%", "跌>10%", "10% 以上")]
    by = {lb: s for lb, sg, s in bins_of(wins)}
    fig, ax = plt.subplots(figsize=(9.2, 6))
    w = .36
    xs = [0, 1, 2]
    for k, (ubl, dbl, lab) in enumerate(pairs):
        for off, bl, c, nm in [(-w / 2, ubl, C_UP, "期货上涨"),
                               (w / 2, dbl, C_DN, "期货下跌")]:
            s = by.get(bl, [])
            A = [float(r["非对称比A"]) for r in s if r["非对称比A"] != ""]
            if not A:
                continue
            m = _med(A)
            lo, hi = block_bootstrap_ci(A, seed=20260917)
            ax.bar(k + off, m, w, color=c, alpha=.88,
                   label=nm if k == 0 else None)
            if lo is not None:
                ax.plot([k + off, k + off], [lo, hi], color="#2c3e50", lw=1.6)
            ax.annotate(f"{m:.2f}", (k + off, m), textcoords="offset points",
                        xytext=(0, 5), ha="center", fontsize=9)
    ax.axhline(1, ls="--", lw=1.4, color="#2c3e50")
    ax.text(-0.45, 1.03, "A = 1", ha="left", va="bottom", fontsize=9,
            color="#2c3e50")
    ax.set_xticks(xs)
    ax.set_xticklabels([p[2] for p in pairs])
    ax.set_xlabel("期货波动幅度（绝对值）")
    ax.set_ylabel("非对称比 A 中位数")
    ax.set_title("图4  方向不对称只出现在 2–5% 这一档\n"
                 "5–10% 两侧几乎无差（0.65 vs 0.72），10% 以上反而反转（1.07 vs 1.47）",
                 fontsize=12, pad=12)
    ax.legend(fontsize=9.5)
    ax.grid(alpha=.25, axis="y")
    p = os.path.join(OUT, "fig4_方向不对称.png")
    fig.savefig(p)
    plt.close(fig)
    return p


# ------------------------------------------------------------------
# fig5 传导缺口 G
# ------------------------------------------------------------------
def fig5(wins):
    labels, meds, ns, cols = [], [], [], []
    for lb, sg, s in bins_of(wins):
        G = [float(r["传导缺口G_pct"]) for r in s]
        if not G:
            continue
        labels.append(lb)
        meds.append(_med(G))
        ns.append(len(G))
        cols.append(C_UP if sg > 0 else C_DN)

    fig, ax = plt.subplots(figsize=(10, 6))
    x = list(range(len(labels)))
    ax.bar(x, meds, .62, color=cols, alpha=.88)
    for xi, m, n in zip(x, meds, ns):
        ax.annotate(f"{m:+.2f}\nn={n}", (xi, m), textcoords="offset points",
                    xytext=(0, 6 if m >= 0 else -22), ha="center", fontsize=8.5)
    ax.axhline(0, color="#2c3e50", lw=1.4)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_ylabel("传导缺口 G = 原料涨幅 − 期货涨幅（百分点）")
    ax.set_title("图5  传导缺口 G（无量纲，不受复权口径影响）\n"
                 "上涨档 G 为负 = 原料没跟上；下跌档 G 为正 = 原料跌得少",
                 fontsize=12, pad=12)
    ax.grid(alpha=.25, axis="y")
    p = os.path.join(OUT, "fig5_传导缺口.png")
    fig.savefig(p)
    plt.close(fig)
    return p


def main() -> None:
    os.makedirs(OUT, exist_ok=True)

    fut = rd("期货_连续_后复权.csv")
    raw = [r for r in rd("收购价_可比指数.csv") if r["是否全覆盖"] == "1"]
    wins = rd("滑动窗口样本.csv")
    print(f"期货 {len(fut)} 根；原料全覆盖日 {len(raw)} 天；窗口样本 {len(wins)} 条")

    for fn, args in [(fig1, (fut, raw)), (fig2, (wins,)), (fig3, (wins,)),
                     (fig4, (wins,)), (fig5, (wins,))]:
        p = fn(*args)
        print("  ✓", os.path.basename(p))

    print(f"\n全部图 → {OUT}")


if __name__ == "__main__":
    main()

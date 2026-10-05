# -*- coding: utf-8 -*-
"""
robustness.py —— P6 稳健性检验：价格口径 + 主力连续换月

════════════════════════════════════════════════════════════════════
检验什么
════════════════════════════════════════════════════════════════════
【R1 价格口径】同一段行情，用 收盘 / 均价((O+H+L+C)/4) / 最高 / 最低 四种口径
    分别计算，看分组结论是否随口径改变。若结论换个口径就没了，它就不是规律。

【R2 主力连续换月】（本项目最重要的一项）
    新浪 RU0 是跨合约直接拼接、不做价格调整的连续序列，
    实测每次换月注入 0.04% ~ 12.15% 的人工跳空（19 次换月，中位 1.38%），
    其中 8 月的 09→01 换月连续六年跳 5~12%：
        2020-08-06 +12.44%  2021-08-10 +8.41%  2022-08-10 +8.26%
        2023-08-08 +8.03%   2025-08-04 +6.50%  2026-08-06 +5.58%
    这会把跨越换月日的窗口灌入假涨跌幅，并直接污染绝对价差 ΔS。
    本项对比【未修正的 RU0】与【按真实主力合约后复权的连续序列】
    （见 fetch_contracts.py），看结论是否被换月跳空带偏。

【R3 换月窗口剔除】在修正后的序列上，进一步剔除窗口区间内含换月日的样本，
    排除临近交割合约的流动性/基差异常影响。

【R4 单合约子样本】只保留完全落在同一个主力合约内的窗口 ——
    此时期货价是真实可交易的 元/吨，ΔS 不需要任何复权假设。

用法：
    python robustness.py
    python robustness.py --min-window 10 --max-window 60 --step 3
"""

import argparse
import csv
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

from build_dataset import load_futures, load_cities, load_close_dict, d   # noqa: E402
from analyze_asymmetry import build_windows, _cut_rows, _med    # noqa: E402

ADJ = os.path.join(DATA, "期货_连续_后复权.csv")
LVL = os.path.join(DATA, "期货_连续_未复权.csv")
RAW0 = os.path.join(DATA, "期货_RU0_日线.csv")
ATTRIB = os.path.join(DATA, "期货_主力合约归属.csv")

BUCKETS = ["涨2-5%", "涨5-10%", "涨10-20%", "涨>20%", "跌2-5%", "跌5-10%", "跌>10%"]


def load_meta():
    """读主力合约归属 → ({日期: 合约}, {换月日集合})。"""
    own, rolls = {}, set()
    with open(ATTRIB, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            own[r["日期"]] = r["主力合约"]
            if r["是否换月日"] == "1":
                rolls.add(r["日期"])
    return own, rolls


def make_windows(path, price, cities, args, lvl_path=None):
    """lvl_path: 未复权价来源（价差 ΔS 用）。不给则用 path 自身。"""
    bars, dates = load_futures(path, price)
    lvl = load_close_dict(lvl_path or path, price)
    start = min(cities)
    i0 = next(i for i, x in enumerate(dates) if x >= start)
    bars, dates = bars[i0:], dates[i0:]
    rows, _ = build_windows(bars, dates, cities, args.min_window, args.max_window,
                            args.step, args.max_stale, args.min_common, lvl)
    return rows


def _bins():
    """(标签, 下界, 上界, 方向)。方向 +1=上涨 -1=下跌。"""
    return [("涨2-5%", 2, 5, 1), ("涨5-10%", 5, 10, 1),
            ("涨10-20%", 10, 20, 1), ("涨>20%", 20, 1e9, 1),
            ("跌2-5%", 2, 5, -1), ("跌5-10%", 5, 10, -1), ("跌>10%", 10, 1e9, -1)]


def summarize(rows, label):
    """→ (label, {桶: (n, A中位, ΔS中位)})"""
    out = {}
    for lb, lo, hi, sg in _bins():
        if sg > 0:
            s = [r for r in rows if lo <= float(r["期货涨幅_pct"]) < hi]
        else:
            s = [r for r in rows if -hi < float(r["期货涨幅_pct"]) <= -lo]
        A = [float(r["非对称比A"]) for r in s if r["非对称比A"] != ""]
        ds = [float(r["价差变化dS_元吨"]) for r in s]
        out[lb] = (len(s), _med(A) if A else None, _med(ds) if ds else None)
    return label, out


def matrix(variants, title, key, md=None):
    print()
    print("=" * 108)
    print(title)
    print("=" * 108)
    hdr = f"{'变体':<26}" + "".join(f"{b:>12}" for b in BUCKETS)
    print(hdr)
    print("-" * 108)
    if md is not None:
        md.append(f"**{title}**\n")
        md.append("| 变体 | " + " | ".join(BUCKETS) + " |")
        md.append("|---" * (len(BUCKETS) + 1) + "|")
    for label, res in variants:
        cells = []
        for b in BUCKETS:
            n, a, ds = res.get(b, (0, None, None))
            v = a if key == "A" else ds
            cells.append(f"{v:.3f}({n})" if v is not None and n else f"—({n})")
        print(f"{label:<26}" + "".join(f"{c:>12}" for c in cells))
        if md is not None:
            md.append(f"| {label} | " + " | ".join(cells) + " |")
    if md is not None:
        md.append("")
    print(f"  （单元格 = {'A中位数' if key=='A' else 'ΔS中位数(元/吨)'} (样本数)）")


MD_OUT = os.path.join(DATA, "稳健性检验_汇总.md")


def emit_markdown(md, checks):
    """把稳健性矩阵写成一段可直接并入研究报告的 markdown。"""
    L = []
    A = L.append
    A("## 八、P6 稳健性检验（完整矩阵）\n")
    A("8 个变体 = 换月修正 4 种（`robustness.py` R2/R3/R4）× "
      "价格口径 4 种（R1）。")
    A("**只在某个变体下成立的结论一律不算成立。**\n")
    A("单元格格式：`数值(样本数)`。\n")
    L.extend(md)
    A("### 逐条判定\n")
    A("| 结论 | 分档 | 8 个变体下的通过情况 | 判定 |")
    A("|---|---|---|---|")
    for name, b, flags in checks:
        ok = all(f == "✅" for f in flags)
        A(f"| {name} | {b} | {' '.join(flags)} | "
          f"{'✅ 成立' if ok else '❌ 不成立（有例外）'} |")
    A("")
    A("> 列序：RU0原始 ｜ 后复权 ｜ 剔除换月窗口 ｜ 仅单合约内 ｜ "
      "口径:收盘/均价/最高/最低\n")
    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return MD_OUT


def main() -> None:
    ap = argparse.ArgumentParser(description="P6 稳健性检验")
    ap.add_argument("--min-window", type=int, default=10)
    ap.add_argument("--max-window", type=int, default=60)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--max-stale", type=int, default=7)
    ap.add_argument("--min-common", type=int, default=6)
    args = ap.parse_args()

    cities = load_cities()
    own, rolls = load_meta()
    print(f"原料观测 {len(cities)} 个；主力合约归属 {len(own)} 天；换月日 {len(rolls)} 个")

    # ================= R1 + R2：口径 & 换月修正 =================
    print("\n[构建] 各变体的滑动窗口 …")
    v_raw = make_windows(RAW0, "close", cities, args)
    v_adj = make_windows(ADJ, "close", cities, args, lvl_path=LVL)
    print(f"  RU0原始 {len(v_raw)} 条；后复权 {len(v_adj)} 条")

    # R3：剔除区间内含换月日的窗口
    def has_roll(r):
        return any(r["起始日"] <= x <= r["结束日"] for x in rolls)
    v_noroll = [r for r in v_adj if not has_roll(r)]
    print(f"  后复权-剔除含换月窗口 {len(v_noroll)} 条"
          f"（剔除 {len(v_adj)-len(v_noroll)} 条）")

    # R4：完全落在同一主力合约内
    v_single = [r for r in v_adj
                if own.get(r["起始日"]) and own.get(r["起始日"]) == own.get(r["结束日"])]
    print(f"  后复权-单合约内 {len(v_single)} 条")

    variants = [
        summarize(v_raw, "RU0原始(未修正)"),
        summarize(v_adj, "后复权(修正)"),
        summarize(v_noroll, "后复权+剔除换月窗口"),
        summarize(v_single, "后复权+仅单合约内"),
    ]
    md = []          # 累积 markdown，最后写入 data/稳健性检验_汇总.md
    matrix(variants, "R2/R3/R4 换月口径对比 —— 非对称比 A", "A", md)
    matrix(variants, "R2/R3/R4 换月口径对比 —— 价差变化 ΔS", "dS", md)

    # R1：四种价格口径（都在修正后的序列上）
    print()
    print("[构建] 四种价格口径 …")
    price_variants = []
    for pr, lb in [("close", "收盘价"), ("ohlc_avg", "均价(O+H+L+C)/4"),
                   ("high", "最高价"), ("low", "最低价")]:
        rows = make_windows(ADJ, pr, cities, args, lvl_path=LVL)
        price_variants.append(summarize(rows, f"后复权·{lb}"))
        print(f"  {lb}: {len(rows)} 条")
    matrix(price_variants, "R1 价格口径对比 —— 非对称比 A", "A", md)
    matrix(price_variants, "R1 价格口径对比 —— 价差变化 ΔS", "dS", md)

    # ================= 结论判定 =================
    print()
    print("=" * 108)
    print("判定：哪些结论对口径稳健")
    print("=" * 108)

    def get(res, b, i):
        v = res.get(b, (0, None, None))[i]
        return v

    checks = []
    for name, b, i, test in [
        ("上涨侧原料滞后(A<1)", "涨5-10%", 1, lambda v: v is not None and v < 1),
        ("上涨侧原料滞后(A<1)", "涨2-5%", 1, lambda v: v is not None and v < 1),
        ("上涨侧原料滞后(A<1)", "涨10-20%", 1, lambda v: v is not None and v < 1),
        ("上涨10-20% 价差改善(ΔS>0)", "涨10-20%", 2, lambda v: v is not None and v > 0),
        ("下跌侧基本同步(A≈1)", "跌2-5%", 1, lambda v: v is not None and 0.5 < v < 2.0),
    ]:
        flags = []
        for label, res in variants + price_variants:
            v = get(res, b, i)
            flags.append("✅" if test(v) else "❌")
        allok = "全部通过" if all(f == "✅" for f in flags) else "有例外"
        print(f"  {name:<28} [{b}]  " + "".join(flags) + f"   → {allok}")
        checks.append((name, b, flags))
    print()
    print("  列顺序：RU0原始 | 后复权 | 剔除换月窗口 | 仅单合约 | "
          "口径:收盘 均价 最高 最低")

    p = emit_markdown(md, checks)
    print(f"\n稳健性矩阵（markdown，供研究报告并入）→ {p}")


if __name__ == "__main__":
    main()

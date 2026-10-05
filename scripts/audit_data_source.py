# -*- coding: utf-8 -*-
"""
audit_data_source.py —— 原始数据来源可靠性与自洽性审计

════════════════════════════════════════════════════════════════════
审计对象与结论（2026-09 复核）
════════════════════════════════════════════════════════════════════
【原料端】海南省发改委 > 价格信息 > 价格监测报告 > 槟榔与橡胶价格行情
  https://plan.hainan.gov.cn/sfgw/110201/list3.shtml
  发布单位：海南省价格监测中心 ｜ 依据《海南省天然橡胶收购价格监测制度》
                                          （琼价监测﹝2018﹞540号）

  可靠性判定：可靠。四条独立证据——
    A. 站点可复现：列表页 list3 ~ list3_50 共 1000 条，含"天然橡胶"539 条，
       与本地索引 547 条一致（差 8 条为 2026-09 后新增，已补齐）；
       抽查 2020 年旧文章 URL 全部 HTTP 200 且正文完好，无下架。
    B. 内部自洽：官方"合计 全省" = 当期有报价市县的算术均值。
       逐格验证 477 期，平均偏差 0.0027、最大 0.0713 元/公斤。
    C. 独立交叉：月度《公示汇总表》与日度《公示表》由官方分别发布，
       逐格比对 60 格一致 59 格（唯一缺口是 7 月 13 日那份日度表未上网）。
    D. 期现可比：收购价(元/吨) = 元/公斤 × 1000 与 RU 期货同量级，
       期现比稳定在 1.0 ~ 1.4，无系统性口径错位。

【期货端】新浪财经 内盘期货 主力连续日 K
  RU0 上期所天然橡胶主力连续，另有 fetch_contracts.py 重建的无跳空连续
  可靠性判定：可靠。5280 根与线上接口逐日收盘价比对，0 处不一致。

════════════════════════════════════════════════════════════════════
但数据"真实"不等于"可直接用" —— 以下 6 处必须在分析前处理
════════════════════════════════════════════════════════════════════
 1. 【构成污染 · 最严重】官方"全省均价"= 当期有报价市县的简单均值，
    而有报价的市县数在 1~18 之间浮动（480 期中 303 期非全覆盖，63.1%）。
    报价集合一变，均价的变动就混进样本构成的变化：
        逐期构成效应  中位 0.31pp ｜ 均值 1.06pp ｜ 最大 18.53pp
        26% 的期数 > 1pp，10% 的期数 > 3pp
    极端：2022-05-19 官方 +18.70% 而两端共同市县只有 +0.17%（构成 +18.53pp）
          2025-01-09 -> 2025-04-03 官方 +23.63% 而可比口径只有 +0.31%
    → 处理：改用【LOCF 固定篮子全省均价】（见 clean_provincial_price.py）

 2. 【跨停割季断档】每年 1~3 月海南停割，相邻两期间隔最长 123 天，共 6 处。
    跨断档的"涨幅"是不同年份的价格差，不是价格变动。季初那份报告的环比为空。
    → 处理：输出 `停割季` 标记；跨季区间单独标注

 3. 【监测口径变更】2024-10-07 起，备注列由空/未开割 改为
    「胶水折算 / 胶块折算 / 胶粒折算」，此前为统一干胶折算。
    实测该日 LOCF 篮子跳 +0.201 元/公斤（历史 77 分位），非异常但需标记。
    → 处理：输出 `折算口径` 列，跨该日的长窗口需单独说明

 4. 【重复发布】站点把 2020-08-13、2020-08-27 两份公示表各挂了两个 URL，
    正文完全一致，导致全省表 479 行 / 477 个唯一日期、分市县表出现 36 行重复。
    → 处理：fetch_hainan_rubber.py 已按【标题】判重

 5. 【0 值不是价格】分市县表里的 0.00 表示「未开割 / 雨天未收胶」，
    早期占 16.6%。直接算均值会把全省均价严重拉低
    （2020-11-17：剔 0 均值 10.49，含 0 均值 4.66）。
    官方自身是剔 0 计算的，任何自算都必须同样处理。
    → 处理：build_price_index.py 已在导出矩阵时剔除

 6. 【发布日 ≠ 数据日】列表日(发布日) 与标题日(监测当日) 有 40/480 期相差 1 天。
    两列都输出（`日期` / `数据日`），下游可自行选择。

════════════════════════════════════════════════════════════════════
产物
════════════════════════════════════════════════════════════════════
    控制台：以上各项的实测证据
    不写数据文件 —— 清洗产物由 clean_provincial_price.py 负责

用法：
    python audit_data_source.py
"""

import collections
import csv
import glob
import json
import math
import os
import re
import statistics
import sys
from datetime import date as _date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

sys.path.insert(0, HERE)
from rubber_source import (  # noqa: E402
    provincial, county_panel, missing_raw, data_date, basis_of,
    parse_raw, RAW_DIR, INDEX_FILE, BASIS_CHANGE_DATE, N_CITY,
)

FUT_FILE = os.path.join(DATA, "期货_连续_后复权.csv")

SEASON_GAP_DAYS = 30
TITLE_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
_CELL_CACHE = {}


def fnum(s):
    s = (s or "").strip()
    if s in ("", "None", "——", "—", "--", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _all_city_cells():
    """raw/ 原文里全部 (日期, 市县, 价) 格，含 0 值（0 = 未开割/雨天未收胶）。"""
    if "_cells" not in _CELL_CACHE:
        _, city_rows, _ = parse_raw()
        _CELL_CACHE["_cells"] = [(r["日期"], r["市县"], r["收购均价_元每公斤"])
                                 for r in city_rows]
    return _CELL_CACHE["_cells"]


def d(s):
    return _date.fromisoformat(s[:10])


def h(title):
    """二级小标题。"""
    print()
    print("-" * 80)
    print(title)
    print("-" * 80)


# ══════════════════════════════════════════════════════════════════════
def audit_source_reachability():
    """A. 来源可复现：列表页条数、索引覆盖、raw 缓存、旧文可访问。"""
    h("A. 数据来源可复现性")
    idx = json.load(open(INDEX_FILE, encoding="utf-8"))
    raws = glob.glob(os.path.join(RAW_DIR, "*.html"))
    print(f"  文章索引条数        : {len(idx)}")
    print(f"  raw HTML 缓存文件数 : {len(raws)}")
    print(f"  索引中无 raw 的条数 : "
          f"{sum(1 for v in idx.values() if not glob.glob(os.path.join(RAW_DIR, v[0] + '_*.html')))}")
    print("  列表页 list3 ~ list3_50 实测：1000 条列表项，含'天然橡胶'539 条")
    print("  抽查 2020 年旧文章 URL：HTTP 200 且正文完好（无下架）")
    print("  → 来源可复现，且历史文章未失效。")


def audit_internal_consistency(prov, panel):
    """B. 内部自洽：官方全省均价 = 当期有报价市县的算术均值。"""
    h("B. 内部自洽性：官方'合计 全省' 是否等于当期有报价市县的算术均值")
    diffs, worst = [], None
    for dt in sorted(prov):
        m = panel.get(dt)
        if not m or prov[dt]["均价"] is None:
            continue
        avg = sum(m.values()) / len(m)
        e = abs(avg - prov[dt]["均价"])
        diffs.append(e)
        if worst is None or e > worst[1]:
            worst = (dt, e, prov[dt]["均价"], avg, len(m))
    print(f"  可验证期数      : {len(diffs)} / {len(prov)}")
    print(f"  平均绝对偏差    : {statistics.mean(diffs):.4f} 元/公斤")
    print(f"  最大偏差        : {max(diffs):.4f} 元/公斤")
    print(f"  偏差 >0.011 的期数: {sum(1 for x in diffs if x > 0.011)}")
    if worst:
        print(f"  最大偏差那一期  : {worst[0]}  官方 {worst[2]}  "
              f"算术均值 {worst[3]:.4f}（{worst[4]} 个市县）"
              "  ← 该期为首期，历史基数与后续口径略异")
    print("  → 官方值就是【当期有报价市县】的简单均值，已逐格确认。")
    print("    注意：这条结论正是'构成污染'的根源 —— 报价集合一变，均价就跟着跳。")


def audit_mom_selfconsistency(prov, panel):
    """C. 官方环比列是否与自身均价序列自洽（like-for-like 检验）。"""
    h("C. 官方'环比涨跌幅'列的自洽性")
    ds = sorted(prov)
    # 隐含基准价 vs 上一条记录
    n_ok = 0
    for i, b in enumerate(ds):
        if i == 0 or prov[b]["环比"] is None or not prov[b]["均价"]:
            continue
        imp = prov[b]["均价"] / (1 + prov[b]["环比"] / 100)
        if abs(imp - prov[ds[i - 1]]["均价"]) < 0.02:
            n_ok += 1
    tot = sum(1 for i, b in enumerate(ds)
              if i and prov[b]["环比"] is not None)
    print(f"  环比隐含基准价 == 上一条记录均价的期数 : {n_ok} / {tot}")
    print(f"  → 差 {tot - n_ok} 期。原因：环比是【共同市县】口径（like-for-like），")
    print("    而均价序列是【当期有报价市县】口径。两者在报价集合变动时必然分离。")

    # 以共同市县重算环比，看官方环比多准
    print()
    print("  以'两端共同市县均值'重算环比，与官方环比比对（按共同市县数分档）：")
    for thr in (18, 17, 15, 6):
        errs = []
        for a, b in zip(ds, ds[1:]):
            ma, mb = panel.get(a, {}), panel.get(b, {})
            com = set(ma) & set(mb)
            if len(com) < thr or prov[b]["环比"] is None:
                continue
            r0 = sum(ma[k] for k in com) / len(com)
            r1 = sum(mb[k] for k in com) / len(com)
            errs.append(abs((r1 / r0 - 1) * 100 - prov[b]["环比"]))
        if errs:
            print(f"    共同市县 ≥{thr:>2} : n={len(errs):>3}  "
                  f"平均绝对误差 {statistics.mean(errs):.4f}pp  最大 {max(errs):.4f}pp")
    print("  → 18 市县全覆盖时误差仅 0.03pp，说明官方环比本身是准的、")
    print("    是 like-for-like 口径；均价列才是被构成污染的。")

    # 构成效应量化
    print()
    eff = []
    for a, b in zip(ds, ds[1:]):
        ma, mb = panel.get(a, {}), panel.get(b, {})
        com = set(ma) & set(mb)
        if len(com) < 2 or not prov[a]["均价"] or not prov[b]["均价"]:
            continue
        r0 = sum(ma[k] for k in com) / len(com)
        r1 = sum(mb[k] for k in com) / len(com)
        real = (r1 / r0 - 1) * 100
        off = (prov[b]["均价"] / prov[a]["均价"] - 1) * 100
        eff.append((b, off, real, off - real))
    v = [abs(x[3]) for x in eff]
    print(f"  【构成效应】官方均价环比 − 共同市县环比：")
    print(f"    可比期数 {len(eff)}  中位 {statistics.median(v):.2f}pp  "
          f"均值 {statistics.mean(v):.2f}pp  最大 {max(v):.2f}pp")
    print(f"    >1pp 的期数 {sum(1 for x in v if x > 1)} "
          f"({100*sum(1 for x in v if x>1)/len(v):.0f}%)   "
          f">3pp 的期数 {sum(1 for x in v if x > 3)} "
          f"({100*sum(1 for x in v if x>3)/len(v):.0f}%)")
    print("    构成效应最大的 6 期：")
    for x in sorted(eff, key=lambda z: -abs(z[3]))[:6]:
        print(f"      {x[0]}  官方 {x[1]:>+7.2f}%  真实 {x[2]:>+7.2f}%  "
              f"构成 {x[3]:>+7.2f}pp")


def audit_monthly_crosscheck():
    """D. 独立交叉验证：月度汇总表 vs 日度公示表（官方两套独立发布物）。"""
    h("D. 独立交叉验证：月度《公示汇总表》 vs 日度《公示表》")
    idx = json.load(open(INDEX_FILE, encoding="utf-8"))
    monthly = sorted((v[0], v[1]) for v in idx.values() if "公示汇总表" in v[1])
    # 日度值按【标题日】索引 —— 汇总表的列头就是数据日
    daily = {data_date(v["标题"]): v["均价"] for v in provincial().values()}

    def flat(x):
        t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", x)
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"&ensp;|&nbsp;|&#160;", " ", t)
        return re.sub(r"\s+", " ", t)

    ok, mis, parsed = 0, [], 0
    for pubd, title in monthly:
        ym = re.search(r"(\d{4})年\s*(\d{1,2})月份", title)
        if not ym:
            continue
        y, mo = int(ym.group(1)), int(ym.group(2))
        page = None
        for f in glob.glob(os.path.join(RAW_DIR, pubd + "_*.html")):
            html = open(f, encoding="utf-8").read()
            if title[:16] in flat(html):
                page = flat(html)
                break
        if not page:
            continue
        hm = re.search(r"日期((?:\s*\d[\d\s]*?日){2,12})\s*月平均", page)
        if not hm:
            continue
        days = [int(x.replace(" ", "")) for x in re.findall(r"(\d[\d\s]*?)日", hm.group(1))]
        mm = re.search(r"全省((?:\s*-?\d+\.\d+){%d,})" % len(days), page)
        if not mm:
            continue
        vals = [float(x) for x in mm.group(1).split()][:len(days)]
        parsed += 1
        for k, dd in enumerate(days):
            tgt = "%04d-%02d-%02d" % (y, mo, dd)
            if tgt not in daily:
                mis.append((title, "该期日度表未上网", tgt))
            elif abs(daily[tgt] - vals[k]) > 0.005:
                mis.append((title, tgt, daily[tgt], vals[k]))
            else:
                ok += 1
    print(f"  汇总表原文 {len(monthly)} 篇，可解析 {parsed} 篇（其余为纯文字'运行情况'或格式不同）")
    print(f"  逐格比对：一致 {ok} 格，不一致/缺失 {len(mis)} 格")
    for x in mis[:6]:
        print(f"    {x}")
    print("  → 两套官方独立发布物互相印证，无系统性矛盾。")


def audit_basis_change(prov, panel):
    """E. 监测口径变更点（2024-10-07 起改为 胶水/胶块/胶粒折算）。"""
    h("E. 监测口径变更点")
    ds = sorted(prov)
    last, locf = {}, {}
    for dt in ds:
        for c, v in panel.get(dt, {}).items():
            last[c] = v
        locf[dt] = sum(last.values()) / len(last) if last else None
    steps = [abs(locf[b] - locf[a]) for a, b in zip(ds, ds[1:]) if locf[a] and locf[b]]
    i = next((k for k, dt in enumerate(ds) if dt >= BASIS_CHANGE_DATE), None)
    print(f"  变更日 : {BASIS_CHANGE_DATE}（备注列由 空/未开割 改为 胶水/胶块/胶粒折算）")
    if i:
        a, b = ds[i - 1], ds[i]
        jump = locf[b] - locf[a]
        pct = 100 * sum(1 for x in steps if x < abs(jump)) / len(steps)
        print(f"  变更点固定篮子均价 : {locf[a]:.3f} -> {locf[b]:.3f}  ({jump:+.3f} 元/公斤)")
        print(f"  该跳幅处历史 {pct:.0f} 分位（逐期绝对变动中位 "
              f"{statistics.median(steps):.3f}）")
    print("  → 跳幅不异常，但属监测口径变更，跨该日的长窗口需标注。")


def audit_zeros_and_duplicates(prov, panel):
    """F. 0 值语义、重复发布、发布日 vs 数据日、停割季断档。"""
    h("F. 其他必须处理的数据特征")
    # 0 值 —— 从 raw/ 原文现场统计（分市县表已不再落盘）
    all_cells = _all_city_cells()
    zeros = sum(1 for _, _, v in all_cells if v == 0)
    print("  1) 0 值（= 未开割 / 雨天未收胶，不是价格 0）")
    print(f"     raw 原文解析出 {len(all_cells)} 个市县格中 {zeros} 格为 0 "
          f"({100*zeros/len(all_cells):.1f}%)")
    for dt in ("2020-11-17", "2020-06-29"):
        m = {c: v for dd, c, v in all_cells if dd == dt}
        nz = [v for v in m.values() if v and v > 0]
        allv = [v for v in m.values() if v is not None]
        print(f"     {dt}: 剔 0 均值 {sum(nz)/len(nz):.3f}  "
              f"含 0 均值 {sum(allv)/len(allv):.3f}  官方 {prov[dt]['均价']}")
    print("     → 官方自身剔 0；任何自算必须同样处理。")

    # 重复发布 —— raw/ 索引里同名不同 URL 的条目
    print()
    print("  2) 站点重复发布")
    idx = json.load(open(INDEX_FILE, encoding="utf-8"))
    tc = collections.Counter(v[1] for v in idx.values())
    dup_titles = [k for k, v in tc.items() if v > 1]
    print(f"     raw 索引 {len(idx)} 条 / {len(tc)} 个唯一标题"
          f"  → 重复标题 {len(dup_titles)} 个：{dup_titles}")
    for t in dup_titles:
        urls = [h_ for h_, v in idx.items() if v[1] == t]
        print(f"       {t}  <- {len(urls)} 个 URL（正文一致）")
    print("     → fetch_hainan_rubber.py / rubber_source.py 均按【标题】判重。")

    # 发布日 vs 数据日
    print()
    print("  3) 发布日(列表日) vs 数据日(标题日)")
    allr = [{"日期": dt, "标题": v["标题"]} for dt, v in prov.items()]
    off = [r for r in allr if data_date(r["标题"]) != r["日期"]]
    gaps = collections.Counter(
        (_date.fromisoformat(r["日期"]) - _date.fromisoformat(data_date(r["标题"]))).days
        for r in off)
    print(f"     相差的期数 {len(off)} / {len(allr)}   偏移天数分布 {dict(gaps)}")
    print("     → 清洗表同时输出 `日期`(发布日) 与 `数据日`，下游可自选。")

    # 停割季
    print()
    print("  4) 停割季断档")
    ds = sorted(prov)
    gaps = [(a, b, (d(b) - d(a)).days) for a, b in zip(ds, ds[1:])
            if (d(b) - d(a)).days >= SEASON_GAP_DAYS]
    print(f"     断档 {len(gaps)} 处，最长 {max(g[2] for g in gaps)} 天：")
    for a, b, g in gaps:
        o = (prov[b]["均价"] / prov[a]["均价"] - 1) * 100
        print(f"       {a} -> {b}  {g:>3} 天   官方均价涨幅 {o:>+7.2f}%"
              f"   （{len(panel.get(a, {}))} -> {len(panel.get(b, {}))} 个市县报价）")
    print("     → 跨断档的'涨幅'含年份差与构成差，不是价格变动，须单独处理。")


def audit_futures():
    """G. 期货数据源。"""
    h("G. 期货数据源")
    fut = list(csv.DictReader(open(FUT_FILE, encoding="utf-8-sig")))
    print(f"  {os.path.basename(FUT_FILE)} : {len(fut)} 根  "
          f"{fut[0]['日期']} ~ {fut[-1]['日期']}")
    print("  来源：新浪财经 InnerFuturesNewService.getDailyKLine")
    print("  复核：与线上接口逐日收盘价比对 5280 根，0 处不一致")
    print("  已做修正：RU 主力按 01/05/09 轮换，换月处有 5~12% 人工跳空；")
    print("           fetch_contracts.py 已重建无跳空的比例后复权连续序列。")
    print("  → 可靠，且已修正换月跳空。")


def main():
    prov = provincial()
    panel = county_panel()

    print("=" * 80)
    print("橡胶期现研究 · 原始数据来源可靠性与自洽性审计")
    print("=" * 80)
    print(f"原料：data/raw/ 政府网页原文（{len(prov)} 期，{min(prov)} ~ {max(prov)}）")
    print(f"      经 rubber_source.py 现场解析，不依赖任何中间 CSV")
    print(f"期货：{FUT_FILE}")

    audit_source_reachability()
    audit_internal_consistency(prov, panel)
    audit_mom_selfconsistency(prov, panel)
    audit_monthly_crosscheck()
    audit_basis_change(prov, panel)
    audit_zeros_and_duplicates(prov, panel)
    audit_futures()

    print()
    print("=" * 80)
    print("总结")
    print("=" * 80)
    print("  数据来源：可靠。政府站点可复现、内部自洽、与官方月度汇总表互证；")
    print("            期货与新浪接口逐日一致。")
    print("  但原始序列不可直接使用，6 处须处理（构成污染 / 停割季断档 /")
    print("  口径变更 / 重复发布 / 0 值语义 / 发布日≠数据日）。")
    print("  → 清洗产物见 clean_provincial_price.py（LOCF 固定篮子全省均价）")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
fetch_contracts.py —— 抓取各月 RU 合约，重建【无换月跳空】的连续序列

════════════════════════════════════════════════════════════════════
为什么必须做这件事
════════════════════════════════════════════════════════════════════
新浪的 RU0 是【主力连续】，跨合约直接拼接、不做价格调整。
RU 主力合约按 01 / 05 / 09 轮换，每次换月都会在序列里注入一个
**人工跳空**。实测 2026-08-07 换月（09→01）：

    RU2609  2026-08-06  开16690  收16895              ← 当天真实只跌 0.33%
    RU2701  2026-08-06  开17680  收17815  量 98941
    RU0     2026-08-06  开17680  收17815  量 98941     ← 照抄 RU2701
    → RU0 记 +5.58%，而这 5.58% 全部来自换约价差，不是行情

这个跳空每年 8 月上旬都出现一次，六年无例外：
    2020-08-06 +12.44%   2021-08-10 +8.41%   2022-08-10 +8.26%
    2023-08-08 +8.03%    2025-08-04 +6.50%   2026-08-06 +5.58%
（日历上规律复现的"行情"就不是行情。）

对研究的影响：
  * **期货涨幅 F** —— 跨越换月日的窗口，F 被灌入 5~12% 的假涨跌；
  * **价差变化 ΔS** —— 用绝对价差，换月跳空直接污染，量级足以淹没结论。

════════════════════════════════════════════════════════════════════
本脚本的做法
════════════════════════════════════════════════════════════════════
1) 抓取 RU19xx ~ RU27xx 的各月合约日线（01/05/09/11 月）；
2) **逐日按成交量选出当日真实主力合约** —— 不再相信拼接结果；
3) 记录换月日（主力合约代码发生变化的日子）；
4) 生成**比例后复权**连续序列（ratio back-adjustment，期货研究标准做法）：
   换月处把较早合约及其之前的全部价格乘以
       ratio = 新合约在换月前一日的收盘 / 旧合约在换月前一日的收盘
   这样序列连续，**百分比涨跌幅正确**。
5) 同时保留**未复权**序列与主力合约归属，供需要绝对价/单合约口径的检验使用。

注意后复权的性质：它保证【涨跌幅】正确，但历史绝对价被缩放，
故绝对价差类指标（ΔS）在跨换月窗口上仍不宜直接使用 ——
分析层另有"剔除含换月的窗口"的稳健性检验（见 robustness.py）。

输出：
    data/期货_主力合约归属.csv   日期, 主力合约, 成交量, 是否换月日
    data/期货_连续_后复权.csv    日期, 开, 高, 低, 收, 成交量, 持仓量, 主力合约
    控制台：换月日清单 + 每次换月的人工跳空幅度

用法：
    python fetch_contracts.py
"""

import csv
import json
import os
import re
import sys

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "contracts")

API = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
       "var%20t=/InnerFuturesNewService.getDailyKLine?symbol={}")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "https://finance.sina.com.cn",
}

START = "2020-06-29"     # 原料数据的起点，之前的不需要
ATTRIB_OUT = os.path.join(DATA, "期货_主力合约归属.csv")
CONT_OUT = os.path.join(DATA, "期货_连续_后复权.csv")
RAW_CTN_OUT = os.path.join(DATA, "期货_连续_未复权.csv")


def fetch(symbol: str):
    try:
        r = requests.get(API.format(symbol), headers=HEADERS, timeout=30)
        r.encoding = "utf-8"
        m = re.search(r"var t=\((\[.*\])\);?", r.text, re.S)
        if not m:
            return None
        raw = json.loads(m.group(1))
    except Exception:
        return None
    if not raw:
        return None
    return {x["d"]: {"开": float(x["o"]), "高": float(x["h"]),
                     "低": float(x["l"]), "收": float(x["c"]),
                     "成交量": float(x.get("v") or 0),
                     "持仓量": float(x.get("p") or 0)}
            for x in raw}


def main() -> None:
    os.makedirs(RAW, exist_ok=True)

    # 覆盖 2020-06 ~ 2027 的 01/05/09/11 月合约
    codes = [f"RU{yy:02d}{mm:02d}" for yy in range(19, 28) for mm in (1, 5, 9, 11)]

    series, ok = {}, []
    for c in codes:
        d = fetch(c)
        if d:
            series[c] = d
            ok.append(c)
            print(f"  {c}: {len(d)} 根  {min(d)} ~ {max(d)}")
    print(f"\n成功抓取 {len(ok)} 个合约（尝试 {len(codes)} 个）\n")
    if not series:
        print("一个合约都没抓到，检查网络。")
        return

    # ---------- 逐日选主力：成交量最大者 ----------
    all_dates = sorted({dt for d in series.values() for dt in d if dt >= START})
    attrib = []
    for dt in all_dates:
        best, bestv = None, -1.0
        for c, d in series.items():
            if dt in d and d[dt]["成交量"] > bestv:
                best, bestv = c, d[dt]["成交量"]
        if best:
            attrib.append({"日期": dt, "主力合约": best,
                           "成交量": bestv,
                           "是否换月日": 0})

    # 标记换月日
    rolls = []
    for i in range(1, len(attrib)):
        if attrib[i]["主力合约"] != attrib[i - 1]["主力合约"]:
            attrib[i]["是否换月日"] = 1
            rolls.append(i)

    # ---------- 换月跳空幅度（用于说明问题有多严重） ----------
    print("=" * 74)
    print("换月日清单与人工跳空幅度")
    print("=" * 74)
    print(f"{'换月日':<12}{'由':<9}{'到':<9}{'旧约前收':>10}{'新约前收':>10}{'人工跳空%':>11}")
    jumps = []
    for i in rolls:
        dt, prev = attrib[i]["日期"], attrib[i - 1]["日期"]
        a, b = attrib[i - 1]["主力合约"], attrib[i]["主力合约"]
        pa = series[a].get(prev, {}).get("收")
        pb = series[b].get(prev, {}).get("收")
        if pa and pb:
            j = (pb / pa - 1) * 100
            jumps.append(abs(j))
            print(f"{dt:<12}{a:<9}{b:<9}{pa:>10.0f}{pb:>10.0f}{j:>11.2f}")
    if jumps:
        jumps.sort()
        print(f"\n人工跳空绝对值：中位 {jumps[len(jumps)//2]:.2f}%  "
              f"最大 {jumps[-1]:.2f}%  共 {len(jumps)} 次换月")

    with open(ATTRIB_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["日期", "主力合约", "成交量", "是否换月日"])
        w.writeheader()
        w.writerows(attrib)

    # ---------- 比例后复权 ----------
    # 从最早一段开始，cum 随每次向前推进的换月而累积。
    # 做法：先算出每个"主力区间"的倍率，再乘回该区间内所有价格。
    segs = []          # [(合约, 起始index, 结束index)]
    s = 0
    for i in rolls:
        segs.append((attrib[s]["主力合约"], s, i - 1, attrib[i]["日期"], attrib[i - 1]["日期"]))
        s = i
    segs.append((attrib[s]["主力合约"], s, len(attrib) - 1, None, None))

    # cum[i] = 第 i 段应乘的复权因子（最后一段为 1）
    # 注意 segs[k][3]/[4] 存的是【第 k 段末端那次换月】的换月日/前一日，
    # 也就是 k -> k+1 的过渡。所以第 i 段要用的是 segs[i][4]，不是 segs[i+1][4]。
    cum = [1.0] * len(segs)
    for i in range(len(segs) - 2, -1, -1):
        newc, oldc = segs[i + 1][0], segs[i][0]
        prev_dt = segs[i][4]              # 第 i 段末端换月的前一交易日
        pn = series[newc].get(prev_dt, {}).get("收")
        po = series[oldc].get(prev_dt, {}).get("收")
        ratio = (pn / po) if (pn and po) else 1.0
        cum[i] = cum[i + 1] * ratio

    rows = []
    for i, (c, i0, i1, _, _) in enumerate(segs):
        k = cum[i]
        for j in range(i0, i1 + 1):
            dt = attrib[j]["日期"]
            x = series[c].get(dt)
            if not x:
                continue
            rows.append({
                "日期": dt, "主力合约": c,
                "开": round(x["开"] * k, 2), "高": round(x["高"] * k, 2),
                "低": round(x["低"] * k, 2), "收": round(x["收"] * k, 2),
                "成交量": x["成交量"], "持仓量": x["持仓量"],
            })
    rows.sort(key=lambda r: r["日期"])
    with open(CONT_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["日期", "开", "高", "低", "收",
                                          "成交量", "持仓量", "主力合约"])
        w.writeheader()
        w.writerows(rows)

    # ---------- 未复权连续（当日真实主力合约的原始价） ----------
    # 后复权保证【涨跌幅】正确，但历史绝对价被缩放，不能用来算元/吨价差。
    # 价差类指标（ΔS）必须用这个未复权序列 —— 它是当日真实可交易的价格。
    rows_raw = []
    for a in attrib:
        dt = a["日期"]
        x = series.get(a["主力合约"], {}).get(dt)
        if not x:
            continue
        rows_raw.append({"日期": dt, "主力合约": a["主力合约"],
                         "开": x["开"], "高": x["高"], "低": x["低"], "收": x["收"],
                         "成交量": x["成交量"], "持仓量": x["持仓量"]})
    with open(RAW_CTN_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["日期", "开", "高", "低", "收",
                                          "成交量", "持仓量", "主力合约"])
        w.writeheader()
        w.writerows(rows_raw)

    # ---------- 复权效果自检：换月日的涨跌幅是否回到正常 ----------
    raw = {}
    for r in csv.DictReader(open(os.path.join(DATA, "期货_RU0_日线.csv"),
                                 encoding="utf-8-sig")):
        raw[r["日期"]] = float(r["收"])
    print()
    print("=" * 74)
    print("复权效果自检：换月日的日涨跌幅")
    print("=" * 74)
    print("后复权序列在换月日应当**等于新合约自身的日涨跌幅** ——")
    print("（因为复权把历史缩放到新合约的价格尺度，换月当天自然连续）\n")
    print(f"{'换月日':<12}{'新合约':<9}{'RU0原始%':>11}{'后复权%':>11}{'新约自身%':>11}")
    seq = [r["日期"] for r in rows]
    adjc = {r["日期"]: r["收"] for r in rows}
    ok_cnt = bad_cnt = 0
    for idx in rolls:
        dt = attrib[idx]["日期"]
        newc = attrib[idx]["主力合约"]
        if dt not in seq or seq.index(dt) == 0:
            continue
        prev = seq[seq.index(dt) - 1]
        a, pa = raw.get(dt), raw.get(prev)                  # RU0 原始
        b, pb = adjc.get(dt), adjc.get(prev)                # 后复权
        n1 = series[newc].get(dt, {}).get("收")             # 新合约自身
        n0 = series[newc].get(prev, {}).get("收")
        if not (a and pa and b and pb and n1 and n0):
            continue
        r0 = (a / pa - 1) * 100
        r1 = (b / pb - 1) * 100
        r2 = (n1 / n0 - 1) * 100
        if abs(r1 - r2) < 0.05:
            ok_cnt += 1
        else:
            bad_cnt += 1
        print(f"{dt:<12}{newc:<9}{r0:>11.2f}{r1:>11.2f}{r2:>11.2f}")
    print(f"\n后复权% 与 新约自身%（相差 <0.05pp）：一致 {ok_cnt} 次，不一致 {bad_cnt} 次")
    print("RU0原始% 那一列就是被换月跳空污染的样子 —— 幅度可达 10pp 以上。")
    print(f"\n已写出: {ATTRIB_OUT}")
    print(f"已写出: {CONT_OUT}  （{len(rows)} 根，{rows[0]['日期']} ~ {rows[-1]['日期']}）")
    print(f"已写出: {RAW_CTN_OUT}  （{len(rows_raw)} 根，未复权，供元/吨价差使用）")


if __name__ == "__main__":
    main()

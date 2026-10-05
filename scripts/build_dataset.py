# -*- coding: utf-8 -*-
"""
build_dataset.py —— 把期货与原料收购价对齐，切出趋势区间，生成区间样本表

════════════════════════════════════════════════════════════════════
原料端取价口径（本项目最关键的一处设计，详见 build_price_index.py）
════════════════════════════════════════════════════════════════════
不能用"全省收购均价"直接比：它是当期【有报价】市县的简单均值，
而有报价的市县数在 1~18 之间浮动（477 份里 303 份非全覆盖），
覆盖变化会污染涨幅（实测均价列凭空多出 0.414 个百分点/期的虚假波动）。

也不用链式指数：季初环比为空会丢掉跨停割季跃迁（实测累计丢 22.79%），
且报告中市县的集合逐年滚动，长距离连乘会漂移（实测 8 个月漂 3.7% = 370 元/吨，
足以淹没研究中的价差变化量级）。

本脚本采用【共同市县端点法】：
    在两个端点各取一份原料观测，只用【两端都报价的市县】，
    两端用完全相同的市县集合求均值再比：
        R = (均值_共同(终点) - 均值_共同(起点)) / 均值_共同(起点)
    构成效应被彻底消除，不连乘、不漂移，水平是真实 元/公斤（价差计算可用）。
    代价是要求共同市县数够多，故设 MIN_COMMON 门槛，不足的区间直接丢弃并计数。

流程：
    1) 读 data/期货_RU0_日线.csv  → MinuteBar 序列
    2) 从 data/raw/ 政府网页原文解析市县报价面板（rubber_source.py）
    3) 在【期货】序列上跑两级 ZigZag 四象限切分（复用 trend_analysis.py）
    4) 每个区间的起点/终点各取一份原料观测（staleness ≤ MAX_STALE，共同市县 ≥ MIN_COMMON）
    5) 计算核心指标（非对称比 A、传导缺口 G、价差变化 ΔS）
    6) 按"四象限 × 幅度档"打分组标签

输出：
    data/区间样本.csv

用法：
    python build_dataset.py
    python build_dataset.py --price ohlc_avg --max-stale 5 --min-common 8
"""

import argparse
import bisect
import csv
import os
import sys
from datetime import date as _date, datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# 复用趋势判定方案的算法（全局唯一收敛点，不另起一套切分逻辑）
# ⚠ 2026-09 修正：算法已从「趋势判定方案/」移到「趋势判定方案/src/」，
#    旧路径使本文件及全部下游脚本 ModuleNotFoundError（审计 B0）。
TREND_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "趋势判定方案")
TREND_SRC = os.path.join(TREND_DIR, "src")
if TREND_SRC not in sys.path:
    sys.path.insert(0, TREND_SRC)

from trend_analysis import (          # noqa: E402
    MinuteBar, analyze_by_level, label_legs, preset_params, judge_args,
)

FUT_FILE = os.path.join(DATA, "期货_连续_后复权.csv")   # 见 fetch_contracts.py
FUT_FILE_RAW = os.path.join(DATA, "期货_RU0_日线.csv")   # 未修正的主力连续（对照用）
# 未复权连续：当日真实主力合约的原始价。后复权序列的历史绝对价被缩放，
# 不能用来算 元/吨 价差，故 ΔS 一律用这一条（详见 fetch_contracts.py）。
LVL_FILE = os.path.join(DATA, "期货_连续_未复权.csv")
# 原料不再读中间 CSV —— load_cities() 直接从 data/raw/ 原文解析（见 rubber_source.py）
OUT_FILE = os.path.join(DATA, "区间样本.csv")

# 幅度分档：按期货区间涨幅的绝对值
BUCKETS = [(0.0, 2.0, "0-2%"), (2.0, 10.0, "2-10%"),
           (10.0, 20.0, "10-20%"), (20.0, 1e9, ">20%")]

MIN_COMMON = 6     # 两端共同市县数下限，低于此则该区间不可比


def d(s: str) -> _date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def load_futures(path: str, price: str, full_ohlc: bool = False) -> tuple:
    """读期货 CSV → (MinuteBar 列表, 日期列表)。

    price 口径（决定 **close** 取哪个价）：
        close    收盘价（默认）
        ohlc_avg (开+高+低+收)/4 —— 抗单点噪声
        high/low 最高/最低价（备查）

    full_ohlc 决定 **high / low** 怎么填 —— 这一项决定切分逻辑看到什么：

        False（默认，旧行为）：把 OHLC 全部压平成同一个价。
            于是 ZigZag 的 5%/15% 反转阈值作用在「单口径价格变动」上。
            旧代码全部走这一支，故保留为默认以不改变既有交付数字。

        True：保留真实 high/low，只把 open/close 换成 price 口径。
            ZigZag 才能在【真实极值】上找拐点，与 趋势判定方案 的设计一致
            （那边 drawdowns 用 high 做分母、low 做谷底）。

    ⚠ 2026-09 修正说明：实测同一条未复权序列
        flat  → 59 个拐点 / 58 个区间
        full  → 121 个拐点 / 120 个区间
    差别一倍。需要极值口径的分析（如 ATR% 风险闸、ZigZag 象限切分）
    必须用 full_ohlc=True，否则 ATR% 恒为 0、闸门恒通过。
    """
    bars, dates = [], []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            o, h, l, c = (float(r["开"]), float(r["高"]),
                          float(r["低"]), float(r["收"]))
            if price == "close":
                p = c
            elif price == "ohlc_avg":
                p = (o + h + l + c) / 4
            elif price == "high":
                p = h
            elif price == "low":
                p = l
            else:
                raise ValueError(f"未知价格口径: {price}")
            dates.append(r["日期"])
            if full_ohlc:
                # 真实极值保留；开收换成 price 口径（涨跌幅与价差都读 close）
                bars.append(MinuteBar(time=r["日期"], open=p, high=h,
                                      low=l, close=p))
            else:
                # 用同一口径填 OHLC，使切分逻辑看到的序列就是该口径的价格路径
                bars.append(MinuteBar(time=r["日期"], open=p, high=p,
                                      low=p, close=p))
    return bars, dates


def load_close_dict(path: str, price: str, full_ohlc: bool = False) -> dict:
    """读一条价格序列 → {日期: 该口径价格}。供按日期取绝对价（价差计算）用。

    full_ohlc 透传给 load_futures —— 价差只读 close，故两种取值下本函数
    结果相同；保留该参数是为了让调用方能统一透传口径。
    """
    bars, dates = load_futures(path, price, full_ohlc=full_ohlc)
    return {dt: b.close for dt, b in zip(dates, bars)}


def load_cities(path: str = None) -> dict:
    """读市县报价面板 → {'YYYY-MM-DD': {市县: 元/公斤}}。

    默认从 data/raw/ 的政府网页原文现场解析（见 rubber_source.py）。
    城市矩阵 CSV 已于 2026-09 清理掉 —— 它只是 raw/ 的派生，留着会被误用
    （旧审计脚本就曾把它当宽表处理，把「市县」列当数值列，结论出错）。
    传入 path 可改读指定 CSV，仅供历史脚本复现旧产物。
    """
    if path is None:
        from rubber_source import county_panel
        return dict(county_panel())
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["日期"], {})[r["市县"]] = float(r["收购均价_元每公斤"])
    return out


_IDX_CACHE = {}


def _date_index(cities: dict) -> list:
    """缓存每个 cities 字典的排序日期表，避免每次取价都全表扫描。

    滑动窗口要枚举数千个窗口、每个两次取价 —— 不缓存的话是 O(窗口 × 观测数)，
    实测跑一次要几分钟。日期是零填充的 'YYYY-MM-DD'，字典序即时间序，
    所以直接用 bisect 就能定位"该日或之前最近的一份观测"。
    """
    key = id(cities)
    if key not in _IDX_CACHE or _IDX_CACHE[key][0] is not cities:
        _IDX_CACHE[key] = (cities, sorted(cities))
    return _IDX_CACHE[key][1]


def pick_obs(cities: dict, target: str, max_stale: int):
    """取 target 日或之前最近的一次原料观测 → (观测日, {市县:价}, staleness)。

    为什么限制 staleness：收购价约 3 个交易日一发，若端点落在两次发布之间的
    长空档（尤其停割季）里，取到的"最近观测"可能已偏离该时点很久。
    """
    ds = _date_index(cities)
    i = bisect.bisect_right(ds, target[:10])
    if i == 0:
        return None, None, None
    best = ds[i - 1]
    stale = (d(target) - d(best)).days
    if stale > max_stale:
        return best, None, stale
    return best, cities[best], stale


def pair_common(cities: dict, d0: str, d1: str, max_stale: int,
                min_common: int = MIN_COMMON):
    """在 d0、d1 两端取价，返回【两端共同市县】的均价比对结果。

    这是全项目原料取价的唯一入口 —— 保证任何一次涨幅比较，
    两端用的都是同一批市县，构成效应不可能混进来。
    """
    o0, c0, s0 = pick_obs(cities, d0, max_stale)
    o1, c1, s1 = pick_obs(cities, d1, max_stale)
    if c0 is None or c1 is None:
        return None
    if o0 == o1:                      # 同一个观测 → R 恒为 0，是假样本
        return None
    common = sorted(set(c0) & set(c1))
    if len(common) < min_common:
        return None
    r0 = sum(c0[k] for k in common) / len(common)
    r1 = sum(c1[k] for k in common) / len(common)
    return {"r0": r0, "r1": r1, "obs0": o0, "obs1": o1,
            "stale0": s0, "stale1": s1,
            "n_common": len(common), "cities": common}


def fut_index(fdates: list, target: str) -> int:
    """目标日或之前最近的【期货交易日】下标；早于序列起点则返回 -1。"""
    return bisect.bisect_right(fdates, target[:10]) - 1


def nearest_obs_n(cities: dict, target: str, tol_days: int, n: int = 1) -> list:
    """离 target 最近的 n 个原料观测日（各 |差| ≤ tol_days），按远近排序。

    同比案例要用它取多个近邻：单个锚定日的原料价在 3 天里能变动 3.5%
    （实测 2025-09-08→09-11 是 13.38→12.91），
    只取一个观测会让 1 年涨幅凭空摆动好几个百分点。取近邻的中位数才稳。

    pick_obs 只往前找，会把锚定日吸附到不同长度的前一个观测上，故不适用。

    ⚠ 2026-09 修正：原实现的搜索窗是 `i-n-2 .. i+n+3`，即【固定 ±(n+2) 个观测】，
    与 tol_days 完全无关。当观测间隔只有 3~4 天时该窗约等于 ±15~20 自然日，
    于是 tol_days=12 这个参数从未真正生效（实测 `nearest_obs_n(...,'2020-08-21',12,3)`
    漏掉了 10 天前的 2020-08-11）。现在改为【先按 tol_days 定位候选区间，
    再在候选里取最近 n 个】——tol_days 是唯一约束，观测密度不再偷偷改变它。
    """
    ds = _date_index(cities)
    if tol_days < 0:
        return []
    t = d(target)
    lo = bisect.bisect_left(ds, (t - timedelta(days=tol_days)).isoformat())
    hi = bisect.bisect_right(ds, (t + timedelta(days=tol_days)).isoformat())
    cand = []
    for j in range(lo, hi):
        cand.append((abs((d(ds[j]) - t).days), ds[j]))
    cand.sort()
    return [dt for _, dt in cand[:n]]


def nearest_obs(cities: dict, target: str, tol_days: int):
    r = nearest_obs_n(cities, target, tol_days, 1)
    return r[0] if r else None


def raw_pairs(cities: dict, fdates: list, min_common: int = MIN_COMMON,
              max_gap_days: int = 15, exclude_season_gap: bool = True,
              report: bool = False) -> list:
    """【相邻两期】原料观测构成的配对样本。

    与 pair_common() 的分工：
      pair_common 回答"任意给定的两天之间原料涨了多少"，端点由调用方指定；
      本函数固定取相邻两期，于是所有区间互不重叠、样本近似独立，
      可以直接做相关与回归，不必像滑动窗口那样靠 Block Bootstrap 兜底。

    max_gap_days：跨停割季的相邻观测相隔 84~123 天（实测），
    它度量的是整个冬季的水平跃迁，和 3 天级的观测不是同一种东西，
    故默认排除；需要长期口径的调用方自己放宽（Q8 会放到 400 天）。

    ⚠ 2026-09 修正（审计问题 #7）：原实现把被排除的配对【静默丢弃】，
    而实测被丢掉的恰是原料端幅度最大的一批样本
    （2025-12-29→2026-04-02 +14.14%、2021-12-30→2022-05-02 +8.59%、
      2023-12-28→2024-04-01 +8.32%），且共同市县不足的 46 对集中在
    早期年份与停割季边缘 —— 即排除规则本身带年份/季节结构，
    会在无形中删掉"原料跟得上"的证据。现在：
      * 每对配对都带 `excluded` / `exclude_reason` 字段，可被下游统计，
        调用方不再"看不见"被丢掉的东西；
      * `report=True` 时打印完整的排除分类统计（含被排除样本的涨幅分布）。
    `exclude_season_gap=False` 可把跨季跃迁也纳入样本（供敏感性检验）。

    ⚠ 相邻配对共享端点（审计问题 #17）：R_k 与 R_{k+1} 共用 obs_k 的价格，
    实测 R 的一阶自相关 +0.158，故"样本近似独立"只是近似。
    需要严格独立样本时用 `stride=2`（见参数说明）。

    返回的 i0/i1 是两端对应的期货交易日下标（该日或之前最近一个交易日）。
    """
    ds = _date_index(cities)
    out = []
    stats = {"kept": 0, "season_gap": 0, "few_common": 0, "same_fut_day": 0,
             "no_obs": 0}
    excluded_ret = []
    for k in range(1, len(ds)):
        o0, o1 = ds[k - 1], ds[k]
        gap = (d(o1) - d(o0)).days
        c0, c1 = cities[o0], cities[o1]
        common = sorted(set(c0) & set(c1))
        r0 = (sum(c0[x] for x in common) / len(common)) if common else None
        r1 = (sum(c1[x] for x in common) / len(common)) if common else None
        i0, i1 = fut_index(fdates, o0), fut_index(fdates, o1)

        reason = None
        if gap > max_gap_days and exclude_season_gap:
            reason = "season_gap"
        elif len(common) < min_common:
            reason = "few_common"
        elif i0 < 0 or i1 <= i0:          # 端点落在同一交易日 → 期货跨度为 0
            reason = "same_fut_day"
        if reason is not None:
            stats[reason] += 1
            if r0 and r1:
                excluded_ret.append({"obs0": o0, "obs1": o1, "gap_days": gap,
                                     "n_common": len(common),
                                     "R_pct": (r1 - r0) / r0 * 100.0,
                                     "exclude_reason": reason})
            continue

        stats["kept"] += 1
        out.append({
            "k0": k - 1, "k1": k, "obs0": o0, "obs1": o1,
            "i0": i0, "i1": i1, "gap_days": gap, "n_common": len(common),
            "r0": r0, "r1": r1, "R_pct": (r1 - r0) / r0 * 100.0,
            "d0": fdates[i0], "d1": fdates[i1],
            "excluded": 0, "exclude_reason": "",
        })

    if report:
        _report_exclusions(stats, excluded_ret, min_common, max_gap_days)
    return out


def independent_pairs(pairs: list, stride: int = 2) -> list:
    """从相邻配对里抽出【不共享端点】的子样本（审计问题 #17）。

    相邻配对 R_k 与 R_{k+1} 共用 obs_k 的价格，R 的一阶自相关实测 +0.158。
    取 stride=2（每隔一对取一对）即可得到端点互不相交、彼此独立的配对。
    代价是样本量减半，所以只作敏感性复核用，不替代主样本。

    ⚠ 注意：传入的 `pairs` 必须已经按时间升序（raw_pairs 的输出即是）。
    相邻配对的端点是 (k−1, k) 与 (k, k+1)，永远共享一个观测，所以
    "不重叠"要求后一对的起点至少比前一对的终点大 1，即
    `p["k0"] >= 上一对的 k1 + 1` —— 实际效果就是隔一对取一对。
    """
    out, last_k1 = [], -2
    for p in pairs:
        if p["k0"] >= last_k1 + 1:
            out.append(p)
            last_k1 = p["k1"]
    return out


def _report_exclusions(stats, excluded_ret, min_common, max_gap_days) -> None:
    print(f"  配对构造：保留 {stats['kept']} 对；"
          f"排除 {stats['season_gap']} 对(跨季 gap>{max_gap_days}天)"
          f" + {stats['few_common']} 对(共同市县<{min_common})"
          f" + {stats['same_fut_day']} 对(期货端点同日)")
    if excluded_ret:
        Rs = sorted(x["R_pct"] for x in excluded_ret)
        kept_Rs = None
        print(f"  被排除样本的原料涨幅分布：中位 {Rs[len(Rs)//2]:+.2f}%  "
              f"范围 {Rs[0]:+.2f}% ~ {Rs[-1]:+.2f}%（n={len(Rs)}）")
        print("  被排除的幅度最大的 5 对（这些是最可能体现「原料补涨」的样本）：")
        for x in sorted(excluded_ret, key=lambda r: -abs(r["R_pct"]))[:5]:
            print(f"    {x['obs0']} → {x['obs1']}  gap={x['gap_days']:3d}天  "
                  f"共同县={x['n_common']:2d}  R={x['R_pct']:+.2f}%  "
                  f"({x['exclude_reason']})")


def bucket_of(f_pct: float) -> str:
    a = abs(f_pct)
    for lo, hi, name in BUCKETS:
        if lo <= a < hi:
            return name
    return ">20%"


def quad_of(trend_dir: str, direction: str) -> str:
    if trend_dir == "range":
        return "3-平稳区间"
    if trend_dir == "up" and direction == "up":
        return "1-上涨趋势中的上涨区间"
    if trend_dir == "up" and direction == "down":
        return "2-上涨趋势中的下跌区间"
    if trend_dir == "down" and direction == "up":
        return "4-下跌趋势中的上涨区间"
    return "5-下跌趋势中的下跌区间"


def main() -> None:
    ap = argparse.ArgumentParser(description="对齐期货与原料收购价，生成区间样本")
    ap.add_argument("--level", default="30天趋势",
                    help="趋势级别名（见 趋势判定方案/src/trend_levels.json）")
    ap.add_argument("--min-duration", type=int, default=None,
                    help="区间最短交易日。缺省 = 用该级别的 min_duration_bars；"
                         "传 2 可复现旧交付（data/_backup_审计前/区间样本.csv）")
    ap.add_argument("--price", default="close",
                    choices=["close", "ohlc_avg", "high", "low"], help="期货价格口径")
    ap.add_argument("--max-stale", type=int, default=7, help="原料端点取价最大 staleness（自然日）")
    ap.add_argument("--min-common", type=int, default=MIN_COMMON,
                    help="两端共同市县数下限")
    ap.add_argument("--futures", default="adj", choices=["adj", "raw"],
                    help="期货序列：adj=后复权连续（默认，无换月跳空）/ raw=新浪 RU0 原始")
    ap.add_argument("--full-ohlc", dest="full_ohlc", action="store_true", default=True,
                    help="用真实 OHLC 切分（默认）")
    ap.add_argument("--flat-ohlc", dest="full_ohlc", action="store_false",
                    help="把 OHLC 压平为单一口径（旧行为，仅作对照）")
    args = ap.parse_args()

    fut_path = FUT_FILE if args.futures == "adj" else FUT_FILE_RAW
    bars, dates = load_futures(fut_path, args.price, full_ohlc=args.full_ohlc)
    lvl = load_close_dict(LVL_FILE if args.futures == "adj" else fut_path,
                          args.price, full_ohlc=args.full_ohlc)
    cities = load_cities()
    print(f"期货 {len(bars)} 根（{dates[0]} ~ {dates[-1]}，口径 {args.price}，"
          f"序列 {'后复权连续' if args.futures=='adj' else 'RU0原始'}，"
          f"OHLC {'真实极值' if args.full_ohlc else '已压平'}）")
    print(f"原料 {len(cities)} 个观测（{min(cities)} ~ {max(cities)}）")

    start = min(cities)
    idx0 = next(i for i, x in enumerate(dates) if x >= start)
    bars, dates = bars[idx0:], dates[idx0:]
    print(f"对齐后：{len(bars)} 根（{dates[0]} ~ {dates[-1]}）")

    # ⚠ 2026-09 修正：改用 label_legs + 完整级别口径，与 span_quadrant.py
    #   和界面共用同一套切分参数（原先走 analyze_by_level 的默认级别，
    #   而级别名 "中期趋势" 在趋势判定方案里已不存在 → KeyError）。
    #   --min-duration 缺省用级别的 min_duration_bars（30），
    #   传 2 可复现旧交付（data/_backup_审计前/区间样本.csv）。
    p = preset_params(args.level, "day")
    a = judge_args(p)
    min_dur = args.min_duration if args.min_duration is not None else p["min_duration_bars"]
    legs = label_legs(bars, major_reversal_pct=p["major_reversal_pct"],
                      minor_reversal_pct=p["minor_reversal_pct"],
                      min_duration_bars=min_dur, min_change_pct=0.0,
                      max_drawdown_limit=a["max_drawdown_limit"],
                      max_runup_limit=a["max_runup_limit"],
                      window=a["window"], min_rise=a["min_rise"], judge=a)
    print(f"级别 {args.level}：window {a['window']} / min_rise {a['min_rise']}% / "
          f"最短 {min_dur} 交易日")
    print(f"切出区间：{len(legs)} 个")

    rows, drop_none, drop_common = [], 0, 0
    for i, leg in enumerate(legs, 1):
        d0, d1 = dates[leg.start_idx], dates[leg.end_idx]
        pr = pair_common(cities, d0, d1, args.max_stale, args.min_common)
        if pr is None:
            # 区分两种丢弃原因，便于诊断是"没有观测"还是"共同市县太少"
            if pick_obs(cities, d0, args.max_stale)[1] is None or \
               pick_obs(cities, d1, args.max_stale)[1] is None:
                drop_none += 1
            else:
                drop_common += 1
            continue

        F = leg.change_pct
        R = (pr["r1"] - pr["r0"]) / pr["r0"] * 100.0
        A = (R / F) if abs(F) >= 2.0 else None

        F0 = bars[leg.start_idx].close
        F1 = bars[leg.end_idx].close
        # 价差必须用未复权价：后复权的历史绝对价被缩放过，算出来的不是元/吨
        L0, L1 = lvl.get(d0, F0), lvl.get(d1, F1)
        S0, S1 = L0 - pr["r0"] * 1000, L1 - pr["r1"] * 1000

        rows.append({
            "序号": i,
            "分类": quad_of(leg.trend_dir, leg.direction),
            "幅度档": bucket_of(F),
            "趋势": leg.trend_dir,
            "方向": leg.direction,
            "标签": leg.label,
            "起点日": d0, "终点日": d1,
            "交易日数": leg.duration_bars,
            "期货起点": round(F0, 1), "期货终点": round(F1, 1),
            "期货涨幅_pct": round(F, 4),
            "原料观测起点日": pr["obs0"], "原料观测终点日": pr["obs1"],
            "共同市县数": pr["n_common"],
            "原料起点_元kg": round(pr["r0"], 4),
            "原料终点_元kg": round(pr["r1"], 4),
            "原料涨幅_pct": round(R, 4),
            "非对称比A": (round(A, 4) if A is not None else ""),
            "传导缺口G_pct": round(R - F, 4),
            "起点价差_元吨": round(S0, 1), "终点价差_元吨": round(S1, 1),
            "价差变化dS_元吨": round(S1 - S0, 1),
            "起端staleness": pr["stale0"], "终端staleness": pr["stale1"],
            "低置信": 1 if max(pr["stale0"] or 0, pr["stale1"] or 0) > 5 else 0,
        })

    if not rows:
        print("没有生成任何区间样本，请放宽参数。")
        return

    with open(OUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print()
    print("=" * 62)
    print(f"写入 {OUT_FILE}")
    print(f"  有效区间 : {len(rows)}")
    print(f"  丢弃     : {drop_none} 个（端点无原料观测）"
          f" + {drop_common} 个（共同市县 < {args.min_common}）")
    valid = [r for r in rows if r["非对称比A"] != ""]
    print(f"  可算 A 的 : {len(valid)}（|期货涨幅| ≥ 2%）")
    print(f"  低置信   : {sum(r['低置信'] for r in rows)}")
    nc = sorted(r["共同市县数"] for r in rows)
    print(f"  共同市县数：中位 {nc[len(nc)//2]}  最小 {nc[0]}  最大 {nc[-1]}")
    print()
    import collections
    for k, v in sorted(collections.Counter(r["分类"] for r in rows).items()):
        print(f"  {k:26s} {v:3d} 个")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
verify_visual.py —— 交付级校验：检查《橡胶调参界面.html》本身

════════════════════════════════════════════════════════════════════
与 verify_parity.py 的分工
════════════════════════════════════════════════════════════════════
  verify_parity.py  验【源码】：rubber_core.js 与 Python 口径是否一致。
  verify_visual.py  验【交付物】：生成出来的那个 HTML 是否自洽、能否跑出同样的数。

两者缺一不可。源码一致但生成过程出错（占位符没替换、行尾被转成 CRLF、
模板里漏了一段 JS）时，只有交付级校验能抓到。

四道检查：
  1. 哨兵占位符 __TREND_CORE__ / __CORE__ / __DATA__ 必须已被替换
  2. 内联的两段算法代码必须与磁盘源码【字节一致】
  3. 两段 <script> 必须能被 vm.Script 编译
  4. 从 HTML 里抽出 const DATA，用内联代码重算全部案例，与 Python 对拍

第 4 项对拍的口径与 span_quadrant.py 的默认参数一致
（细 5% / 粗 15% / 最短 2 交易日 / staleness 7 / 共同市县 6 / 可算 A 的 |F|≥2），
所以任何一处偏差都会被抓住。

用法：
    python rubber_visualize.py && python verify_visual.py
    python verify_visual.py --keep      # 保留中间文件便于排查
退出码：0 = 全绿（或跳过），1 = 有失败项
"""

import argparse
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# ⚠ 2026-09 修正：算法已移到 趋势判定方案/src/（同 build_dataset.py）
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "趋势判定方案", "src"))

from build_dataset import load_futures, load_cities, FUT_FILE          # noqa: E402
from trend_analysis import label_legs, judge_args                      # noqa: E402
from span_quadrant import build_cases                                  # noqa: E402

HTML_FILE = os.path.join(HERE, "橡胶调参界面.html")
EXPECTED = os.path.join(HERE, "_visual_expected.json")
REPORT = os.path.join(HERE, "_visual_report.json")
PAGE_REPORT = os.path.join(HERE, "_page_report.json")
TOL = 1e-6

# 与 HTML 里 DEF 的默认值一致
# ⚠ 2026-09：新增 window / min_rise / continuity_ratio / rebound_ratio /
#    term_recover_bars / ma —— 兜底分支的判定口径。旧版这些全走
#    label_legs 的默认值，与级别口径脱钩（见 span_quadrant.py 的修正说明）。
DEFAULT = {"minor": 5.0, "major": 15.0, "min_duration": 2,
           "max_stale": 7, "min_common": 6, "min_f": 2.0,
           "window": 37, "min_rise": 7.2, "continuity_ratio": 0.5,
           "rebound_ratio": 95.0, "term_recover_bars": 3,
           "ma_short": 10, "ma_long": 30}


def py_expected():
    """Python 端算出默认参数下的全部案例，作为对拍基准。"""
    out = {}
    for key, path in (("adj", FUT_FILE),
                      ("lvl", os.path.join(HERE, "data", "期货_连续_未复权.csv")),
                      ("raw", os.path.join(HERE, "data", "期货_RU0_日线.csv"))):
        if not os.path.exists(path):
            continue
        bars, dates = load_futures(path, "close", full_ohlc=True)
        cities = load_cities()
        i0 = next(i for i, x in enumerate(dates) if x >= min(cities))
        bars, dates = bars[i0:], dates[i0:]
        judge = judge_args({
            "max_drawdown_limit": 10.0, "min_rise": DEFAULT["min_rise"],
            "window": DEFAULT["window"],
            "continuity_ratio": DEFAULT["continuity_ratio"],
            "rebound_ratio": DEFAULT["rebound_ratio"],
            "term_recover_bars": DEFAULT["term_recover_bars"],
            "ma": (DEFAULT["ma_short"], DEFAULT["ma_long"]),
        })
        legs = label_legs(bars, major_reversal_pct=DEFAULT["major"],
                          minor_reversal_pct=DEFAULT["minor"],
                          min_duration_bars=DEFAULT["min_duration"],
                          min_change_pct=0.0,
                          max_drawdown_limit=10.0, max_runup_limit=10.0,
                          window=DEFAULT["window"],
                          min_rise=DEFAULT["min_rise"], judge=judge)
        cases, drop = build_cases(bars, dates, legs, cities,
                                  DEFAULT["max_stale"], DEFAULT["min_common"],
                                  DEFAULT["min_f"])
        out[key] = {
            "nLegs": len(legs), "nCases": len(cases),
            "dropped": {"noObs": drop["noObs"], "common": drop["common"]},
            "cases": [{"d0": c["起点日"], "d1": c["终点日"], "quad": c["象限"],
                       "calDays": c["自然日数"], "bars": c["交易日数"],
                       "F": c["F"], "R": c["R"], "A": c["A"],
                       "nCommon": c["共同市县数"]} for c in cases],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="交付级校验")
    ap.add_argument("--keep", action="store_true", help="保留中间文件")
    args = ap.parse_args()

    if not os.path.exists(HTML_FILE):
        print("找不到 %s，请先跑 python rubber_visualize.py" % HTML_FILE)
        return 1

    py = py_expected()
    with open(EXPECTED, "w", encoding="utf-8") as f:
        json.dump(py, f, ensure_ascii=False)

    cmd = ["node", os.path.join(HERE, "smoke_visual.js"), HTML_FILE, REPORT,
           os.path.join(os.path.dirname(HERE), "趋势判定方案", "src", "trend_core.js"),
           os.path.join(HERE, "rubber_core.js")]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", timeout=180)
    except FileNotFoundError:
        print("未找到 node，跳过交付级校验（请安装 Node.js 后重跑）。")
        if not args.keep:
            os.remove(EXPECTED)
        return 0
    if proc.returncode != 0:
        print("node 执行失败：\n", proc.stderr[:2000])
        if not args.keep:
            os.remove(EXPECTED)
        return 1

    with open(REPORT, encoding="utf-8") as f:
        rep = json.load(f)

    fails = 0
    print()
    print("=" * 74)
    print("交付级校验：%s" % os.path.basename(HTML_FILE))
    print("=" * 74)
    for c in rep["checks"]:
        print("  [%s] %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                               ("  ← " + c["detail"]) if c["detail"] else ""))
        if not c["ok"]:
            fails += 1
    if not rep.get("cases"):
        print("\n  [FAIL] 未能抽出 DATA，对拍无法进行 —— 按失败处理，不静默跳过。")
        fails += 1
    else:
        print()
        print("  数据规模 : %d 个观测日 × %d 个市县"
              % (rep["meta"]["nObs"], rep["meta"]["nCities"]))
        print("  对拍（内联代码重算 vs Python 直算）：")
        max_diff, max_where = 0.0, ""
        n_cmp = 0
        for key in sorted(rep["cases"]):
            j, p = rep["cases"][key], py.get(key)
            if p is None:
                # 不能 SKIP —— 对拍没做就等于没验，必须按失败计。
                print("    [FAIL] %s：Python 侧无此序列，无法对拍" % key)
                fails += 1
                continue
            if j["nLegs"] != p["nLegs"] or j["nCases"] != p["nCases"]:
                print("    [FAIL] %s 数量不同: JS 区间%d/案例%d  vs  "
                      "PY 区间%d/案例%d" % (key, j["nLegs"], j["nCases"],
                                            p["nLegs"], p["nCases"]))
                fails += 1
                continue
            keyed = {(c["d0"], c["d1"]): c for c in p["cases"]}
            bad = 0
            for c in j["cases"]:
                q = keyed.get((c["d0"], c["d1"]))
                if q is None:
                    print("    [FAIL] %s 案例 %s~%s 在 Python 侧不存在"
                          % (key, c["d0"], c["d1"]))
                    bad += 1
                    continue
                if c["quad"] != q["quad"] or c["calDays"] != q["calDays"] or \
                   c["bars"] != q["bars"] or c["nCommon"] != q["nCommon"]:
                    print("    [FAIL] %s 案例 %s~%s 结构性字段不同: js=%s py=%s"
                          % (key, c["d0"], c["d1"],
                             (c["quad"], c["calDays"], c["bars"], c["nCommon"]),
                             (q["quad"], q["calDays"], q["bars"], q["nCommon"])))
                    bad += 1
                for f in ("F", "R"):
                    diff = abs(c[f] - q[f])
                    if diff > max_diff:
                        max_diff, max_where = diff, "%s %s~%s %s" % (key, c["d0"], c["d1"], f)
                    if diff > TOL:
                        print("    [FAIL] %s 案例 %s~%s %s 偏差 %.3e"
                              % (key, c["d0"], c["d1"], f, diff))
                        bad += 1
                if (c["A"] is None) != (q["A"] is None):
                    print("    [FAIL] %s 案例 %s~%s A 的 null 性不同"
                          % (key, c["d0"], c["d1"]))
                    bad += 1
                elif c["A"] is not None and abs(c["A"] - q["A"]) > TOL:
                    print("    [FAIL] %s 案例 %s~%s A 偏差 %.3e"
                          % (key, c["d0"], c["d1"], abs(c["A"] - q["A"])))
                    bad += 1
            fails += bad
            n_cmp += len(j["cases"])
            print("    %-4s 区间 %3d 个、案例 %3d 个、丢弃 %d+%d  —— %s"
                  % (key, j["nLegs"], j["nCases"], j["dropped"]["noObs"],
                     j["dropped"]["common"], "一致 ✔" if bad == 0 else "不一致 ✘"))
        if n_cmp == 0:
            print("    [FAIL] 一条案例都没比上 —— 对拍等于没做。")
            fails += 1
        print("  对拍案例数  : %d" % n_cmp)
        print("  数值最大偏差: %.3e  (%s)" % (max_diff, max_where or "—"))
        print("  容差        : %.1e" % TOL)

    if not args.keep:
        for p in (EXPECTED, REPORT):
            if os.path.exists(p):
                os.remove(p)

    # ---------- 5. 渲染代码真跑一遍 ----------
    print()
    print("=" * 74)
    print("渲染级校验：用假 DOM 执行界面脚本")
    print("=" * 74)
    try:
        subprocess.run(["node", os.path.join(HERE, "smoke_page.js"),
                        HTML_FILE, PAGE_REPORT],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=180, check=False)
        with open(PAGE_REPORT, encoding="utf-8") as f:
            page = json.load(f)
    except FileNotFoundError:
        print("  未找到 node，跳过渲染级校验。")
        page = None
    if page is not None:
        if not page["ok"]:
            print("  [FAIL] 界面脚本执行时抛异常：")
            print("  " + (page.get("error") or "")[:1200])
            fails += 1
        else:
            print("  [PASS] 三段 script 在同一上下文内执行无异常")
            print("  [%s] TrendCore / RubberCore 已挂载"
                  % ("PASS" if page["hasTrend"] and page["hasRubber"] else "FAIL"))
            if not (page["hasTrend"] and page["hasRubber"]):
                fails += 1
            print("  [%s] canvas 有绘制输出（写了 %d 段文字）"
                  % ("PASS" if page["nDrawn"] > 0 else "FAIL", page["nDrawn"]))
            if page["nDrawn"] <= 0:
                fails += 1
            print("  副标题   : %s" % page["subText"][:100])
            # 网格计数与 Python 直算对拍（这是界面上最显眼的那张表）
            exp = []
            quad_js = [q for q in ("1-上涨趋势中的上涨区间", "2-上涨趋势中的下跌区间",
                                   "4-下跌趋势中的上涨区间", "5-下跌趋势中的下跌区间")]
            for q in quad_js:
                for lo, hi in ((30, 37), (55, 65)):
                    exp.append(sum(1 for c in py["adj"]["cases"]
                                   if c["quad"] == q and lo <= c["calDays"] <= hi))
            got = [int(x) for x in re.findall(r"class='cn'>(\d+)\s*个<", page["gridHtml"])]
            if got == exp:
                print("  [PASS] 网格计数 = %s（%d 格，与 Python 直算一致）" % (exp, len(got)))
            else:
                print("  [FAIL] 网格计数不一致: 界面=%s  Python=%s" % (got, exp))
                fails += 1

    print()
    print("=" * 74)
    print("  结果 : " + ("全部通过 ✔" if fails == 0 else "发现 %d 处问题 ✘" % fails))

    if not args.keep:
        for p in (EXPECTED, REPORT, PAGE_REPORT):
            if os.path.exists(p):
                os.remove(p)
    else:
        print("  中间文件已保留: %s / %s / %s"
              % (os.path.basename(EXPECTED), os.path.basename(REPORT),
                 os.path.basename(PAGE_REPORT)))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

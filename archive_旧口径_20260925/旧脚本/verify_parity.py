# -*- coding: utf-8 -*-
"""
verify_parity.py —— 一致性校验：rubber_core.js 必须与 Python 口径逐点一致

════════════════════════════════════════════════════════════════════
为什么必须有这个文件
════════════════════════════════════════════════════════════════════
调参界面在浏览器里跑，所以原料取价口径有了第二份实现（rubber_core.js）。
这份实现一旦与 build_dataset.pair_common 有偏差，界面上所有的案例数、
F/R/A 都是错的，而且**错得看不出来** —— 界面不会报错，只会安静地给出
另一套数字。所以每次改动两边任一个，都必须重跑本校验。

校验的是【真实函数】，不是重新实现：
  Python 侧直接调用 build_dataset.pair_common / pick_obs，
  JS 侧直接调用 rubber_core.js 的对应函数，两边吃同一份随机数据。
  任何一处偏差都会打出 Python 的原始值，便于定位。

════════════════════════════════════════════════════════════════════
测试数据刻意构造得比真实数据更刁钻
════════════════════════════════════════════════════════════════════
真实数据的坑主要是"覆盖率在 1~18 之间浮动"。除此之外本校验还专门造：
  * 观测日完全不规则，且含 84~123 天的停割季级空档（触发 stale 分支）
  * 报价市县数在 1~N 间随机浮动（触发 min_common 分支）
  * 两端落在同一个观测上的日期对（触发 o0==o1 判假样本分支）
  * 目标日早于全部观测（触发 bisect 返回 0 的边界）
  * 落在节假日/周末的目标日（触发"吸附到前一个观测"）
另有一组用【真实城市矩阵】抽大量真实日期对做对拍 —— 这是最硬的一组。

用法：
    python verify_parity.py            # 找不到 node 时跳过并返回 0
退出码：0 = 全部一致（或跳过），1 = 发现不一致
"""

import collections
import json
import os
import random
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from build_dataset import load_cities, pick_obs, pair_common, d   # noqa: E402

INPUT_FILE = os.path.join(HERE, "_parity_input.json")
TOL = 1e-6      # 两边都按 float 存储，量级 ~10，理论差远小于此
CITY_POOL = ["海口", "三亚", "儋州", "琼海", "万宁", "文昌", "东方", "五指山",
             "白沙", "昌江", "乐东", "陵水", "保亭", "琼中", "定安", "屯昌",
             "澄迈", "临高"]


# ------------------------------------------------------------------
# 随机数据构造
# ------------------------------------------------------------------
def gen_cities(rng, n_obs, seed_gap=False):
    """造一份城市矩阵 → (Python 版 {日期:{市县:价}}, JS 版 {dates,names,grid})。

    日期不规则：间隔 2~5 天；seed_gap=True 时随机插入一个 90 天级空档，
    复现停割季那种"上一份观测在三个月前"的情形。
    """
    names = CITY_POOL[:rng.randint(6, len(CITY_POOL))]
    names = sorted(names)
    y, m, dd = 2020, 1, 6
    dates = []
    for _ in range(n_obs):
        dates.append("%04d-%02d-%02d" % (y, m, dd))
        step = rng.randint(2, 5)
        if seed_gap and rng.random() < 0.03:
            step = rng.randint(85, 125)
        dd += step
        while dd > 28:
            dd -= 28
            m += 1
            if m > 12:
                m = 1
                y += 1
    py, grid = {}, []
    for dt in dates:
        k = rng.randint(1, len(names))          # 覆盖率 1~N 浮动
        picked = rng.sample(names, k)
        row = []
        for nm in names:
            row.append(round(rng.uniform(8.0, 17.0), 2) if nm in picked else None)
        grid.append(row)
        py[dt] = {nm: row[j] for j, nm in enumerate(names) if row[j] is not None}
    return py, {"dates": dates, "names": names, "grid": grid}


def gen_targets(rng, dates, n):
    """造目标日：含早于全部观测的、落在观测之间的、正好是观测日的。"""
    out = []
    out.append("2019-01-01")                    # 早于全部观测
    out.append(dates[0])                        # 第一个观测日本身
    out.append(dates[-1])
    for _ in range(n):
        base = rng.choice(dates)
        dd = d(base)
        from datetime import timedelta
        out.append((dd + timedelta(days=rng.randint(-6, 6))).isoformat())
    return out


# ------------------------------------------------------------------
def build_window_probe(rng):
    """构造统计层窗口归属的对拍数据。

    用【Python 端切出来的 legs】当输入，两侧喂同一份，比的纯是窗口归属逻辑。
    拐点处的归属最容易出错（相邻区间共享拐点下标），故专门覆盖。
    """
    from build_dataset import load_futures, FUT_FILE, quad_of
    from trend_analysis import label_legs, judge_args
    from span_quadrant import window_cases

    # ⚠ 2026-09：与 span_quadrant.py 的默认口径对齐
    #   （真实 OHLC + 完整判定参数）。否则这里对拍的是"旧口径"，
    #   而生产跑的是"新口径"—— 正是本项目最忌讳的双份真相。
    bars, dates = load_futures(FUT_FILE, "close", full_ohlc=True)
    cities = load_cities()
    i0 = next(i for i, x in enumerate(dates) if x >= min(cities))
    bars, dates = bars[i0:], dates[i0:]
    _j = judge_args({"max_drawdown_limit": 10.0, "min_rise": 7.2, "window": 37,
                     "continuity_ratio": 0.5, "rebound_ratio": 95.0,
                     "term_recover_bars": 3, "ma": (10, 30)})
    legs = label_legs(bars, major_reversal_pct=15.0, minor_reversal_pct=5.0,
                      min_duration_bars=2, min_change_pct=0.0,
                      max_drawdown_limit=10.0, max_runup_limit=10.0,
                      window=37, min_rise=7.2, judge=_j)

    js_cities = {"dates": sorted(cities),
                 "names": sorted({c for v in cities.values() for c in v}),
                 "grid": []}
    for dt in js_cities["dates"]:
        js_cities["grid"].append([cities[dt].get(nm) for nm in js_cities["names"]])

    probe = {
        "cities": js_cities,
        "prices": [b.close for b in bars],
        "dates": dates,
        "legs": [{"start": lg.start_idx, "end": lg.end_idx,
                  "trend": lg.trend_dir, "dir": lg.direction} for lg in legs],
        "spans": [[30, 37], [55, 65], [20, 30]],
        "modes": ["start", "mid", "inside"],
        "step": 3, "maxStale": 7, "minCommon": 6, "minF": 2.0,
    }
    # Python 侧同一批
    py = []
    for lo, hi in probe["spans"]:
        wins = window_cases(bars, dates, legs, cities, lo, hi, probe["step"],
                            probe["maxStale"], probe["minCommon"], probe["minF"])
        own = {}
        for k, lg in enumerate(legs):
            for j in range(lg.start_idx, lg.end_idx + 1):
                own[j] = k
        for mode in probe["modes"]:
            sel = []
            for w in wins:
                i = dates.index(w["起点日"])
                e = dates.index(w["终点日"])
                k = own.get(i, -1)
                if k < 0:
                    continue
                if mode == "mid":
                    k = own.get(i + (e - i) // 2, -1)
                elif mode == "inside":
                    if own.get(e, -2) != k:
                        continue
                if k < 0:
                    continue
                sel.append([w["起点日"], w["终点日"],
                            quad_of(legs[k].trend_dir, legs[k].direction)])
            sel.sort()
            py.append({"span": [lo, hi], "mode": mode, "n": len(sel),
                       "list": sel})
    return probe, py


def build_cases(rng):
    cases = []
    # ---- A 组：小型随机矩阵，密集边界 ----
    for i in range(12):
        py, js = gen_cities(rng, rng.randint(8, 30), seed_gap=(i % 3 == 0))
        dates = js["dates"]
        picks = [(t, rng.choice([0, 3, 7, 30])) for t in gen_targets(rng, dates, 14)]
        pairs = []
        for _ in range(24):
            a, b = rng.choice(dates), rng.choice(dates)
            pairs.append([a, b, rng.choice([0, 7, 30]), rng.choice([1, 6, 12])])
        # 强制触发 o0==o1 分支：两端用同一天
        pairs.append([dates[3], dates[3], 7, 1])
        # 强制触发"两端吸附到同一观测"：相邻两天
        pairs.append([dates[5], dates[6], 0, 1])
        # 强制触发 stale 分支：跨越 90 天空档
        if len(dates) > 10:
            pairs.append([dates[0], dates[-1], 0, 1])
        cases.append({"cities": js, "picks": picks, "pairs": pairs, "_py": py})

    # ---- B 组：真实城市矩阵 + 真实日期对（最硬的一组）----
    real = load_cities()
    rdates = sorted(real)
    js = {"dates": rdates,
          "names": sorted({c for v in real.values() for c in v}),
          "grid": []}
    for dt in rdates:
        js["grid"].append([real[dt].get(nm) for nm in js["names"]])
    picks, pairs = [], []
    for _ in range(400):
        picks.append([rng.choice(rdates), rng.choice([0, 7, 30])])
        pairs.append([rng.choice(rdates), rng.choice(rdates),
                      rng.choice([0, 7]), rng.choice([1, 6, 12])])
    # 真实数据里最要紧的一类：相邻两次观测
    for i in range(1, len(rdates)):
        pairs.append([rdates[i - 1], rdates[i], 0, 1])
        if i % 7 == 0:
            pairs.append([rdates[i - 1], rdates[i], 7, 6])
    cases.append({"cities": js, "picks": picks, "pairs": pairs, "_py": real})
    return cases


def main() -> None:
    rng = random.Random(20260917)
    cases = build_cases(rng)
    wprobe, wpy = build_window_probe(rng)

    payload = {"cases": [{"cities": c["cities"], "picks": c["picks"],
                          "pairs": c["pairs"]} for c in cases],
               "windows": wprobe}
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    try:
        proc = subprocess.run(
            ["node", os.path.join(HERE, "parity_check.js"), INPUT_FILE],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
    except FileNotFoundError:
        print("未找到 node，跳过一致性校验（请安装 Node.js 后重跑）。")
        os.remove(INPUT_FILE)
        return 0
    if proc.returncode != 0:
        print("node 执行失败：\n", proc.stderr[:2000])
        os.remove(INPUT_FILE)
        return 1
    js_out = json.loads(proc.stdout)

    fails = 0
    max_diff, max_where = 0.0, ""
    n_pick = n_pair = n_null = 0

    def note_diff(diff, where):
        nonlocal max_diff, max_where
        if diff > max_diff:
            max_diff, max_where = diff, where

    for ci, (c, jc) in enumerate(zip(cases, js_out["cases"])):
        py = c["_py"]
        tag = "A组#%d" % ci if ci < 12 else "B组(真实矩阵)"

        # ---------- pickObs ----------
        for k, ((target, ms), j) in enumerate(zip(c["picks"], jc["picks"])):
            o, prices, stale = pick_obs(py, target, ms)
            n_pick += 1
            if o != j["obs"]:
                print("  [FAIL] %s pick#%d target=%s ms=%d 观测日不同: py=%s js=%s"
                      % (tag, k, target, ms, o, j["obs"]))
                fails += 1
            if (prices is not None) != j["has"]:
                print("  [FAIL] %s pick#%d target=%s ms=%d 是否有价不同: py=%s js=%s"
                      % (tag, k, target, ms, prices is not None, j["has"]))
                fails += 1
            if stale != j["stale"]:
                print("  [FAIL] %s pick#%d target=%s ms=%d stale 不同: py=%s js=%s"
                      % (tag, k, target, ms, stale, j["stale"]))
                fails += 1

        # ---------- pairCommon ----------
        for k, ((d0, d1, ms, mc), j) in enumerate(zip(c["pairs"], jc["pairs"])):
            q = pair_common(py, d0, d1, ms, mc)
            n_pair += 1
            if (q is None) != (j is None):
                print("  [FAIL] %s pair#%d (%s→%s ms=%d mc=%d) null 性不同: "
                      "py=%s js=%s" % (tag, k, d0, d1, ms, mc,
                                       "None" if q is None else "值",
                                       "None" if j is None else "值"))
                fails += 1
                continue
            if q is None:
                n_null += 1
                continue
            for key in ("obs0", "obs1", "n_common"):
                jk = {"n_common": "nCommon"}[key] if key == "n_common" else key
                if q[key] != j[jk]:
                    print("  [FAIL] %s pair#%d %s 不同: py=%s js=%s"
                          % (tag, k, key, q[key], j[jk]))
                    fails += 1
            if q["cities"] != j["cities"]:
                print("  [FAIL] %s pair#%d 共同市县集合不同:\n    py=%s\n    js=%s"
                      % (tag, k, q["cities"], j["cities"]))
                fails += 1
            for key, jk in (("r0", "r0"), ("r1", "r1"),
                            ("stale0", "stale0"), ("stale1", "stale1")):
                diff = abs(q[key] - j[jk])
                note_diff(diff, "%s pair#%d %s" % (tag, k, key))
                if diff > TOL:
                    print("  [FAIL] %s pair#%d %s 偏差 %.3e: py=%r js=%r"
                          % (tag, k, key, diff, q[key], j[jk]))
                    fails += 1

    os.remove(INPUT_FILE)

    # ---------- 统计层：窗口归属 ----------
    n_win = 0
    n_win_fail = 0
    jw = js_out.get("windows")
    if not jw:
        print("\n  [FAIL] JS 未返回统计层窗口结果 —— 按失败处理。")
        fails += 1
    else:
        for a, b in zip(wpy, jw):
            n_win += 1
            tag = "跨度%s 归属=%s" % (a["span"], a["mode"])
            if a["n"] != b["n"]:
                print("  [FAIL] %s 窗口数不同: py=%d js=%d"
                      % (tag, a["n"], b["n"]))
                fails += 1
                n_win_fail += 1
                continue
            if a["list"] != [list(x) for x in b["list"]]:
                ca = collections.Counter(tuple(x) for x in a["list"])
                cb = collections.Counter(tuple(x) for x in b["list"])
                only_py = list((ca - cb).items())[:3]
                only_js = list((cb - ca).items())[:3]
                print("  [FAIL] %s 窗口归属不一致（py 独有 %d，js 独有 %d）"
                      % (tag, sum((ca - cb).values()), sum((cb - ca).values())))
                for kk, n in only_py:
                    print("        py only:", kk, "x", n)
                for kk, n in only_js:
                    print("        js only:", kk, "x", n)
                fails += 1
                n_win_fail += 1

    print()
    print("=" * 68)
    print("一致性校验：rubber_core.js  vs  build_dataset.py")
    print("=" * 68)
    print("  用例组        : %d（12 组随机矩阵 + 1 组真实矩阵）" % len(cases))
    print("  pickObs 比对 : %d 次" % n_pick)
    print("  pairCommon   : %d 次（其中 %d 次两边都判为无效样本）" % (n_pair, n_null))
    print("  统计层窗口   : %d 组（跨度 × 归属方式），不一致 %d 组" % (n_win, n_win_fail))
    print("  数值最大偏差 : %.3e  (%s)" % (max_diff, max_where or "—"))
    print("  容差         : %.1e" % TOL)
    print("  结果         : " + ("全部一致 ✔" if fails == 0
                                 else "发现 %d 处不一致 ✘" % fails))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

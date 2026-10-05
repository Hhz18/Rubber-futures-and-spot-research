/* rubber_core.js —— 本项目 Python 端口径的 JavaScript 镜像实现
 *
 * 为什么需要它：
 *   调参界面要"改一个数、立刻看到每格还剩几个案例"，就必须在浏览器里算。
 *   于是 Python 与 JS 各有一份实现 —— 这是唯一会产生"双份真相"的地方。
 *
 * 如何保证不跑偏：
 *   本文件与 build_dataset.py 的 pair_common / pick_obs 逐行对应，
 *   由 verify_parity.py 用随机造的城市矩阵对两份实现做逐点比对，
 *   任何一处偏离都会在测试中报错。改动其一，必须同步改另一个并重跑校验。
 *
 * 【刻意不做的事】
 *   ZigZag 区间切分【不在这里重写】。趋势判定方案的 trend_core.js 已经有一份
 *   经过 parity 校验的 UMD 实现，本文件直接依赖它（浏览器里叫 TrendCore，
 *   Node 里 require 进来）。这里只做橡胶端的增量：共同市县端点法 + 案例聚合。
 *
 * 数据格式：
 *   CITIES = {dates:[...], names:[...], grid:[[价|null, ...], ...]}
 *   SERIES = {dates:[...], ohlc:[[o,h,l,c], ...]}
 *   bars   = [{t,o,h,l,c}, ...]（与 trend_core.js / MinuteBar 同构）
 */

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.RubberCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------- 日期 ----------
  // 一律走 Date.UTC 手算，不用 new Date('2025-09-11')：
  // 后者按 UTC 解析、按本地时区读回，东八区下会整体错一天，差值随之出错。
  function dayNum(s) {
    return Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / 86400000;
  }

  // bisect.bisect_right 的对应实现：
  // 返回"小于等于 x 的元素个数"，故 arr[i-1] 就是"该日或之前最近的一个"。
  function bisectRight(arr, x) {
    var lo = 0, hi = arr.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (x < arr[mid]) hi = mid; else lo = mid + 1;
    }
    return lo;
  }

  var _IDX_CACHE = null;

  // 把 {dates, names, grid} 摊成 {ds:[排序日期], byIdx:[{市县:价}]}，只做一次。
  // Python 端 _date_index 也有同样的缓存，理由相同：每个窗口要取两次价，
  // 不缓存就是 O(窗口数 × 观测数)。
  function cityIndex(cities) {
    if (_IDX_CACHE && _IDX_CACHE.src === cities) return _IDX_CACHE.idx;
    var pairs = [];
    for (var i = 0; i < cities.dates.length; i++) {
      var m = {};
      var row = cities.grid[i];
      for (var j = 0; j < cities.names.length; j++) {
        if (row[j] !== null && row[j] !== undefined) m[cities.names[j]] = row[j];
      }
      pairs.push([cities.dates[i], m]);
    }
    pairs.sort(function (a, b) { return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0; });
    var idx = { ds: pairs.map(function (p) { return p[0]; }),
                byIdx: pairs.map(function (p) { return p[1]; }) };
    _IDX_CACHE = { src: cities, idx: idx };
    return idx;
  }

  // ---------- 原料取价（与 build_dataset.py 一一对应） ----------
  function pickObs(cities, target, maxStale) {
    var ix = cityIndex(cities), ds = ix.ds;
    var i = bisectRight(ds, String(target).slice(0, 10));
    if (i === 0) return { obs: null, prices: null, stale: null };
    var best = ds[i - 1];
    var stale = dayNum(target) - dayNum(best);
    if (stale > maxStale) return { obs: best, prices: null, stale: stale };
    return { obs: best, prices: ix.byIdx[i - 1], stale: stale };
  }

  // 全项目原料取价的唯一入口：两端只用【共同报价市县】求均值再比。
  // 省均价是"当期有报价市县的简单均值"，报价市县数在 1~18 间浮动，
  // 直接比会把"今天有几个县收胶"当成价格信号（详见研究报告第四节）。
  function pairCommon(cities, d0, d1, maxStale, minCommon) {
    var a = pickObs(cities, d0, maxStale), b = pickObs(cities, d1, maxStale);
    if (a.prices === null || b.prices === null) return null;
    if (a.obs === b.obs) return null;          // 同一个观测 → R 恒为 0，是假样本
    var common = [];
    for (var k in a.prices) {
      if (Object.prototype.hasOwnProperty.call(a.prices, k) &&
          Object.prototype.hasOwnProperty.call(b.prices, k)) common.push(k);
    }
    common.sort(function (x, y) { return x < y ? -1 : x > y ? 1 : 0; });
    if (common.length < minCommon) return null;
    var s0 = 0, s1 = 0;
    for (var t = 0; t < common.length; t++) {
      s0 += a.prices[common[t]];
      s1 += b.prices[common[t]];
    }
    return {
      r0: s0 / common.length, r1: s1 / common.length,
      obs0: a.obs, obs1: b.obs, stale0: a.stale, stale1: b.stale,
      nCommon: common.length, cities: common
    };
  }

  // ---------- 期货 ----------
  function futIndex(fdates, target) {
    return bisectRight(fdates, String(target).slice(0, 10)) - 1;
  }

  var PRICE_PICK = {
    close:    function (r) { return r[3]; },
    ohlc_avg: function (r) { return (r[0] + r[1] + r[2] + r[3]) / 4; },
    high:     function (r) { return r[1]; },
    low:      function (r) { return r[2]; }
  };

  // 把 SERIES 抽成 bars（供 TrendCore 用）与价格数组（供算涨幅用）。
  // price 口径要同时作用于【切分】与【算涨幅】，否则会出现
  // "用最高价切分、用收盘价算涨幅"的错配。
  //
  // ⚠ 2026-09 修正：旧版把 o/h/l/c 全部压平成同一个价，于是 ZigZag 的
  //   5%/15% 反转阈值作用在"单口径价格变动"上，而不是真实极值。
  //   实测同一条未复权序列：压平 59 拐点 / 58 区间，真实 OHLC 121 / 120。
  //   现在 h/l 保留真实极值、o/c 换成 price 口径 —— 与 Python 侧
  //   build_dataset.load_futures(full_ohlc=True) 逐点一致。
  function seriesToBars(series, price) {
    var f = PRICE_PICK[price] || PRICE_PICK.close;
    var bars = [], prices = [];
    for (var i = 0; i < series.dates.length; i++) {
      var p = f(series.ohlc[i]);
      prices.push(p);
      bars.push({ t: series.dates[i], o: p, h: series.ohlc[i][1],
                  l: series.ohlc[i][2], c: p });
    }
    return { bars: bars, prices: prices, dates: series.dates };
  }

  // ---------- 分类 ----------
  // 与 build_dataset.py 的 quad_of / bucket_of 对应
  function quadOf(trendDir, direction) {
    if (trendDir === "range") return "3-平稳区间";
    if (trendDir === "up" && direction === "up") return "1-上涨趋势中的上涨区间";
    if (trendDir === "up" && direction === "down") return "2-上涨趋势中的下跌区间";
    if (trendDir === "down" && direction === "up") return "4-下跌趋势中的上涨区间";
    return "5-下跌趋势中的下跌区间";
  }

  var QUADS = ["1-上涨趋势中的上涨区间", "2-上涨趋势中的下跌区间",
               "3-平稳区间",
               "4-下跌趋势中的上涨区间", "5-下跌趋势中的下跌区间"];

  function bucketOf(fPct) {
    var a = Math.abs(fPct);
    if (a < 2) return "0-2%";
    if (a < 10) return "2-10%";
    if (a < 20) return "10-20%";
    return ">20%";
  }

  // ---------- 案例：把 ZigZag 区间补上原料端读数 ----------
  // opt = {maxStale, minCommon, minF(可算A的|F|下限，默认2)}
  function buildCases(bars, prices, dates, legs, cities, opt) {
    opt = opt || {};
    var maxStale = opt.maxStale == null ? 7 : opt.maxStale;
    var minCommon = opt.minCommon == null ? 6 : opt.minCommon;
    var minF = opt.minF == null ? 2 : opt.minF;
    var out = [], dropped = { noObs: 0, common: 0 };
    for (var i = 0; i < legs.length; i++) {
      var lg = legs[i];
      var d0 = dates[lg.start], d1 = dates[lg.end];
      var pr = pairCommon(cities, d0, d1, maxStale, minCommon);
      if (pr === null) {
        var a = pickObs(cities, d0, maxStale), b = pickObs(cities, d1, maxStale);
        if (a.prices === null || b.prices === null) dropped.noObs++;
        else dropped.common++;
        continue;
      }
      var F0 = prices[lg.start], F1 = prices[lg.end];
      var F = (F1 - F0) / F0 * 100;
      var R = (pr.r1 - pr.r0) / pr.r0 * 100;
      out.push({
        seq: i + 1,
        quad: quadOf(lg.trend, lg.dir),
        dir: lg.dir, trend: lg.trend, label: lg.label,
        bucket: bucketOf(F),
        d0: d0, d1: d1,
        bars: lg.bars, calDays: dayNum(d1) - dayNum(d0),
        f0: F0, f1: F1, F: F,
        obs0: pr.obs0, obs1: pr.obs1, r0: pr.r0, r1: pr.r1, R: R,
        A: Math.abs(F) >= minF ? R / F : null,
        G: R - F,
        nCommon: pr.nCommon, stale: Math.max(pr.stale0 || 0, pr.stale1 || 0),
        legChg: lg.chg, dd: lg.dd
      });
    }
    return { cases: out, dropped: dropped };
  }

  // ---------- 统计 ----------
  function median(xs) {
    if (!xs.length) return null;
    var v = xs.slice().sort(function (a, b) { return a - b; });
    var n = v.length;
    return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
  }

  function summarize(list) {
    var F = [], R = [], A = [], G = [];
    for (var i = 0; i < list.length; i++) {
      F.push(list[i].F); R.push(list[i].R); G.push(list[i].G);
      if (list[i].A !== null) A.push(list[i].A);
    }
    var lt1 = 0;
    for (var k = 0; k < A.length; k++) if (A[k] < 1) lt1++;
    return {
      n: list.length, nA: A.length,
      Fmed: median(F), Rmed: median(R), Amed: median(A), Gmed: median(G),
      shareLt1: A.length ? lt1 / A.length : null,
      F: F, R: R, A: A
    };
  }

  // 贪心挑出互不重叠的样本。
  // 滑动窗口相邻之间重叠 (L-1)/L，n 有数千但真正独立的只有 总跨度/窗口长度 个。
  // 直接对全部窗口做 bootstrap 会给出假窄的置信区间 —— 必须先在独立子样本上做。
  function nonOverlapping(list) {
    var v = list.slice().sort(function (a, b) {
      return a.d0 < b.d0 ? -1 : a.d0 > b.d0 ? 1 : 0;
    });
    var out = [], last = null;
    for (var i = 0; i < v.length; i++) {
      if (last === null || v[i].d0 >= last) { out.push(v[i]); last = v[i].d1; }
    }
    return out;
  }

  // 汇总 + 基于【互不重叠子样本】的 A 的置信区间。
  //
  // ⚠️ 点估计与区间必须来自【同一个样本】，否则会出现
  //    "中位数 0.79、CI [1.09,1.95]" 这种区间不含点估计的荒谬结果 ——
  //    那是 1224 个重叠窗口的中位数配了 10 个独立样本的区间，两者不是同一批数据。
  //    所以这里额外给出 AindMed（独立子样本自己的中位数）供并列显示。
  //
  // 另外必须让使用方看到 nIndep：滑动窗口相邻重叠 (L-1)/L，
  // 名义 n 有数千，真正独立的只有 总跨度/窗口长度 个。**那才是有效样本量。**
  function summarizeWithCI(list, nBoot) {
    var s = summarize(list);
    var ind = nonOverlapping(list);
    var Aind = [];
    for (var i = 0; i < ind.length; i++) if (ind[i].A !== null) Aind.push(ind[i].A);
    s.nIndep = ind.length;
    s.nIndepA = Aind.length;
    s.AindMed = Aind.length ? median(Aind) : null;
    s.FindMed = ind.length ? median(ind.map(function (x) { return x.F; })) : null;
    s.RindMed = ind.length ? median(ind.map(function (x) { return x.R; })) : null;
    s.Aci = Aind.length >= 8 ? bootCI(Aind, nBoot || 2000) : null;
    return s;
  }

  // 每类区间的【自然日长度】分布。
  // 这张表是"跨度该设多少"的唯一依据：四类的自然时长差得很远，
  // 卡一个跨度必然有格子空掉。
  function durationStats(cases) {
    var by = {};
    for (var i = 0; i < cases.length; i++) {
      (by[cases[i].quad] = by[cases[i].quad] || []).push(cases[i].calDays);
    }
    var out = {};
    for (var q in by) {
      var v = by[q].slice().sort(function (a, b) { return a - b; });
      var n = v.length;
      out[q] = {
        n: n, min: v[0], max: v[n - 1], median: median(v),
        q25: v[Math.round(0.25 * (n - 1))], q75: v[Math.round(0.75 * (n - 1))],
        all: v
      };
    }
    return out;
  }

  // ---------- 跨度网格 ----------
  // spans = [{lo, hi, name}]（自然日）。返回 象限 × 档 的网格。
  // 案例层与统计层共用本函数 —— 两者的单元格结构相同（quad/calDays/F/R/A），
  // 差别只在"这一行代表的是区间本身还是窗口"，由调用方说明。
  function buildGrid(cases, spans, opts) {
    opts = opts || {};
    var keep = opts.includeRange ? QUADS : QUADS.filter(function (q) {
      return q !== "3-平稳区间";
    });
    var stat = opts.withCI ? summarizeWithCI : summarize;
    var grid = [];
    keep.forEach(function (q) {
      var row = { quad: q, cells: [] };
      spans.forEach(function (sp) {
        var sel = cases.filter(function (c) {
          return c.quad === q && c.calDays >= sp.lo && c.calDays <= sp.hi;
        });
        row.cells.push({ span: sp, list: sel, stat: stat(sel) });
      });
      row.all = cases.filter(function (c) { return c.quad === q; });
      row.allStat = stat(row.all);
      grid.push(row);
    });
    return grid;
  }

  // ---------- 补充口径：固定长度滑动窗口 ----------
  // 与主口径的区别：这里的"象限"是【窗口起点所处的区间】，
  // 不是区间本身的类型。样本量大得多，但口径不同，只作补充。
  function ownerArray(legs, n) {
    var own = new Array(n);
    for (var i = 0; i < n; i++) own[i] = -1;
    for (var k = 0; k < legs.length; k++) {
      for (var j = legs[k].start; j <= legs[k].end && j < n; j++) own[j] = k;
    }
    return own;
  }

  // 窗口枚举很贵（数万个窗口、每个都要取一次原料价），而它【只依赖数据与跨度，
  // 不依赖 ZigZag 阈值】。所以按 (序列, 跨度, 口径) 缓存，
  // 拖阈值滑块时只需重新分配象限，不必重算取价。
  var _WIN_CACHE = {};

  // 自然日跨度 → 需要枚举的【交易日】长度范围。
  // 实测交易日/自然日 ≈ 0.714，这里放宽到 [0.55, 0.95] 以免漏掉边界。
  function barRange(calLo, calHi) {
    return [Math.max(2, Math.floor(calLo * 0.55)),
            Math.max(3, Math.ceil(calHi * 0.95))];
  }

  function enumerateWindows(cities, prices, dates, opt) {
    var ck = [opt.seriesTag, opt.price, opt.calLo, opt.calHi,
              opt.step, opt.maxStale, opt.minCommon].join("|");
    if (_WIN_CACHE[ck]) return _WIN_CACHE[ck];
    var rng = barRange(opt.calLo, opt.calHi);
    var step = Math.max(1, opt.step || 1);
    var minF = opt.minF == null ? 2 : opt.minF;
    var out = [];
    for (var L = rng[0]; L <= rng[1]; L++) {
      for (var i = 0; i + L < prices.length; i += step) {
        var cal = dayNum(dates[i + L]) - dayNum(dates[i]);
        if (cal < opt.calLo || cal > opt.calHi) continue;
        var pr = pairCommon(cities, dates[i], dates[i + L], opt.maxStale, opt.minCommon);
        if (pr === null) continue;
        var F = (prices[i + L] - prices[i]) / prices[i] * 100;
        var R = (pr.r1 - pr.r0) / pr.r0 * 100;
        out.push({
          start: i, end: i + L, bars: L, calDays: cal,
          d0: dates[i], d1: dates[i + L],
          F: F, R: R, G: R - F,
          A: Math.abs(F) >= minF ? R / F : null,
          nCommon: pr.nCommon
        });
      }
    }
    _WIN_CACHE[ck] = out;
    return out;
  }

  // mode: "start" 起点所属 | "mid" 中点所属 | "inside" 完全落在同一区间
  function windowCases(legs, wins, mode) {
    // 必须用 ownerArray（后写的区间覆盖先写的），不能线性扫描取第一个命中的区间。
    // ZigZag 相邻两区间共享拐点下标：leg k 结束处正是 leg k+1 的起点。
    // Python 端 owner 字典是"后写覆盖"，线性扫描取第一个则是"先到先得"，
    // 两者在拐点上会归错象限（实测 174/4641 个窗口因此分类不同）。
    var n = 0;
    for (var t = 0; t < wins.length; t++) {
      if (wins[t].end + 1 > n) n = wins[t].end + 1;
    }
    for (var u = 0; u < legs.length; u++) {
      if (legs[u].end + 1 > n) n = legs[u].end + 1;
    }
    var own = ownerArray(legs, n);
    var out = [];
    for (var i = 0; i < wins.length; i++) {
      var w = wins[i], k = own[w.start];
      if (k < 0) continue;
      if (mode === "mid") {
        k = own[w.start + Math.floor(w.bars / 2)];
        if (k < 0) continue;
      } else if (mode === "inside") {
        var e = own[w.end];
        if (e !== k) continue;          // 含"起点在 leg、终点落在下一个 leg"的情况
      }
      out.push({
        quad: quadOf(legs[k].trend, legs[k].dir),
        d0: w.d0, d1: w.d1, bars: w.bars, calDays: w.calDays,
        F: w.F, R: w.R, G: w.G, A: w.A, nCommon: w.nCommon
      });
    }
    return out;
  }

  // 多个自然日档位 → 每档一组窗口案例
  function windowCasesForSpans(legs, cities, prices, dates, spans, opt) {
    var out = [];
    for (var s = 0; s < spans.length; s++) {
      var o = {};
      for (var k in opt) o[k] = opt[k];
      o.calLo = spans[s].lo; o.calHi = spans[s].hi;
      var wins = enumerateWindows(cities, prices, dates, o);
      out.push(windowCases(legs, wins, opt.mode || "start"));
    }
    return out;
  }

  // 互不重叠子样本上的 bootstrap —— 长跨度下窗口高度重叠，
  // 直接对全部窗口做 bootstrap 会给出假窄的区间。
  function bootCI(values, nBoot, seed) {
    if (values.length < 4) return null;
    var s = seed == null ? 20260917 : seed;
    function rnd() { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; }
    var meds = [];
    for (var b = 0; b < (nBoot || 2000); b++) {
      var draw = [];
      for (var i = 0; i < values.length; i++) {
        draw.push(values[Math.floor(rnd() * values.length)]);
      }
      meds.push(median(draw));
    }
    meds.sort(function (a, b) { return a - b; });
    return [meds[Math.floor(0.025 * meds.length)],
            meds[Math.floor(0.975 * meds.length)]];
  }

  return {
    dayNum: dayNum, bisectRight: bisectRight,
    cityIndex: cityIndex, pickObs: pickObs, pairCommon: pairCommon,
    futIndex: futIndex, seriesToBars: seriesToBars, PRICE_PICK: PRICE_PICK,
    quadOf: quadOf, bucketOf: bucketOf, QUADS: QUADS,
    buildCases: buildCases, summarize: summarize, median: median,
    summarizeWithCI: summarizeWithCI, nonOverlapping: nonOverlapping,
    durationStats: durationStats, buildGrid: buildGrid,
    ownerArray: ownerArray, bootCI: bootCI,
    barRange: barRange, enumerateWindows: enumerateWindows,
    windowCases: windowCases, windowCasesForSpans: windowCasesForSpans
  };
});

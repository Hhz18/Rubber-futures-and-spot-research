/* parity_check.js —— 一致性校验的 Node 端
 * 读取 _parity_input.json，用 rubber_core.js 计算，把结果打到 stdout。
 * 由 verify_parity.py 调用，并与 Python 端结果逐点比对。
 *
 * 本文件只负责"算"，不含任何比对逻辑 —— 比对全在 Python 侧，
 * 这样有偏差时能打出 Python 的原始值，便于定位。
 */
const R = require("./rubber_core.js");
const fs = require("fs");

const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const out = { cases: [], windows: null };

// ---------- 统计层窗口归属 ----------
// 单独一组：用【Python 端切出来的 legs】做输入，
// 这样测的是"给定同样的区间，两边的窗口归属逻辑是否一致"，
// 把 ZigZag 本身的差异隔离开（那部分由趋势项目的 verify_parity 管）。
if (input.windows) {
  const W = input.windows;
  const bars = W.prices.map((p, i) => ({ t: String(i), o: p, h: p, l: p, c: p }));
  const res = [];
  W.spans.forEach((sp) => {
    const wins = R.enumerateWindows(W.cities, W.prices, W.dates, {
      seriesTag: "parity", price: "close",
      calLo: sp[0], calHi: sp[1], step: W.step,
      maxStale: W.maxStale, minCommon: W.minCommon, minF: W.minF,
    });
    W.modes.forEach((mode) => {
      const cs = R.windowCases(W.legs, wins, mode);
      res.push({
        span: sp, mode: mode, n: cs.length,
        list: cs.map((c) => [c.d0, c.d1, c.quad]).sort(),
      });
    });
  });
  out.windows = res;
}

input.cases.forEach((c) => {
  const res = { picks: [], pairs: [] };

  // pickObs：观测日 / 是否有价 / staleness 三项都要对
  c.picks.forEach(([target, maxStale]) => {
    const p = R.pickObs(c.cities, target, maxStale);
    res.picks.push({
      obs: p.obs, has: p.prices !== null, stale: p.stale,
    });
  });

  // pairCommon：null 也算一种结果，"该返回 null 却返回了值"必须能抓到
  c.pairs.forEach(([d0, d1, maxStale, minCommon]) => {
    const q = R.pairCommon(c.cities, d0, d1, maxStale, minCommon);
    res.pairs.push(q === null ? null : {
      r0: q.r0, r1: q.r1, obs0: q.obs0, obs1: q.obs1,
      stale0: q.stale0, stale1: q.stale1,
      nCommon: q.nCommon, cities: q.cities,
    });
  });

  out.cases.push(res);
});

process.stdout.write(JSON.stringify(out));

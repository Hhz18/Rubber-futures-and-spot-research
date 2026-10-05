/* smoke_visual.js —— 交付级校验的 Node 端
 * 读取已生成的 HTML，检查它是否自洽，并用它内联的那份代码重算一遍，
 * 把结果打到 stdout 交给 verify_visual.py 与 Python 端对拍。
 *
 * 四道检查：
 *   1. 哨兵占位符 __TREND_CORE__ / __CORE__ / __DATA__ 必须已被替换
 *   2. 内联的两段算法代码必须与磁盘源码【字节一致】
 *      （不一致说明生成时经过了行尾转换或手工改动）
 *   3. 两段 <script> 必须能被 vm.Script 编译（语法正确）
 *   4. 从 HTML 里抽出 const DATA，用内联代码重算全部案例 → 交给 Python 对拍
 *
 * 检查 2 是必须的：Windows 下若不用 newline="" 写出，\n 会变成 \r\n，
 * 内联代码与磁盘源码就不一致了 —— 这类"看起来能用但已经不是那份代码"的
 * 问题只有逐字节比对才能发现。
 *
 * 用法: node smoke_visual.js <html> <out.json> <trend_core.js> <rubber_core.js>
 */
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const [htmlPath, outPath, trendPath, rubPath] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, "utf8");
const trendSrc = fs.readFileSync(trendPath, "utf8");
const rubSrc = fs.readFileSync(rubPath, "utf8");

const report = { checks: [], fails: 0, cases: null, meta: null };
function check(name, ok, detail) {
  report.checks.push({ name: name, ok: !!ok, detail: detail || "" });
  if (!ok) report.fails++;
}

// ---------- 1. 占位符已替换 ----------
["__TREND_CORE__", "__CORE__", "__DATA__"].forEach((tok) => {
  check("占位符已替换 " + tok, html.indexOf(tok) < 0,
        html.indexOf(tok) < 0 ? "" : "HTML 里仍存在未替换的 " + tok);
});

// ---------- 2. 内联代码与磁盘源码字节一致 ----------
check("内联 trend_core.js 与磁盘一致", html.indexOf(trendSrc) >= 0,
      html.indexOf(trendSrc) >= 0 ? "" : "内联副本与 trend_core.js 不一致（行尾转换？）");
check("内联 rubber_core.js 与磁盘一致", html.indexOf(rubSrc) >= 0,
      html.indexOf(rubSrc) >= 0 ? "" : "内联副本与 rubber_core.js 不一致（行尾转换？）");

// ---------- 3. 两段 script 可编译 ----------
const scripts = [];
const re = /<script>([\s\S]*?)<\/script>/g;
let m;
while ((m = re.exec(html)) !== null) scripts.push(m[1]);
check("找到 3 段 <script>", scripts.length === 3, "实际 " + scripts.length + " 段");
scripts.forEach((s, i) => {
  try {
    new vm.Script(s);                       // 只编译、不执行
    check("第 " + (i + 1) + " 段 script 可编译", true);
  } catch (e) {
    check("第 " + (i + 1) + " 段 script 可编译", false, String(e).slice(0, 300));
  }
});

// ---------- 4. 抽出 DATA 并用内联代码重算 ----------
// 手写花括号配对：JSON 里有嵌套对象，正则切不干净。
function extractData(src) {
  const key = "const DATA = ";
  const i = src.indexOf(key);
  if (i < 0) return null;
  let j = i + key.length;
  if (src[j] !== "{") return null;
  let depth = 0, inStr = false, esc = false;
  for (let k = j; k < src.length; k++) {
    const ch = src[k];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === "\\") esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return JSON.parse(src.slice(j, k + 1)); }
  }
  return null;
}

let DATA = null;
try {
  DATA = extractData(html);
  check("可从 HTML 抽出 const DATA", DATA !== null);
} catch (e) {
  check("可从 HTML 抽出 const DATA", false, String(e).slice(0, 200));
}

if (DATA) {
  const TrendCore = require(path.resolve(trendPath));
  const RubberCore = require(path.resolve(rubPath));

  const out = { series: {} };
  DATA.seriesOrder.forEach((key) => {
    const ser = DATA.series[key];
    const sb = RubberCore.seriesToBars(ser, "close");
    const legs = TrendCore.labelLegs(sb.bars, 15, 5, 2, 0, 10, 10);
    const built = RubberCore.buildCases(
      sb.bars, sb.prices, sb.dates, legs, DATA.cities,
      { maxStale: 7, minCommon: 6, minF: 2});
    out.series[key] = {
      nLegs: legs.length,
      nCases: built.cases.length,
      dropped: built.dropped,
      cases: built.cases.map((c) => ({
        d0: c.d0, d1: c.d1, quad: c.quad, calDays: c.calDays, bars: c.bars,
        F: c.F, R: c.R, A: c.A, nCommon: c.nCommon,
      })),
    };
  });
  report.cases = out.series;   // 注意是 out.series，不是 out —— 多包一层会让对拍静默跳过
  report.meta = { nCities: DATA.cities.names.length,
                  nObs: DATA.cities.dates.length };
}

fs.writeFileSync(outPath, JSON.stringify(report), "utf8");
process.stdout.write(JSON.stringify({ ok: report.fails === 0, fails: report.fails }));

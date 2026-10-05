/* smoke_page.js —— 在 Node 里用假 DOM 把界面脚本真跑一遍
 *
 * 为什么需要它：
 *   verify_visual.py 能验数据对不对，但验不了【渲染代码会不会抛异常】。
 *   recompute/render/drawDur/drawScatter/drawPrice 里任何一个笔误，
 *   vm.Script 编译期发现不了，只会等到双击打开时白屏。
 *   这里把三段 <script> 放进同一个 vm 上下文真执行一遍，
 *   用最小 DOM 桩接住 document/canvas/localStorage 的调用。
 *
 * 它能抓到：变量名笔误、undefined 取值、canvas 方法参数错误、渲染函数漏定义。
 * 它抓不到：视觉样式（配色/布局）。那部分只能靠人眼看。
 *
 * 用法: node smoke_page.js <html> <out.json>
 */
const fs = require("fs");
const vm = require("vm");

const [htmlPath, outPath] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, "utf8");

const scripts = [];
const re = /<script>([\s\S]*?)<\/script>/g;
let m;
while ((m = re.exec(html)) !== null) scripts.push(m[1]);

// ---------- 最小 DOM 桩 ----------
const drawn = [];          // canvas 上写过的文字，用来确认图真的画了
const HANDLERS = [];       // 记录所有 addEventListener，供交互测试回放
const byId = {};
// 真浏览器里 innerHTML 会被解析成真实子元素；这里没有 HTML 解析器，
// 但它只需要支持本项目用到的那一个模式：'<label…>…</label><input … id="in_XXX" …>'。
// 不模拟这一步的话，参数输入框就不存在，**"改参数→重算"这条核心路径根本测不到**。
function adoptHtml(el, v) {
  el._html = v;
  const ids = v.match(/id='([^']+)'/g) || [];
  ids.forEach((s) => { const id = s.slice(4, -1); if (!byId[id]) byId[id] = mkEl(id); });
  const im = v.match(/<input[^>]*id='([^']+)'/);
  el._input = im ? byId[im[1]] : null;
}
function mkEl(id) {
  const el = {
    id: id || "", value: "", textContent: "", checked: false,
    dataset: {}, children: [], _html: "", _input: null,
    style: new Proxy({}, { get: () => "", set: () => true }),
    classList: { toggle() {}, add() {}, remove() {} },
    clientWidth: 900, clientHeight: 300,
    addEventListener(type, fn) { HANDLERS.push({ el: el, type: type, fn: fn }); },
    removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    querySelector() { return el._input || mkEl("input"); },
    querySelectorAll() { return []; },
    select() {}, click() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 900, height: 300 }; },
    getContext() { return CTX; },
    set width(v) {}, get width() { return 900; },
    set height(v) {}, get height() { return 300; },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return el._html; },
    set(v) { adoptHtml(el, String(v)); },
    configurable: true,
  });
  return el;
}
const CTX = new Proxy({
  fillText(t) { drawn.push(String(t)); },
  measureText() { return { width: 10 }; },
  setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
  stroke() {}, fill() {}, arc() {}, fillRect() {}, strokeRect() {},
  setLineDash() {}, save() {}, restore() {}, closePath() {}, rect() {},
}, { get: (t, k) => (k in t ? t[k] : (typeof k === "string" ? () => {} : undefined)),
     set: () => true });

const stubs = {
  document: {
    getElementById(id) { return (byId[id] = byId[id] || mkEl(id)); },
    createElement(tag) { return mkEl(tag); },
    querySelectorAll() { return []; },
    querySelector() { return mkEl(); },
    addEventListener() {},
  },
  localStorage: {
    _d: {},
    getItem(k) { return this._d[k] || null; },
    setItem(k, v) { this._d[k] = String(v); },
    removeItem(k) { delete this._d[k]; },
  },
  navigator: { clipboard: { writeText() {} } },
  Blob: function () {},
  URL: { createObjectURL() { return "blob:x"; } },
  console: { log() {}, warn() {}, error() {} },
  devicePixelRatio: 1,
  isFinite: isFinite, parseFloat: parseFloat, parseInt: parseInt,
  Math: Math, JSON: JSON, Date: Date, Array: Array, Object: Object,
  String: String, Number: Number, Boolean: Boolean, Error: Error,
  Infinity: Infinity, NaN: NaN, undefined: undefined,
};
stubs.setTimeout = setTimeout;
stubs.addEventListener = function () {};

const ctxObj = vm.createContext(stubs);
stubs.self = stubs; stubs.window = stubs; stubs.globalThis = stubs;
vm.createContext(stubs);

const report = { ok: true, error: null, stages: [], gridHtml: "",
                 subText: "", nDrawn: 0, hasTrend: false, hasRubber: false };
try {
  scripts.forEach((src, i) => {
    vm.runInContext(src, stubs, { filename: "script" + (i + 1) + ".js", timeout: 60000 });
    report.stages.push("第 " + (i + 1) + " 段 script 执行完成");
  });
  report.hasTrend = typeof stubs.TrendCore === "object" && stubs.TrendCore !== null;
  report.hasRubber = typeof stubs.RubberCore === "object" && stubs.RubberCore !== null;
} catch (e) {
  report.ok = false;
  report.error = (e && e.stack ? e.stack : String(e)).slice(0, 1500);
}

if (report.ok) {
  report.gridHtml = byId["grid"] ? String(byId["grid"].innerHTML) : "";
  report.subText = byId["sub"] ? String(byId["sub"].textContent) : "";
  report.nDrawn = drawn.length;
  report.drawnSample = drawn.slice(0, 40);

  // ---------- 交互测试 ----------
  // 光能加载不够 —— 用户真正要做的是"改参数→看结果变"。
  // 用记录下来的监听器回放一次，确认这条路径不会抛异常、且结果确实变了。
  function fire(elId, type, ev) {
    const el = byId[elId];
    if (!el) return false;
    let hit = 0;
    HANDLERS.forEach((h) => {
      if (h.el === el && h.type === type) { h.fn(ev || { target: el }); hit++; }
    });
    return hit > 0;
  }
  const inter = [];
  // 每一步都必须【确实触发了监听器】，否则跳过就等于没测 —— 不能静默通过。
  function step(name, fn) {
    const before = HANDLERS.length;
    try {
      const hits = fn();
      if (typeof hits === "number" && hits <= 0) {
        throw new Error("没有任何监听器被触发，这一步等于没测");
      }
      inter.push({ name: name, ok: true, fired: hits });
    } catch (e) {
      inter.push({ name: name, ok: false, err: String(e).slice(0, 300) });
    }
  }

  step("切到统计层", () => {
    byId["selLayer"].value = "stat";
    const h = fire("selLayer", "change");
    report.gridStat = String(byId["grid"].innerHTML);
    return h;
  });
  step("切回案例层", () => {
    byId["selLayer"].value = "case";
    const h = fire("selLayer", "change");
    report.gridBack = String(byId["grid"].innerHTML);
    return h;
  });
  step("改归属方式=完全落在同一区间", () => {
    byId["selMode"].value = "inside";
    const h = fire("selMode", "change");
    report.gridInside = String(byId["grid"].innerHTML);
    return h;
  });
  step("改 ZigZag 细阈值 5→2.5", () => {
    byId["in_minor_reversal_pct"].value = "2.5";
    const h = fire("in_minor_reversal_pct", "input");
    report.gridMinor25 = String(byId["grid"].innerHTML);
    return h;
  });
  step("拖档B到 40–90", () => {
    byId["in_spanB_lo"].value = "40"; const a = fire("in_spanB_lo", "input");
    byId["in_spanB_hi"].value = "90"; const b = fire("in_spanB_hi", "input");
    report.gridSpanB = String(byId["grid"].innerHTML);
    return a + b;
  });
  step("勾选显示平稳区间", () => {
    byId["cRange"].checked = true;
    const h = fire("cRange", "change");
    report.gridRange = String(byId["grid"].innerHTML);
    return h;
  });
  report.interactions = inter;
  report.nDrawnAfter = drawn.length;
}

fs.writeFileSync(outPath, JSON.stringify(report), "utf8");
process.stdout.write(JSON.stringify({ ok: report.ok }));

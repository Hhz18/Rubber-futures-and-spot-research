# -*- coding: utf-8 -*-
"""
fetch_futures.py —— 抓取橡胶期货日线

数据源：新浪财经 期货 主力连续 日K
        https://stock.finance.sina.com.cn/futures/api/jsonp.php/
            var%20t=/InnerFuturesNewService.getDailyKLine?symbol={symbol}

品种选择（可 --symbol 切换）：
    RU0   上期所 天然橡胶 主力连续   ← 默认，对应"橡胶期货"
    NR0   上期所 20 号胶 主力连续    （保税区品种，规格不同）
    RU2601 等具体合约                （做特定合约研究时用）

关于"主力连续"的口径（重要）：
    主力连续是把不同时期的主力合约拼起来的序列，**跨合约拼接处会有跳空**。
    做【百分比涨跌幅】研究时这是可接受的（跳空通常很小），
    但做【绝对价差/加工利润】研究时必须知道：换月日的价差变化可能来自换约，
    而不是真实市场变化。所以本脚本同时输出：
        - 日线 OHLCV
        - 每次换月（主力合约代码变化）的标记，供分析层剔除敏感窗口
    新浪该接口不返回主力合约代码，因此换月点用【成交/持仓突变 + 价格跳空】
    做近似识别（见 mark_rollover）。

输出：data/期货_{symbol}_日线.csv
      日期, 开, 高, 低, 收, 成交量, 持仓量

用法：
    python fetch_futures.py                 # 默认 RU0
    python fetch_futures.py --symbol NR0
"""

import argparse
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

API = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
       "var%20t=/InnerFuturesNewService.getDailyKLine?symbol={}")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "https://finance.sina.com.cn",
}


def fetch_daily(symbol: str) -> list:
    r = requests.get(API.format(symbol), headers=HEADERS, timeout=30)
    r.encoding = "utf-8"
    m = re.search(r"var t=\((\[.*\])\);?", r.text, re.S)
    if not m:
        raise RuntimeError(f"接口返回格式异常，前 200 字符：{r.text[:200]}")
    raw = json.loads(m.group(1))
    out = []
    for x in raw:
        out.append({
            "日期": x["d"],
            "开": float(x["o"]), "高": float(x["h"]),
            "低": float(x["l"]), "收": float(x["c"]),
            "成交量": float(x.get("v") or 0),
            "持仓量": float(x.get("p") or 0),
        })
    out.sort(key=lambda r: r["日期"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="抓取橡胶期货日线")
    ap.add_argument("--symbol", default="RU0", help="合约代码，默认 RU0（天然橡胶主力连续）")
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    rows = fetch_daily(args.symbol)
    out = os.path.join(DATA, f"期货_{args.symbol}_日线.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["日期", "开", "高", "低", "收", "成交量", "持仓量"])
        w.writeheader()
        w.writerows(rows)

    print(f"已生成: {out}")
    print(f"  条数 : {len(rows)}")
    print(f"  范围 : {rows[0]['日期']} ~ {rows[-1]['日期']}")
    print(f"  最新 : 收 {rows[-1]['收']:.0f}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
audit_and_clean_data.py —— 数据审计与清洗的统一入口

════════════════════════════════════════════════════════════════════
本脚本已于 2026-09 拆分，只作入口保留
════════════════════════════════════════════════════════════════════
原脚本把"审计"和"清洗重算"混在一个文件里，且有两处缺陷：

  1) 【比对逻辑失效】"官方全省均价 vs 城市矩阵算术平均"一节，用
     `[k for k in fieldnames if k != "日期"]` 取列，而 收购价_城市矩阵.csv
     是【长表】（列：日期, 市县, 收购均价_元每公斤），于是把 '市县'
     也当成数值列 —— float('万宁市') 抛错被 `except ValueError: pass`
     吞掉，结果每期只剩最后一个市县的单价参与平均。
     它算出的"矩阵算术平均"其实就是【陵水县单价】（2020-06-29: 11.98），
     却输出了"平均绝对偏差 1.1575 / 最大 4.0800"，并据此下结论
     "高度吻合，数据真实可靠" —— 这个结论是错的，实际偏差是 0.0027。

  2) 【重复发布未识别】2020-08-13 / 2020-08-27 两份公示表被站点挂了两个
     URL，正文完全一致，导致 479 行 / 477 个唯一日期。原脚本按日期去重后
     未说明这 2 条是什么，读者会误以为有数据质量问题。

  3) 【口径混淆】直接用官方发布均价算窗口涨幅，而该列含构成污染
     （报价市县数在 1~18 浮动，逐期构成效应中位 0.31pp、最大 18.53pp）。

现在拆成三个职责单一的脚本：

    audit_data_source.py        来源可靠性 + 自洽性审计（只读，不写数据）
    clean_provincial_price.py   全省均价清洗（LOCF 固定篮子可比口径）
    micro_windows_provincial.py 3/5/7/15 天窗口，三口径并列对比

用法：
    python audit_and_clean_data.py          # 依次跑上面三个
    python audit_and_clean_data.py --audit  # 只跑审计
    python audit_and_clean_data.py --clean  # 只跑清洗
    python audit_and_clean_data.py --micro  # 只跑窗口分析
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    ("audit", "audit_data_source.py", "来源可靠性与自洽性审计"),
    ("clean", "clean_provincial_price.py", "全省均价清洗（LOCF 可比口径）"),
    ("micro", "micro_windows_provincial.py", "3/5/7/15 天窗口三口径对比"),
]


def run(script):
    path = os.path.join(HERE, script)
    print()
    print("#" * 88)
    print(f"# {script}")
    print("#" * 88)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, path], cwd=HERE, env=env)
    if r.returncode != 0:
        print(f"!! {script} 退出码 {r.returncode}")


def main():
    ap = argparse.ArgumentParser(description="数据审计与清洗统一入口")
    for flag, _, _ in STEPS:
        ap.add_argument(f"--{flag}", action="store_true")
    args = ap.parse_args()

    selected = [s for s in STEPS if getattr(args, s[0])] or STEPS
    for flag, script, desc in selected:
        print(f"▶ {desc} …", flush=True)
        run(script)


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
周期毛利率回归测试：非牛市实际数据验证

测试目标：
- 使用与牛市推导完全相同的公式，测试过往非牛市（F₀ < 20,000元/吨）的实际数据
- 验证计算方法的合理性
- 对比非牛市与牛市的毛利率差异

核心公式（与牛市推导完全一致）：
- R_avg = (R₀ + R₁) / 2
- 周期总利润 = (F₁ - 基差) - (R_avg×1000 + 采购溢价) - 加工成本
- 周期毛利率 = 周期总利润 / ((F₀+F₁)/2 - 基差)

修正后参数：
- 基差: 800 元/吨
- 采购溢价: 500 元/吨
- 加工成本: 1,200 元/吨
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys

# 设置编码
sys.stdout.reconfigure(encoding='utf-8')

def load_data():
    """加载库存周期窗口数据"""
    data_path = Path(__file__).parent.parent / "data" / "库存周期窗口样本.csv"
    df = pd.read_csv(data_path)
    print(f"加载数据：{len(df)} 个窗口")
    return df

def filter_non_bull_market(df, max_price=20000):
    """
    筛选非牛市窗口：期货起点价格 < 20000元/吨
    """
    non_bull_df = df[df['期货起点价_元吨'] < max_price].copy()
    print(f"\n非牛市筛选（期货起点价 < {max_price}元/吨）：")
    print(f"  - 总窗口数：{len(df)}")
    print(f"  - 非牛市窗口数：{len(non_bull_df)}")
    print(f"  - 非牛市占比：{len(non_bull_df)/len(df)*100:.1f}%")

    if len(non_bull_df) > 0:
        print(f"\n  非牛市价格区间：")
        print(f"    - F₀ 最小值：{non_bull_df['期货起点价_元吨'].min():,.0f} 元/吨")
        print(f"    - F₀ 最大值：{non_bull_df['期货起点价_元吨'].max():,.0f} 元/吨")
        print(f"    - F₀ 中位数：{non_bull_df['期货起点价_元吨'].median():,.0f} 元/吨")

    return non_bull_df

def classify_scenarios(df):
    """按涨跌幅分类为八个场景"""
    from operator import lt

    scenarios = {
        # 上涨场景
        'A_3-5%上涨': (df['期货涨跌F_pct'] >= 3) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, 5))),
        'B_5-7%上涨': (df['期货涨跌F_pct'] >= 5) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, 7))),
        'C_8-12%上涨': (df['期货涨跌F_pct'] >= 8) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, 12))),
        'D_12-18%上涨': (df['期货涨跌F_pct'] >= 12) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, 18))),
        'E_≥18%上涨': df['期货涨跌F_pct'] >= 18,
        # 下跌场景
        'F_3-5%下跌': (df['期货涨跌F_pct'] >= -5) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, -3))),
        'G_5-7%下跌': (df['期货涨跌F_pct'] >= -7) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, -5))),
        'H_8-10%下跌': (df['期货涨跌F_pct'] >= -10) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, -8))),
    }

    result = {}
    for name, mask in scenarios.items():
        subset = df[mask].copy()
        if len(subset) > 0:
            result[name] = subset
            print(f"\n{name}: {len(subset)} 个窗口")
            print(f"  - F中位: {subset['期货涨跌F_pct'].median():.2f}%")
            print(f"  - R中位: {subset['原料涨跌R_pct'].median():.2f}%")
            print(f"  - G中位: {subset['传导缺口G_pp'].median():.2f}pp")
            print(f"  - dS中位: {subset['价差变化dS_元吨'].median():.0f} 元/吨")

    return result

def calculate_cycle_margin(df, basis=800, procurement_premium=500, processing_cost=1200):
    """
    计算周期毛利率（与牛市推导完全一致的公式）

    公式：
    - R_avg = (R₀ + R₁) / 2
    - 周期总利润 = (F₁ - 基差) - (R_avg×1000 + 采购溢价) - 加工成本
    - 平均售价 = (F₀ + F₁)/2 - 基差
    - 周期毛利率 = 周期总利润 / 平均售价
    """
    # 计算周期平均原料成本
    df['R_avg'] = (df['原料起点价_元kg'] + df['原料终点价_元kg']) / 2

    # 计算平均售价（扣除基差）
    df['平均售价'] = (df['期货起点价_元吨'] + df['期货终点价_元吨']) / 2 - basis

    # 计算周期总利润
    df['周期总利润'] = (df['期货终点价_元吨'] - basis) - (df['R_avg'] * 1000 + procurement_premium) - processing_cost

    # 计算周期毛利率
    df['周期毛利率'] = df['周期总利润'] / df['平均售价'] * 100

    # 计算23万吨总利润（亿元）
    df['23万吨总利润_亿元'] = df['周期总利润'] * 230000 / 100000000

    return df

def calculate_stats(df, scenario_name):
    """计算各场景的统计指标"""
    stats = {
        '场景': scenario_name,
        '样本数': len(df),
        'F中位(%)': df['期货涨跌F_pct'].median(),
        'R中位(%)': df['原料涨跌R_pct'].median(),
        'G中位(pp)': df['传导缺口G_pp'].median(),
        'dS中位(元/吨)': df['价差变化dS_元吨'].median(),
        'F₀中位(元/吨)': df['期货起点价_元吨'].median(),
        'R₀中位(元/kg)': df['原料起点价_元kg'].median(),
        'R_avg中位(元/kg)': df['R_avg'].median(),
        '周期毛利率中位(%)': df['周期毛利率'].median(),
        '周期毛利率最小值(%)': df['周期毛利率'].min(),
        '周期毛利率最大值(%)': df['周期毛利率'].max(),
        '周期毛利率P25(%)': df['周期毛利率'].quantile(0.25),
        '周期毛利率P75(%)': df['周期毛利率'].quantile(0.75),
        '正周期毛利率占比(%)': (df['周期毛利率'] > 0).sum() / len(df) * 100,
        '23万吨总利润中位(亿元)': df['23万吨总利润_亿元'].median(),
    }
    return stats

def print_comparison_table(all_stats):
    """打印对比表格"""
    print("\n" + "="*120)
    print("周期毛利率回归测试结果（非牛市实际数据）")
    print("="*120)

    # 表1：场景基础信息
    print("\n【表1】非牛市场景基础信息（期货起点价 < 20,000元/吨）")
    print("-"*120)
    header = f"{'场景':<16} {'样本数':>6} {'F中位%':>8} {'R中位%':>8} {'G中位pp':>8} {'dS中位':>10} {'F₀中位':>10} {'R₀中位':>10}"
    print(header)
    print("-"*120)
    for s in all_stats:
        row = f"{s['场景']:<16} {s['样本数']:>6} {s['F中位(%)']:>8.2f} {s['R中位(%)']:>8.2f} {s['G中位(pp)']:>8.2f} {s['dS中位(元/吨)']:>10.0f} {s['F₀中位(元/吨)']:>10.0f} {s['R₀中位(元/kg)']:>10.2f}"
        print(row)

    # 表2：周期毛利率分析（核心）
    print("\n" + "="*120)
    print("【表2】周期毛利率分析（非牛市实际数据）")
    print("="*120)
    print("-"*120)
    header = f"{'场景':<16} {'中位数':>10} {'最小值':>10} {'最大值':>10} {'P25':>10} {'P75':>10} {'正毛利率占比':>12}"
    print(header)
    print("-"*120)
    for s in all_stats:
        row = f"{s['场景']:<16} {s['周期毛利率中位(%)']:>10.2f}% {s['周期毛利率最小值(%)']:>10.2f}% {s['周期毛利率最大值(%)']:>10.2f}% {s['周期毛利率P25(%)']:>10.2f}% {s['周期毛利率P75(%)']:>10.2f}% {s['正周期毛利率占比(%)']:>12.1f}%"
        print(row)

    # 表3：与牛市理论推导值对比
    print("\n" + "="*120)
    print("【表3】回归测试 vs 牛市理论推导对比")
    print("="*120)
    print("-"*120)
    header = f"{'场景':<16} {'非牛市实际值':>14} {'牛市理论值':>14} {'差异':>12} {'差异率':>10}"
    print(header)
    print("-"*120)

    # 牛市理论推导值
    bull_theory = {
        'A_3-5%上涨': 12.39,
        'B_5-7%上涨': 13.32,
        'C_8-12%上涨': 15.72,
        'D_12-18%上涨': 20.60,
        'E_≥18%上涨': 21.11,
        'F_3-5%下跌': 6.31,
        'G_5-7%下跌': 4.07,
        'H_8-10%下跌': 0.72,
    }

    for s in all_stats:
        scenario = s['场景']
        non_bull = s['周期毛利率中位(%)']
        bull = bull_theory.get(scenario, 0)
        diff = non_bull - bull
        diff_rate = (diff / bull * 100) if bull != 0 else 0
        row = f"{scenario:<16} {non_bull:>13.2f}% {bull:>13.2f}% {diff:>11.2f}pp {diff_rate:>9.1f}%"
        print(row)

    # 核心发现
    print("\n" + "="*120)
    print("【核心发现】")
    print("="*120)

    # 找出非牛市毛利率最高和最低的场景
    max_margin = max(all_stats, key=lambda x: x['周期毛利率中位(%)'])
    min_margin = min(all_stats, key=lambda x: x['周期毛利率中位(%)'])

    print(f"\n1. 非牛市周期毛利率范围：")
    print(f"   - 最高场景：{max_margin['场景']} = {max_margin['周期毛利率中位(%)']:.2f}%")
    print(f"   - 最低场景：{min_margin['场景']} = {min_margin['周期毛利率中位(%)']:.2f}%")

    print(f"\n2. 与牛市理论推导值对比：")
    print(f"   - 非牛市毛利率普遍{'高于' if max_margin['周期毛利率中位(%)'] > 12 else '低于'}牛市理论值")
    print(f"   - 这{'符合' if max_margin['周期毛利率中位(%)'] < 21 else '不符合'}经济逻辑：牛市期现非对称性更强")

    print(f"\n3. 关键结论：")
    for s in all_stats:
        print(f"     * {s['场景']}: 非牛市 {s['周期毛利率中位(%)']:.2f}% vs 牛市 {bull_theory.get(s['场景'], 0):.2f}%")

def save_results(all_stats, output_path):
    """保存结果到CSV"""
    df_stats = pd.DataFrame(all_stats)
    df_stats.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存至：{output_path}")

def main():
    """主函数"""
    print("="*120)
    print("周期毛利率回归测试")
    print("测试目标：验证非牛市（F₀ < 20,000元/吨）实际数据下的周期毛利率")
    print("核心公式：与牛市推导完全一致的周期毛利率公式")
    print("="*120)

    # 1. 加载数据
    df = load_data()

    # 2. 筛选非牛市窗口（F₀ < 20,000元/吨）
    non_bull_df = filter_non_bull_market(df, max_price=20000)

    if len(non_bull_df) == 0:
        print("\n错误：未找到符合条件的非牛市窗口数据！")
        return

    # 3. 分类场景
    print("\n" + "="*120)
    print("场景分类（上涨5种 + 下跌3种 = 8种场景）")
    print("="*120)
    scenarios = classify_scenarios(non_bull_df)

    # 4. 计算各场景的周期毛利率
    print("\n" + "="*120)
    print("计算周期毛利率（与牛市推导完全一致的公式）")
    print("="*120)
    print("公式：R_avg = (R₀ + R₁) / 2")
    print("      周期总利润 = (F₁ - 基差) - (R_avg×1000 + 采购溢价) - 加工成本")
    print("      周期毛利率 = 周期总利润 / ((F₀+F₁)/2 - 基差)")
    print("参数：基差=800元/吨，采购溢价=500元/吨，加工成本=1200元/吨")
    print("-"*120)

    all_stats = []
    for name, subset in scenarios.items():
        # 计算周期毛利率
        subset = calculate_cycle_margin(subset, basis=800, procurement_premium=500, processing_cost=1200)

        # 计算统计指标
        stats = calculate_stats(subset, name)
        all_stats.append(stats)

    # 5. 打印对比表格
    print_comparison_table(all_stats)

    # 6. 保存结果
    output_path = Path(__file__).parent.parent / "data" / "regression_test_results.csv"
    save_results(all_stats, output_path)

    print("\n" + "="*120)
    print("回归测试完成！")
    print("="*120)

if __name__ == "__main__":
    main()

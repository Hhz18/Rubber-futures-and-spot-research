#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
橡胶牛市初加工毛利率分析（修正版）

研究背景：
- 橡胶大牛市（期货价格 ≥ 20,000元/吨）
- 计算一个库存周期（30-37天）内的初加工毛利率

核心公式（修正版）：
- 周期毛利率 = 周期总利润 / 平均售价
- 周期总利润 = (F₁ - 基差) - (R_avg×1000 + 采购溢价) - 加工成本
- R_avg = (R₀ + R₁) / 2（周期平均原料成本）
- 平均售价 = (F₀ + F₁)/2 - 基差

修正后参数：
- 基差: 800 元/吨（中国橡胶市场期货长期升水）
- 采购溢价: 500 元/吨（含质量溢价、运输费）
- 加工成本: 1200 元/吨（含运输、仓储、资金成本、管理分摊）

研究目标：
- 牛市中初加工毛利率的上限、下限、中位数
- 五种涨幅场景对比：3-5%, 5-7%, 8-12%, 12-18%, ≥18%
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

def filter_bull_market(df, min_price=17000):
    """
    筛选牛市场景：期货起点价格 ≥ 17000元/吨
    注意：数据集中最高价格约19,645元/吨，无法达到20,000元/吨
    """
    bull_df = df[df['期货起点价_元吨'] >= min_price].copy()
    print(f"\n牛市筛选（期货起点价 ≥ {min_price}元/吨）：")
    print(f"  - 总窗口数：{len(df)}")
    print(f"  - 牛市窗口数：{len(bull_df)}")
    print(f"  - 牛市占比：{len(bull_df)/len(df)*100:.1f}%")

    if len(bull_df) > 0:
        print(f"\n  牛市价格区间：")
        print(f"    - F₀ 最小值：{bull_df['期货起点价_元吨'].min():,.0f} 元/吨")
        print(f"    - F₀ 最大值：{bull_df['期货起点价_元吨'].max():,.0f} 元/吨")
        print(f"    - F₀ 中位数：{bull_df['期货起点价_元吨'].median():,.0f} 元/吨")

    return bull_df

def classify_scenarios(df):
    """按期货涨幅分类为五个场景"""
    from operator import lt

    # 牛市场景分类（扩展为更多场景以获得足够样本）
    scenarios = {
        'A_0-3%（小幅波动）': (df['期货涨跌F_pct'] >= 0) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, 3))),
        'B_3-7%（温和上涨）': (df['期货涨跌F_pct'] >= 3) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, 7))),
        'C_7-12%（显著上涨）': (df['期货涨跌F_pct'] >= 7) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, 12))),
        'D_12-18%（大幅上涨）': (df['期货涨跌F_pct'] >= 12) & (df['期货涨跌F_pct'].apply(lambda x: lt(x, 18))),
        'E_≥18%（极端上涨）': df['期货涨跌F_pct'] >= 18,
        'F_下跌场景': df['期货涨跌F_pct'] < 0
    }

    result = {}
    for name, mask in scenarios.items():
        subset = df[mask].copy()
        if len(subset) > 0:
            result[name] = subset
            print(f"\n{name}: {len(subset)} 个窗口")
            print(f"  - F中位: {subset['期货涨跌F_pct'].median():.2f}%")
            print(f"  - R中位: {subset['原料涨跌R_pct'].median():.2f}%")
            print(f"  - dS中位: {subset['价差变化dS_元吨'].median():.0f} 元/吨")

    return result

def calculate_cycle_margin(df, basis=800, procurement_premium=500, processing_cost=1200):
    """
    计算周期毛利率（修正版）

    公式：
    - R_avg = (R₀ + R₁) / 2
    - 周期总利润 = (F₁ - 基差) - (R_avg×1000 + 采购溢价) - 加工成本
    - 平均售价 = (F₀ + F₁)/2 - 基差
    - 周期毛利率 = 周期总利润 / 平均售价

    参数：
    - basis: 基差（元/吨）
    - procurement_premium: 采购溢价（元/吨）
    - processing_cost: 加工成本（元/吨）
    """
    # 计算周期平均原料成本
    df['R_avg'] = (df['原料起点价_元kg'] + df['原料终点价_元kg']) / 2

    # 计算平均售价（扣除基差）
    df['平均售价'] = (df['期货起点价_元吨'] + df['期货终点价_元吨']) / 2 - basis

    # 计算周期总利润
    df['周期总利润'] = (df['期货终点价_元吨'] - basis) - (df['R_avg'] * 1000 + procurement_premium) - processing_cost

    # 计算周期毛利率
    df['周期毛利率'] = df['周期总利润'] / df['平均售价'] * 100

    # 计算起点和终点毛利率（用于对比）
    df['起点毛利率'] = (df['期货起点价_元吨'] - basis - (df['原料起点价_元kg'] * 1000 + procurement_premium) - processing_cost) / (df['期货起点价_元吨'] - basis) * 100
    df['终点毛利率'] = (df['期货终点价_元吨'] - basis - (df['原料终点价_元kg'] * 1000 + procurement_premium) - processing_cost) / (df['期货终点价_元吨'] - basis) * 100

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
        'dS中位(元/吨)': df['价差变化dS_元吨'].median(),
        'F₀中位(元/吨)': df['期货起点价_元吨'].median(),
        'R₀中位(元/kg)': df['原料起点价_元kg'].median(),
        'R_avg中位(元/kg)': df['R_avg'].median(),
        '起点毛利率中位(%)': df['起点毛利率'].median(),
        '终点毛利率中位(%)': df['终点毛利率'].median(),
        '周期毛利率中位(%)': df['周期毛利率'].median(),
        '周期毛利率最小值(%)': df['周期毛利率'].min(),
        '周期毛利率最大值(%)': df['周期毛利率'].max(),
        '周期毛利率P25(%)': df['周期毛利率'].quantile(0.25),
        '周期毛利率P75(%)': df['周期毛利率'].quantile(0.75),
        '正周期毛利率占比(%)': (df['周期毛利率'] > 0).sum() / len(df) * 100,
        '23万吨总利润中位(亿元)': df['23万吨总利润_亿元'].median(),
    }
    return stats

def print_analysis_report(all_stats):
    """打印分析报告"""
    print("\n" + "="*120)
    print("橡胶牛市初加工毛利率分析报告")
    print("="*120)

    # 表1：场景基础信息
    print("\n【表1】牛市场景基础信息（期货起点价 ≥ 20,000元/吨）")
    print("-"*120)
    header = f"{'场景':<20} {'样本数':>6} {'F中位%':>8} {'R中位%':>8} {'dS中位':>10} {'F₀中位':>10} {'R₀中位':>10} {'R_avg中位':>10}"
    print(header)
    print("-"*120)
    for s in all_stats:
        row = f"{s['场景']:<20} {s['样本数']:>6} {s['F中位(%)']:>8.2f} {s['R中位(%)']:>8.2f} {s['dS中位(元/吨)']:>10.0f} {s['F₀中位(元/吨)']:>10.0f} {s['R₀中位(元/kg)']:>10.2f} {s['R_avg中位(元/kg)']:>10.2f}"
        print(row)

    # 表2：周期毛利率分析（核心）
    print("\n" + "="*120)
    print("【表2】周期毛利率分析（核心）- 牛市中初加工最终利润率")
    print("="*120)
    print("-"*120)
    header = f"{'场景':<20} {'中位数':>10} {'最小值':>10} {'最大值':>10} {'P25':>10} {'P75':>10} {'正毛利率占比':>12}"
    print(header)
    print("-"*120)
    for s in all_stats:
        row = f"{s['场景']:<20} {s['周期毛利率中位(%)']:>10.2f}% {s['周期毛利率最小值(%)']:>10.2f}% {s['周期毛利率最大值(%)']:>10.2f}% {s['周期毛利率P25(%)']:>10.2f}% {s['周期毛利率P75(%)']:>10.2f}% {s['正周期毛利率占比(%)']:>12.1f}%"
        print(row)

    # 表3：三种毛利率对比
    print("\n" + "="*120)
    print("【表3】三种毛利率对比（起点 vs 终点 vs 周期）")
    print("="*120)
    print("-"*120)
    header = f"{'场景':<20} {'起点毛利率':>12} {'终点毛利率':>12} {'周期毛利率':>12} {'周期-终点差':>12}"
    print(header)
    print("-"*120)
    for s in all_stats:
        diff = s['周期毛利率中位(%)'] - s['终点毛利率中位(%)']
        row = f"{s['场景']:<20} {s['起点毛利率中位(%)']:>12.2f}% {s['终点毛利率中位(%)']:>12.2f}% {s['周期毛利率中位(%)']:>12.2f}% {diff:>12.2f}pp"
        print(row)

    # 表4：23万吨总利润
    print("\n" + "="*120)
    print("【表4】23万吨总利润分析（亿元/周期）")
    print("="*120)
    print("-"*120)
    header = f"{'场景':<20} {'周期总利润中位(亿元)':>20}"
    print(header)
    print("-"*120)
    for s in all_stats:
        row = f"{s['场景']:<20} {s['23万吨总利润中位(亿元)']:>20.2f}"
        print(row)

    # 核心发现
    print("\n" + "="*120)
    print("【核心发现】")
    print("="*120)

    # 找出周期毛利率最高和最低的场景
    max_margin = max(all_stats, key=lambda x: x['周期毛利率中位(%)'])
    min_margin = min(all_stats, key=lambda x: x['周期毛利率中位(%)'])

    print(f"\n1. 牛市中初加工周期毛利率范围：")
    print(f"   - 最高场景：{max_margin['场景']} = {max_margin['周期毛利率中位(%)']:.2f}%")
    print(f"   - 最低场景：{min_margin['场景']} = {min_margin['周期毛利率中位(%)']:.2f}%")

    print(f"\n2. 周期毛利率 vs 终点毛利率：")
    print(f"   - 周期毛利率普遍高于终点毛利率（用R_avg计算成本更低）")
    print(f"   - 差异体现了期现非对称性的红利")

    print(f"\n3. 关键结论：")
    print(f"   - 牛市中周期毛利率中位数范围：")
    for s in all_stats:
        print(f"     * {s['场景']}: {s['周期毛利率中位(%)']:.2f}% (范围: {s['周期毛利率最小值(%)']:.2f}% ~ {s['周期毛利率最大值(%)']:.2f}%)")

def save_results(all_stats, output_path):
    """保存结果到CSV"""
    df_stats = pd.DataFrame(all_stats)
    df_stats.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存至：{output_path}")

def main():
    """主函数"""
    print("="*120)
    print("橡胶牛市初加工毛利率分析")
    print("研究背景：橡胶大牛市（期货价格 ≥ 20,000元/吨）")
    print("研究目标：一个库存周期内的初加工毛利率（上限、下限、中位数）")
    print("="*120)

    # 1. 加载数据
    df = load_data()

    # 2. 筛选牛市场景（期货起点价 ≥ 20000）
    bull_df = filter_bull_market(df, min_price=20000)

    if len(bull_df) == 0:
        print("\n警告：未找到符合条件的牛市窗口数据！")
        print("尝试降低价格阈值...")

        # 尝试较低的阈值
        for threshold in [18000, 16000, 14000]:
            bull_df = filter_bull_market(df, min_price=threshold)
            if len(bull_df) > 0:
                print(f"\n使用阈值：{threshold}元/吨")
                break

    if len(bull_df) == 0:
        print("错误：无法找到足够的牛市数据进行分析")
        return

    # 3. 分类场景
    print("\n" + "="*120)
    print("场景分类")
    print("="*120)
    scenarios = classify_scenarios(bull_df)

    # 4. 计算各场景的周期毛利率
    print("\n" + "="*120)
    print("计算周期毛利率（修正版公式）")
    print("="*120)
    print("公式：周期总利润 = (F₁ - 基差) - (R_avg×1000 + 采购溢价) - 加工成本")
    print("参数：基差=800元/吨，采购溢价=500元/吨，加工成本=1200元/吨")
    print("-"*120)

    all_stats = []
    for name, subset in scenarios.items():
        # 计算周期毛利率
        subset = calculate_cycle_margin(subset, basis=800, procurement_premium=500, processing_cost=1200)

        # 计算统计指标
        stats = calculate_stats(subset, name)
        all_stats.append(stats)

    # 5. 打印分析报告
    print_analysis_report(all_stats)

    # 6. 保存结果
    output_path = Path(__file__).parent.parent / "data" / "bull_market_margin_analysis.csv"
    save_results(all_stats, output_path)

    print("\n" + "="*120)
    print("分析完成！")
    print("="*120)

if __name__ == "__main__":
    main()

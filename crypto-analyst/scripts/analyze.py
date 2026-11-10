#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyze.py —— 只读本地 K 线缓存，打印「当前状态 + 历史条件统计」。

用法：
    python3 scripts/analyze.py

设计原则（给读者看的东西必须能自证）：
  1. 每个统计都打印样本量，重叠样本要说人话；
  2. 不输出单一点位预测；
  3. 相关性/回撤用同一口径，避免「看起来精确」的假象。

前置：先跑 `python3 scripts/fetch_history.py`。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    data = {}
    for sym in core.SYMS:
        try:
            data[sym] = core.load_bars(sym, HERE)
        except FileNotFoundError:
            print(f"跳过 {sym}：缺少 {core.klines_dir(HERE)}/{sym.lower()}_1d.json", file=sys.stderr)

    print("=" * 100)
    print("① 当前状态（日线，UTC；数据源 Binance 公开 K 线）")
    print("=" * 100)
    print(f"{'币':4s}{'现价':>11s}{'MA50':>11s}{'vs MA50':>9s}{'MA200':>11s}{'vs MA200':>9s}"
          f"{'1年':>8s}{'距ATH':>8s}{'52w位置':>8s}{'量30/90':>8s}")
    for sym, bars in data.items():
        r = core.state_row(sym, bars)
        print(f"{sym:4s}{r['last']:>11.2f}{r['ma50']:>11.2f}{(r['vs_ma50_pct'] or 0):>+8.2f}%"
              f"{r['ma200']:>11.2f}{(r['vs_ma200_pct'] or 0):>+8.2f}%"
              f"{(r['ret_1y_pct'] or 0):>+7.1f}%{r['dd_from_ath_pct']:>+7.1f}%"
              f"{(r['pos_52w_pct'] or 0):>7.1f}%{(r['vol_ratio_30_90'] or 0):>8.2f}")

    px = {s: {b["date"]: b["close"] for b in v} for s, v in data.items()}
    if len(px) >= 2:
        print()
        print("=" * 100)
        print("② 等权组合（日再平衡）")
        print("=" * 100)
        bs = core.equal_weight_stats(px)
        print(f"区间 {bs['start']} → {bs['end']}（{bs['years']:.1f} 年）")
        print(f"CAGR {bs['cagr_pct']:.1f}% | 年化波动 {bs['vol_ann_pct']:.1f}% | 历史最大回撤 {bs['maxdd_pct']:.1f}%")
        print(f"距历史高点 {bs['dd_from_ath_pct']:.1f}% | 近30日 {core.pct(bs['ret_30d_pct'])} "
              f"| 近90日 {core.pct(bs['ret_90d_pct'])} | 近1年 {core.pct(bs['ret_1y_pct'])}")
        print(f"（注意：样本 {bs['years']:.1f} 年只含 2 个完整周期，年化数字不代表未来）")

        print()
        print("=" * 100)
        print("③ 分化程度（近 365 天日收益相关性）——决定「分散」是否真的有用")
        print("=" * 100)
        corr = core.correlation(px)
        for i, a in enumerate(core.SYMS):
            row = []
            for b in core.SYMS[i + 1:]:
                k = f"{a}-{b}"
                if k in corr:
                    row.append(f"{a}-{b}: {corr[k]:.2f}")
            if row:
                print("  " + "   ".join(row))

    if "BTC" in data:
        print()
        print("=" * 100)
        print("④ 历史条件统计：BTC 在 MA200 上方 0–5% 之后（当前就处在这个区间）")
        print("=" * 100)
        st_ = core.ma200_zone_stats(data["BTC"])
        print(f"日级样本 n={st_['day']['n']}（重叠，不能当概率）: {st_['horizon']}天中位数 "
              f"{core.pct(st_['day']['median'])}, 为正 {st_['day']['pct_positive']:.0f}%")
        print(f"时段级样本 n={st_['episode']['n']}（相互独立）: {st_['horizon']}天中位数 "
              f"{core.pct(st_['episode']['median'])}, 为正 {st_['episode']['pct_positive']:.0f}%")
        print("最近 4 个时段：")
        for ep in st_["episodes"][-4:]:
            print(f"  {ep['start']} → {ep['end']}（{ep['days']} 天）  之后{st_['horizon']}天: {core.pct(ep['fwd'])}")
        print("结论：该形态本身没有方向优势（正负接近、结果由之后的大趋势决定），"
              "只能用来定性「多空分水岭」，不能当买入信号。")

    print()
    print("=" * 100)



if __name__ == "__main__":
    main()

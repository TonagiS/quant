# POS-09 VT15 + 缓冲带 k+2 结果

## ext

- VT15 基线 5bp：年化 16.50%，回撤 -16.51%，卡玛 1.00，换手 26.0x，超额 +8.71pp
- +buffer7 5bp：年化 18.37%，回撤 -15.31%，卡玛 1.20，换手 20.1x，超额 +10.58pp

- 盈亏平衡：buffer 50.8bp，基线 34.3bp
- 判据：C1 True / C2 True / C3 True（换手比 0.77） / C4 True

## base

- VT15 基线 5bp：年化 15.85%，回撤 -21.89%，卡玛 0.72，换手 24.3x，超额 +7.23pp
- +buffer7 5bp：年化 15.15%，回撤 -21.48%，卡玛 0.71，换手 20.1x，超额 +6.53pp

- 盈亏平衡：buffer 33.8bp，基线 31.3bp
- 判据：C1 True / C2 True / C3 False（换手比 0.83） / C4 True

## 判定

- 两池同时满足 C1~C4：**未通过**

```json
{
  "ext": {
    "breakeven_bp": 50.8,
    "breakeven_base_bp": 34.3,
    "C1": true,
    "C2": true,
    "C3": true,
    "C3_turn_ratio": 0.773,
    "C4_delay2_excess_pp": 8.97,
    "C4_exclude3_excess_pp": 12.31,
    "C4": true
  },
  "base": {
    "breakeven_bp": 33.8,
    "breakeven_base_bp": 31.3,
    "C1": true,
    "C2": true,
    "C3": false,
    "C3_turn_ratio": 0.826,
    "C4_delay2_excess_pp": 4.67,
    "C4_exclude3_excess_pp": 2.99,
    "C4": true
  }
}
```

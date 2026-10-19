# V074：当日行动标记与社交收益

本次改动改变行为规则，后续验证使用新的输出目录；V070、V073 的历史结果保留。
V073 冻结矩阵入口会拒绝新行为代码，这是预期保护，不应更新旧协议绕过。

- 决策可见 `peer_attempt_day` 和 `service_attempt_day`，同日已经主动邀请后，后续时段选择其他可用行动。
- 重复伙伴互动的即时联结、心情和归属收益按 `1/sqrt(contacts)` 递减，并随状态接近上限而降低；伙伴收益也遵循此规则。
- 社交回避的互动奖励统一到长期状态函数，移除执行层的重复扣减。
- 日末不再重放最近行动；仅对当天新观察到的缺课更新长期状态。
- 学业与社交连续成功奖励只在对应行动发生时计算。
- 心情日末变化加入双向有界影响及向 0.5 的缓慢回归。0.5 是合成模型中点，不代表实证校准的个人基准。
- 伙伴回应概率暂未改变；本次只修复标记、重复奖励与收益递减。

Linux 验证：

```bash
python -m pytest -q
python -m ruff check src tests
git diff --check
python -m campus_sim.cli run --config configs/three_period_baseline.json --days 30 --seed 11 --output runs/v074-rule-three-period-seed11-30d
python -m campus_sim.cli trajectory-report runs/v074-rule-three-period-seed11-30d
```

与 `runs/v073-check-rule-three-period-30d` 对比重复邀请拒绝、各状态边界人数及每日状态路径。
预期无效重复邀请减少、后续时段有休息；状态边界是否改善需实际运行确认。

# V068 干预实验扩展

原有 `baseline`、`outreach`、`easier_access` 的默认触达范围和时机保留。新增 `targeted_outreach` 配置示例，只在第 5 天向初始低联结组投递信息，信息影响持续 7 天。该分组在初始化时固定，干预不会依据运行中的压力或行为重新挑选学生。

`policy_components.information_delivery` 可设 `enabled`、`day`、`target_group`（`all`、`lower`、`higher`）及可选的 `duration_days`。`policy_components.access` 可设 `barrier_multiplier`、`start_day`、`target_group` 及可选的 `duration_days`。不设持续天数表示持续到运行结束。窗口结束后信息加成和降低门槛的效果停止，但 `outreach_seen` 保留“曾收到信息”的事实。

实际投递产生 `outreach_information` 事件；每天仍受影响的学生产生 `intervention_exposure` 事件。`summary.json` 新增投递数、实际触达学生数、暴露学生天数、信息暴露学生天数和降低门槛暴露学生天数。运行配置与哈希仍保存在原有 manifest 中。

示例：

```bash
python -m campus_sim.cli run --config configs/targeted_outreach.json --days 10 --seed 11 --output runs/v068-targeted-outreach-seed11-10d
```

新版机制与 V063 的 27 组矩阵不直接合并；正式比较需另行冻结驱动、配置、种子、天数与代码提交。

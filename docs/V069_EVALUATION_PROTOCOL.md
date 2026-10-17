# V069 冻结评估协议

机器可读协议位于 `configs/v069-evaluation-protocol.json`。行为代码以提交 `a518ae6e6f9b39d42c7c4977ce26e6b9929228e2` 为基线；矩阵命令在运行前核验当前行为文件未偏离基线，报告也核验每个运行记录的提交。评估工具和文档本身允许后续修订，但须保留本协议 ID 和行为文件内容。四个基础配置由矩阵工具读取，运行时只覆盖种子、结束日和模型参数；报告核验解析后配置的 SHA256。

每组从第 1 天运行至第 10 天，40 名学生、2 名职员。种子为 11、22、33；条件为 `baseline`、`outreach`、`easier_access`、`targeted_outreach`。先进行规则驱动的 12 组实验。Qwen (`qwen-plus`) 和 DeepSeek (`deepseek-chat`) 是可选的独立扩展矩阵，各自 12 组，温度 0.2、每组调用预算 900。付费矩阵需分别运行，不与规则矩阵混合统计。

主要指标为求助率、获得支持率、连续 7 天低联结率、末日平均压力、末日平均心情、过去 7 天内有可观察社交机会却无接触的比例。次要指标为逐学生轨迹分型、孤立状态、行动次数、网络调用和已完成等待天数。比例分母为 40 名学生；支持等待均值只计已完成支持者。轨迹分型采用末尾 7 个完整日，要求全部 40 人具备快照。

每个驱动分别按条件和种子输出单次结果，跨三种子报告均值和样本标准差。同种子中将各政策条件减去基线，再对三次配对差报告均值和样本标准差。报告拒绝缺失、未完成、配置或行为版本不一致、轨迹文件与汇总矛盾的运行。三种子统计仅作模型内部描述，不支持显著性或真实世界因果结论；旧 V063 的结果不纳入新版矩阵。

Linux 项目根目录下执行：

```bash
python -m campus_sim.cli matrix-run-v069 --root runs --driver rule
python -m campus_sim.cli matrix-report-v069 --root runs --driver rule
```

结果目录形如 `runs/v069-matrix-rule-baseline-seed11-10d`，含 `summary.json`、`events.jsonl`、`trajectory_profiles.jsonl` 等。聚合报告为 `runs/v069-matrix-rule-report.json`。矩阵命令在运行前检查目标目录均不存在，避免覆盖旧实验。

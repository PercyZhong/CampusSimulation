# CampusSimulation

基于 Society0 固定提交 `6ba4a6284a8830491cf326fe9840c48c96c742fb` 的合成校园规则场景。所有 ID 与状态都是虚构模型变量。输出仅称为**未校准的模型内情景结果**，不能用于真实学生诊断、个体自杀风险判断或实际干预效果评估。

## 环境与安装

Society0 的正式运行合同面向 Linux/macOS 和 Python 3.12+。在 Linux **本地磁盘**目录执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest -q
```

`pyproject.toml` 把引擎直接依赖锁在上述完整 SHA。没有 API 密钥、HTTP 模型请求或付费模型。运行目录 `runs/`、`.env` 和 `.venv/` 已被 Git 忽略。

## 最小运行与实验

先在 Linux 做两天烟测，命令由固定提交的 `runner-contract.md` 与 `rule_run.py` 核对；以下校园命令仍须在 Linux 实测验收：

```bash
python -m campus_sim.cli run --config configs/baseline.json --days 2 --output runs/smoke-2d
python -m campus_sim.cli batch --configs configs --output runs/experiment
```

批处理生成 3 条件 × 3 种子（11、22、33）的 9 个独立目录，每个 40 名合成主体、30 天。也可直接使用引擎入口：

```bash
python -m society0.kernel.runner --factory campus_sim.plan:build --config configs/baseline.json --output runs/direct-baseline
python -m campus_sim.cli export runs/direct-baseline
```

每个成功导出的目录有 `summary.json`、`daily_metrics.csv`、`events.jsonl`；批处理还有 `comparison.json`。汇总包含原始计数、分母、已完成请求的等待时间、未完成请求数、初始分层和三个种子的均值与范围。导出只读 Society0 `Observation` 的已完成步骤和权威 SQL 事实，失败目录会标记诊断状态。不会重新运行规则决策。

## 恢复对照

源运行需有完整第 15 步。恢复仍使用 1–30 日的原配置、相同种子，并写入全新目录：

```bash
python -m campus_sim.cli run --config configs/baseline.json --seed 11 --output runs/resumed-baseline-11 --source runs/experiment/baseline-seed11 --step 15
python -m campus_sim.cli verify-resume runs/experiment/baseline-seed11 runs/resumed-baseline-11
```

验证逐日指标、行动事实和聚合结果相同，忽略运行 ID。失败后只从 Society0 完整步骤恢复；不得把部分阶段结果作为完成。需要人工核对的 Linux 验收项和证据字段见 [LINUX_ACCEPTANCE.md](docs/LINUX_ACCEPTANCE.md)。

## Windows 检查

Windows 上只进行纯 Python 领域测试与语法检查；本仓库开发时实际执行的是 `python -m pytest -q tests/test_domain.py` 和 `python -m compileall -q src tests`。Society0 POSIX 持久化、完整运行和恢复未在 Windows 宣称通过。

## 模型边界

三个条件只改变声明字段：`outreach` 在第 5 天向所有合成主体显示一般支持信息；`easier_access` 降低请求规则中的预约障碍系数。容量、初始人口、网络、外部学业事件与命名随机流在同 seed 下固定。校园专业人员仍需审查机制含义和参数，不能从三个种子推断统计显著性。详见 [MODEL_CARD.md](docs/MODEL_CARD.md)。

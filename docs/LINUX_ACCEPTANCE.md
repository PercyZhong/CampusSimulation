# Linux 集成验收记录

## 环境与源码身份

- 系统：Ubuntu 20.04.6 LTS；Python 3.12.14，使用独立的 Conda 环境 `campus-sim-py312`。
- Society0：通过 `pip install -e '.[dev]'` 从 Git SHA `6ba4a6284a8830491cf326fe9840c48c96c742fb` 构建并安装，版本 6.0.0。
- 首轮模拟源码：`ca16f26de17344754285fae4cecb20324eb41be8`；修正压力规则后的正式模拟源码：`b2749accda57825f032d49d4581bc6524e9da3ae`。后续 `ad37a88` 只修正静态检查问题，`7527c60` 只加入真实结果示例，`b34eb7b` 增加负向集成测试。
- 运行工件位于 Linux `/home/zpj/CampusSimulation/runs/`，由 `.gitignore` 排除。每个 `runner.json` 和 `summary.json` 记录 run ID、配置哈希及源码身份。

## 实际执行与结果

| 检查 | 实际结果 |
| --- | --- |
| 首次安装 | GitHub HTTPS 曾超时；网络恢复后固定 SHA 安装成功。Git HTTP/HTTPS 代理与代理环境变量均为空。 |
| 首次 Linux 测试 | 4 passed、2 failed：Society0 要求文本主键显式 `NOT NULL`；在 `ca16f26` 修复并保留原失败事实。 |
| 最终测试 | `python -m pytest -q`：9 passed；`python -m ruff check src tests`：All checks passed（在 `b34eb7b` 后执行）。 |
| 两天冒烟 | `runs/smoke-2d` 完成 2 步；Society0 Observation 显示 `failed=false`，从已完成 Results 读到 `s000/rest/completed`。此运行使用首轮压力规则。 |
| 首轮 9 次实验 | `runs/experiment/` 下 9 个 run 均完成 30 步；第 30 天压力全为 1.0，判定为原规则的上限堆积失败模式，不作为条件比较依据。 |
| 修正后 9 次实验 | 下列 9 个独立目录各完成 30 步，run ID 各异；第 30 天平均压力约 0.356–0.418，未再全部触顶。 |
| 容量和对账 | 修正后 9×30 天每日 `served_today ≤ 3`；9 个 `events.jsonl` 的行动状态计数与 `comparison.json` 一致。 |
| 权限与失败边界 | Linux 集成测试验证跨主体读取被拒绝、重复请求和跨主体行动被拒绝、服务容量写入时复检；另验证第 2 步中途失败只保留第 1 个完整步骤，并从该步恢复后与连续运行等价。 |
| 恢复 | `runs/resumed-baseline-b2749ac` 从修正后 baseline/seed 11 的完整第 15 步恢复到第 30 步；`verify-resume` 返回 `{"equivalent":true,"steps":30}`。首轮规则的恢复和独立重跑也各自返回等价。 |
| 耗时 | 修正后 9 个 run 的 `timings.jsonl` 共 270 条，`total_s` 合计 **31.49 秒**。这是模拟步骤耗时合计，不是安装或整批墙钟时间；后者未测量。 |
| 导出资料 | 修正后 9 个 `events.jsonl` 的主体 ID 均属于 `s000`–`s039`，事件字段限于预期结构；完整输出的敏感资料人工复查仍待办。 |

修正后 9 个运行目录：

```text
runs/experiment-b2749ac/baseline-seed11
runs/experiment-b2749ac/baseline-seed22
runs/experiment-b2749ac/baseline-seed33
runs/experiment-b2749ac/outreach-seed11
runs/experiment-b2749ac/outreach-seed22
runs/experiment-b2749ac/outreach-seed33
runs/experiment-b2749ac/easier_access-seed11
runs/experiment-b2749ac/easier_access-seed22
runs/experiment-b2749ac/easier_access-seed33
```

真实运行示例为 [EXAMPLE_RESULT.json](EXAMPLE_RESULT.json)，从 `runs/experiment-b2749ac/baseline-seed11/summary.json` 直接复制；两份文件的 SHA-256 同为 `94fddd83157f676b789f4cefce0cb47966242c5f4ba9e59a42565c8285ca910a`。

## 可复现命令

以下命令与实测路径对应；重跑时必须使用**新输出目录**，避免覆盖现有运行事实：

```bash
python -m pytest -q
python -m ruff check src tests
python -m campus_sim.cli run --config configs/baseline.json --days 2 --output runs/smoke-2d
python -m society0.kernel.observation runs/smoke-2d
python -m campus_sim.cli batch --configs configs --output runs/experiment-b2749ac
python -m campus_sim.cli run --config configs/baseline.json --seed 11 --output runs/resumed-baseline-b2749ac --source runs/experiment-b2749ac/baseline-seed11 --step 15
python -m campus_sim.cli verify-resume runs/experiment-b2749ac/baseline-seed11 runs/resumed-baseline-b2749ac
```

## 待验收与解释边界

- `network_calls=0` 和 `paid_model_calls=0` 是规则后端的设计声明；没有独立网络流量计量或成本审计。模拟代码未配置模型服务或凭据。
- 行动身份、队列和容量由插件写入时重检；容量已用 9×30 天工件和负向集成测试核对。负向测试覆盖本人资料访问、跨主体读取和行动拒绝、重复请求、容量上限及失败步骤恢复；其他权限组合仍可能需要扩展测试。
- 等待均值只包含已服务请求，未完成请求单列。`outreach`、`easier_access` 的效果方向不作为测试断言。30 天内支持人数接近全部人口，压力适应项 `0.15` 均未经经验校准。
- 校园专业人员对支持服务含义、开放时间、容量、参数及初始分层规则的审阅仍待办。不能将这些模型量用于真实学生诊断、个体自杀风险判断或实际政策效果预测。

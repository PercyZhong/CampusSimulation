# Linux 集成验收（待实测）

本文件记录确切命令及待填写证据。当前开发环境为 Windows；**以下 Linux 项目尚未执行，均不标为通过**。本机尝试列出 WSL 发行版时返回 `Wsl/EnumerateDistros/Service/E_ACCESSDENIED`，无法借此完成 Linux 试跑。请使用 Linux 本地磁盘而非网络文件系统。

## 安装与身份

```bash
git rev-parse HEAD
python3.12 --version
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pip freeze | grep -i society0
python -m pytest -q
```

应记录：OS/发行版 `待填`；Python `待填`；场景 SHA `待填`；Society0 SHA `6ba4a6284a8830491cf326fe9840c48c96c742fb（待安装核实）`；安装失败原文与代理状态 `待填`。如 GitHub 安装失败，检查 `git config --get http.proxy`、`git config --get https.proxy`、`env | grep -i proxy`，保留原始错误。

## 两天冒烟和只读观察

```bash
python -m campus_sim.cli run --config configs/baseline.json --days 2 --output runs/smoke-2d
python -m society0.kernel.observation runs/smoke-2d
python -m campus_sim.cli export runs/smoke-2d
```

验收：完成 2 步；`Observation` 可读；`campus_events` 至少一条完成动作；行动表与 `events.jsonl` 可对账。路径/结果/耗时：`待填`。

## 九次实验与复现

```bash
python -m campus_sim.cli batch --configs configs --output runs/experiment
python -m campus_sim.cli run --config configs/baseline.json --seed 11 --output runs/repeat-baseline-11
python -m campus_sim.cli verify-resume runs/experiment/baseline-seed11 runs/repeat-baseline-11
```

九个预期路径：`runs/experiment/{baseline,outreach,easier_access}-seed{11,22,33}`。各需 30 完整步骤、独立目录；核对同种子逐日事实与指标相同，3 条件仅改变 `condition` 声明字段，人口/边/外部事件相同。`verify-resume` 也可对全程重跑核对。9 路径实录、总耗时、失败：`待填`。

## 第 15 步恢复

```bash
python -m campus_sim.cli run --config configs/baseline.json --seed 11 --output runs/resumed-baseline-11 --source runs/experiment/baseline-seed11 --step 15
python -m campus_sim.cli verify-resume runs/experiment/baseline-seed11 runs/resumed-baseline-11
```

验收：源目录未覆盖；分支完成第 30 步；逐日事实、事件与聚合值相同；故障后只能从完整步骤恢复。结果/失败原文：`待填`。

## 统计、权限和审阅

- 检查请求/支持比例分母为合成学生数，等待均值排除未完成请求；`served_total` 等于服务事件数与受支持人数；每日服务数 ≤ `capacity`。
- 检查行动写入身份、重复请求、开放时间、同伴边和容量约束；主体信息只含本人状态与公共服务信息。结果中不得有姓名、学号、联系方式或密钥。
- 记录零付费模型调用与零模型 HTTP 请求的实际运行证据；场景源码身份、配置哈希、引擎 SHA 与 run ID 在每个 `summary.json`/`runner.json` 可追溯。
- 聚合方向不作为测试断言。专业人员对支持服务现实含义和参数的审阅：`待办`。

首次 Linux `python -m pytest -q`：4 passed、2 failed；两个集成测试均在 Society0 `StageStore.create` 阶段因 `schema requires an explicit nonnull primary key` 失败，尚未执行模拟步骤。已将 `campus_students.id` 与 `campus_queue.actor` 的文本主键显式声明 `NOT NULL`；修复后结果待重跑。最初安装曾因 GitHub HTTPS 超时失败，网络恢复后固定 SHA 安装成功。

最终测试结果 `待填`；成本/网络调用次数 `待填`；其他限制与失败 `待填`；真实运行简例（从输出生成，不手造）`待填`。

固定提交 `ca16f26de17344754285fae4cecb20324eb41be8` 的后续实测：Linux Python 3.12.14；`python -m pytest -q` 为 6 passed；两天 run `runs/smoke-2d` 完成 2 步，Society0 Observation 读取到 `s000/rest/completed`；9 个 `runs/experiment/{condition}-seed{11,22,33}` 均完成 30 步且 run ID 不同；`runs/resumed-baseline-11` 从第 15 步恢复与连续运行等价；`runs/repeat-baseline-11` 重跑等价。首轮 9 个运行的第 30 天压力均为 `1.0`，该退化结果保留在原 run 目录中；新规则将写入新目录，不覆盖原结果。总耗时、成本与专业审阅仍待填。

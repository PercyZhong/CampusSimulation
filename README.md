# CampusSimulation

## V073 三时段校园日程

新配置 `configs/three_period_baseline.json` 启用 `periods_per_day: 3`。学生每天在上午、下午、晚上各选择一次行动。课程只在上午开放，求助申请与服务仅在下午开放；排队期间上午仍可上课，其他时段可休息或进行同伴互动。服务名额及队列顺序仍生效。轨迹报告现在显示时段、行动状态及拒绝原因。旧配置默认每天一次行动，既有 V070 运行结果不变。

规则驱动试运行：

```bash
python -m campus_sim.cli run --config configs/three_period_baseline.json --output runs/v073-three-period-rule
python -m campus_sim.cli export runs/v073-three-period-rule
python -m campus_sim.cli trajectory-report runs/v073-three-period-rule
```

## V072 长期状态与记忆扩展（开发中）

在 V070 冻结结果之外，当前开发版本增加了长期状态联动、恢复与反复恶化计数、伙伴回应历史与冲突置信度、重要事件记忆衰减，以及滚动窗口轨迹重新分类和类型转移统计。该版本需要在 Linux 环境完成测试后再建立新的评估协议。

基于 Society0 的校园学生行为模拟项目。项目模拟学生的课程、求助、同伴互动、宿舍与社团接触、关系变化、长期状态和行为轨迹，并支持规则驱动、Qwen 和 DeepSeek 三种决策驱动。

> 当前结果是未校准的模型内部场景结果，不用于临床诊断、个体风险判断或现实政策因果结论。

## 当前版本

当前主线已完成 V070 定向外展修复与多驱动评估：

- 10 天模拟，40 名学生，2 名职员；
- 四个条件：`baseline`、`outreach`、`easier_access`、`targeted_outreach`；
- 三个种子：11、22、33；
- 每种驱动 12 组运行；
- 行为轨迹窗口为末尾 7 个完整日；
- 输出轨迹分型、孤立指标、行动选择、关系网络和干预指标；
- 当前已完成规则、Qwen、DeepSeek 三种驱动的 V070 矩阵；
- V070 修复了定向外展决策阶段缺少 `initial_group` 的问题；
- 模型响应解析支持附加文本，并对不可用行动进行安全回退。

评估协议见 [docs/V070_TARGETED_OUTREACH_FIX.md](docs/V070_TARGETED_OUTREACH_FIX.md) 和 [configs/v070-evaluation-protocol.json](configs/v070-evaluation-protocol.json)。

## 安装

项目要求 Linux/macOS、Python 3.12+。在项目根目录执行：

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check src tests
git diff --check
```

Society0 依赖锁定在 `pyproject.toml` 中的固定提交。

## API key

API key 只设置在当前 Linux 终端环境中，不要写入配置文件、README 或 Git：

```bash
read -r -s -p "请输入 Qwen API key: " DASHSCOPE_API_KEY; echo
export DASHSCOPE_API_KEY
read -r -s -p "请输入 DeepSeek API key: " DEEPSEEK_API_KEY; echo
export DEEPSEEK_API_KEY
```

Qwen 使用 `DASHSCOPE_API_KEY`，DeepSeek 使用 `DEEPSEEK_API_KEY`。

## 单次运行

规则驱动示例：

```bash
python -m campus_sim.cli run \
  --config configs/baseline.json \
  --days 10 --seed 11 \
  --output runs/example-rule-10d
```

Qwen 或 DeepSeek 示例：

```bash
python -m campus_sim.cli run \
  --config configs/baseline.json \
  --days 10 --seed 11 \
  --output runs/example-qwen-10d \
  --provider qwen --model qwen-plus --max-model-calls 900
```

每次运行应使用新的输出目录。主要输出包括 `summary.json`、`events.jsonl`、`daily_metrics.csv`、`trajectory_profiles.jsonl`、`trajectory-assessment.json` 和 `interactions.jsonl`。运行 `trajectory-report` 还会生成 `trajectory-report.html`。

## V070 多种子矩阵

运行一个驱动的 12 组矩阵：

```bash
python -m campus_sim.cli matrix-run-v070 --root runs --driver rule
python -m campus_sim.cli matrix-report-v070 --root runs --driver rule
```

模型驱动只需将 `rule` 替换为 `qwen` 或 `deepseek`。报告文件为 `runs/v070-matrix-{driver}-report.json`，包含每个条件的三种子均值、样本标准差，以及相对 `baseline` 的同种子配对差。

三种驱动的手工汇总可保存为 `runs/v070-three-driver-comparison.json`。对单次运行生成轨迹评估和 HTML 可视化：\n\n```bash\npython -m campus_sim.cli trajectory-report runs/example-rule-10d\n```

## 四个政策条件

- `baseline`：基线求助门槛和服务规则；
- `outreach`：第 5 天向全体学生提供支持服务信息；
- `easier_access`：降低有效求助门槛；
- `targeted_outreach`：第 5 天向初始低联结组提供支持服务信息。

初始低联结分组在模拟开始时固定，用于定义定向干预对象；学生的动态联结值仍会随互动、缺席和时间变化。

## 行为轨迹与指标

系统记录 `attend`、`rest`、`peer`、`request`、`accept_support`，以及同伴邀请回应、关系形成与衰减、课程/学习小组/宿舍/社团共同出现、长期状态和伙伴记忆。

轨迹评估包含持续缺课、重复求助、无社会接触、混合或已连接等类型，以及有机会却无接触、没有观察到机会、观察到接触和观察不足等孤立状态。LLM 驱动的 `peer` 行动会生成四轮对话；规则驱动使用离线对话后端。

## 实验结果说明

V070 三驱动矩阵已经在 Linux 上完成。不同驱动的基线行为和干预响应存在明显差异，尤其是 DeepSeek 的基线求助率较低、Qwen 对降低求助门槛较敏感。已完成模型运行通过响应解析和四轮对话有效性检查。

这些结果只支持当前协议下的描述性比较。增加种子、延长模拟时间、行为校准和真实数据接入属于后续工作。

## 项目目录

- `src/campus_sim/`：模拟器源码；
- `configs/`：场景和评估协议；
- `tests/`：单元与集成测试；
- `docs/`：阶段说明和评估协议；
- `runs/`：本地实验输出，默认不提交到 Git。

# CampusSimulation

## 本地平台（0.4.0 开发版）

0.4.0 开发树加入学生 LLM 行动决策。实验者可在 Web 工作台选择离线规则或 DeepSeek、Qwen、OpenAI GPT、Gemini、Anthropic Claude 的 API，并可填写提供商支持的其他模型 ID。Claude Code 使用的 Claude 模型在这里通过 Anthropic API 调用；Claude Code 订阅本身不是 API 密钥。辅导员值班和服务资格、排队、容量继续由确定性校园规则执行。

启动 Web 服务前，在 Linux 终端为所选提供商设置环境变量：DeepSeek 使用 `DEEPSEEK_API_KEY`，Qwen 使用 `DASHSCOPE_API_KEY`，OpenAI 使用 `OPENAI_API_KEY`，Gemini 使用 `GEMINI_API_KEY`，Anthropic 使用 `ANTHROPIC_API_KEY`。不要把密钥写入配置文件、命令行或 Git。Qwen 适配器使用阿里云百炼中国站兼容接口；其他地域或自定义网关需要另行配置适配器。Web 页面会显示密钥是否已配置，但不显示密钥值。模型建议列表只是示例，具体 ID 和 API 权限以各提供商账户为准。

命令行示例（DeepSeek 两天、40 名学生，最多 80 次 API 调用）：

```bash
python -m campus_sim.cli run --config configs/baseline.json --days 2 --output runs/llm-deepseek-smoke --provider deepseek --model deepseek-chat --max-model-calls 80
```

每名学生每天至少需要一次行动决策请求。程序在运行前检查调用上限是否达到 `学生数 × 天数`；TLS 握手超时最多重试一次，重试也占用调用上限。因此 30 天、40 名学生至少需要 1200 次，若要留重试余量应设置更高上限；3×3 批量实验有 9 个独立运行，上限按每个运行计算。达到调用上限或 API 返回错误会使运行失败，避免悄悄改用规则行动。LLM 输出的行动仍会经过 Society0 校园插件校验；运行事件记录提供商、模型、动作和提供商返回的用量字段，密钥和完整模型回复不写入事件。`network_calls` 和 `paid_model_calls` 在汇总中统计已记录的成功决策，失败请求及 TLS 重试可能未计入。模型生成仍有随机性，即使将温度设为零，也不能据此假定跨次运行逐项一致。DeepSeek 与 Qwen 已完成小规模真实 API 验收，详见 [0.4.0 验收进度](docs/V040_ACCEPTANCE.md)。

当前开发树包含本地 Web 工作台、合成辅导员值班行动、独立情绪变量、压力前后变化指标和可替换的离线语言层。0.3.0 的 Linux 规则实验、恢复与 Web 筛选技术验收见 [0.3.0 验收记录](docs/V030_ACCEPTANCE.md)；[0.2.0 验收记录](docs/V020_ACCEPTANCE.md)对应旧规则。`docs/EXAMPLE_RESULT.json` 与 `docs/LINUX_ACCEPTANCE.md` 仅对应 0.1.0 规则场景。

在 Linux 的 Python 3.12 环境安装本仓库后，从仓库根目录启动：

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check src tests
python -m campus_sim.cli web --output runs/workbench --port 8765
```

工作台监听 `127.0.0.1:8765`，可启动单次或 3×3 批量运行，并查看已完成运行的逐日平均压力和条件汇总。远程 VS Code 可使用端口转发访问；不要将该无身份认证的开发服务绑定到公网。每次运行使用新目录，结果保存在被 Git 忽略的 `runs/workbench/`。旧归档保持原样。语言层目前只生成可复现的离线同伴交流示例文本，`language_backend` 仅接受 `offline`；它没有调用 LLM，也不控制行动资格、服务队列或容量。今后接入模型需单独实现适配器和验收。

工作台还可按模拟日与行动类型筛选已导出的行动轨迹，每页显示 50 条。该功能只读取 `events.jsonl`，不重新执行主体决策；输入只能选择合成运行目录。Linux 接口与浏览器筛选已验收。

0.3.0 让合成辅导员作为 Society0 规则主体，在学生行动前登记当日值班；服务容量按已登记槽位与配置上限共同约束。新规则已在独立目录重跑 3×3 实验与恢复验收。先前 0.2.0 的归档只对应旧规则。

0.2.0 的合成辅导员只作为服务资源名册，尚不作为会决策的 Society0 主体；每人最多提供 3 个容量槽，配置容量不得超过总槽数。`mood` 是模型内部情绪变量，不是心理量表。新的压力变化指标是最终压力减初始压力。新版本参数、服务规则和语言模板仍待校园专业人员审阅。

复跑时使用**新输出目录**。本轮执行过的命令与结果详见 0.2.0 验收记录：

```bash
python -m campus_sim.cli run --config configs/baseline.json --days 2 --output runs/v020-smoke-2d
python -m campus_sim.cli batch --configs configs --output runs/v020-experiment
python -m campus_sim.cli run --config configs/baseline.json --seed 11 --output runs/v020-resumed-baseline --source runs/v020-experiment/baseline-seed11 --step 15
python -m campus_sim.cli verify-resume runs/v020-experiment/baseline-seed11 runs/v020-resumed-baseline
```

基于 Society0 固定提交 `6ba4a6284a8830491cf326fe9840c48c96c742fb` 的合成校园规则场景。所有 ID 与状态都是虚构模型变量。输出仅称为**未校准的模型内情景结果**，不能用于真实学生诊断、个体自杀风险判断或实际干预效果评估。

## 环境与安装

Society0 的正式运行合同面向 Linux/macOS 和 Python 3.12+。在 Linux **本地磁盘**目录执行；也可使用独立的 Python 3.12 Conda 环境：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest -q
```

`pyproject.toml` 把引擎直接依赖锁在上述完整 SHA。没有 API 密钥、HTTP 模型请求或付费模型。运行目录 `runs/`、`.env` 和 `.venv/` 已被 Git 忽略。

## 最小运行与实验

先在 Linux 做两天烟测，命令由固定提交的 `runner-contract.md` 与 `rule_run.py` 核对。以下命令已在 Ubuntu 20.04.6 / Python 3.12.14 实测；重跑时须更换 `--output` 目录：

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

[真实 Linux 运行示例](docs/EXAMPLE_RESULT.json)从 baseline/seed 11 的 `summary.json` 原样复制。修正压力规则后的 9 个实验及恢复验收记录见 [LINUX_ACCEPTANCE.md](docs/LINUX_ACCEPTANCE.md)。示例中的数值是未校准的模型内结果。

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

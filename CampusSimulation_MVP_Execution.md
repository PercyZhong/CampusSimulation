# CampusSimulation 第一版执行文档

日期：2026-10-10。适用仓库：`https://github.com/PercyZhong/CampusSimulation`（检查时为空仓库，尚无 `main` 提交）。目标工作流：Windows VS Code + Codex 写代码并推送 GitHub；SSH 到 Linux 拉取、安装、运行、验收。

## 0. 产品边界与第一版验收

实现一个**独立于网站、可命令行运行的校园社会模拟场景包**，将来由师兄接入 Society Zero Universe 场景库。第一版研究问题：不同校园支持措施如何改变合成学生群体的求助、获得支持的等待时间、长期孤立和压力变化。所有主体均为虚构合成数据。内部情绪/压力是模型状态变量，不能解释为真实学生诊断、个体自杀风险或实际干预效果。

必须交付：固定源码和随机种子下可复现的完整运行；对照条件；行动与状态事件；聚合指标；从已完成步骤恢复并验证与连续运行等价；运行记录含引擎及场景源码身份。不得加入真实学生个人资料、个体风险排名、诊断阈值、自伤情节/方法模拟或自动联系真实人员。专业支持措施的现实含义与参数应由校园心理健康专业人员审阅；没有经验数据校准时，报告称为“模型内情景结果”。

Society0 的当前公开主分支提交是 `6ba4a6284a8830491cf326fe9840c48c96c742fb`；引擎 `pyproject.toml` 标记版本 6.0.0、Python >=3.12、基础运行支持 macOS/Linux。第一版固定该提交，Codex 不得追踪浮动 `main`，也不得复制/改写 Society0 Core 以绕过接口。Windows 上只执行可移植领域单测；正式集成测试放在 Linux 本地文件系统。仓库证据：[README](https://github.com/lavapapa/society0)、[安装边界](https://github.com/lavapapa/society0/blob/main/docs/core-next/installation.md)、[最小运行示例](https://github.com/lavapapa/society0/blob/main/examples/core_next/rule_run.py)、[插件合同](https://github.com/lavapapa/society0/blob/main/docs/core-next/plugin-contract.md)、[运行合同](https://github.com/lavapapa/society0/blob/main/docs/core-next/runner-contract.md)、[交互合同](https://github.com/lavapapa/society0/blob/main/docs/core-next/interaction-contract.md)、[结果合同](https://github.com/lavapapa/society0/blob/main/docs/core-next/results-contract.md)。

## 1. 创建项目与目录

在空仓库的本地克隆目录中开发；若远端确实仍为空，首次克隆后自行创建 `main`，推送首次提交。若 Codex 开始时仓库已有内容，先检查 `git status`、目录和近期提交，保留已有代码，仅调整冲突部分。

建议布局（可按实际接口调整，保留同等职责）：

```text
CampusSimulation/
  pyproject.toml                    # Python >=3.12；Society0 Git SHA 固定
  README.md                         # Windows→GitHub→Linux 使用说明
  .gitignore                        # .venv/, runs/, __pycache__/, .env
  configs/
    baseline.json
    outreach.json
    easier_access.json
  src/campus_sim/
    __init__.py
    domain.py                       # 纯 Python 领域状态、转移和参数校验
    population.py                   # 合成人口及初始社交网络
    mechanism.py                    # Society0 插件：持久事实、Information、Actions
    schedule.py                     # 每日事件/决策/汇总阶段
    plan.py                         # build(config) -> RunPlan
    analysis.py                     # 从已完成 Results 导出聚合指标
    cli.py                          # 批量运行、分析、比较 CLI
  tests/
    test_domain.py
    test_experiment.py
    test_society0_integration.py
  docs/
    MODEL_CARD.md                    # 参数、假设、范围、失效条件
    LINUX_ACCEPTANCE.md              # 实际命令和验收证据字段
```

不要把运行数据或 `.env` 推送到 GitHub。为首次试跑提供可安装 Python 包和命令行入口；如果直接用 `python -m ...` 更稳，可用模块入口而不增加包装工具。

## 2. 最小领域模型

初版 `n_students=40`、`days=30`、`seed=11`；至少 3 个种子（11、22、33）和 3 个条件（baseline、outreach、easier_access），形成 9 个独立 run 目录。首先用规则 Driver，无 API 密钥，HTTP/付费模型请求为零。时间离散为一天；初始人口、社交边和事件时间表在同一 seed 下固定。数据完全合成，主体用 `s000` 等不对应真实学生的 ID。

每名学生的**领域状态**：学业压力、恢复/休息水平、社会联结、求助意愿、已获得支持状态和累计等待日数；使用 0–1 或明确单位的数值并校验范围。压力等内部状态供机制更新，不在页面中作为真实心理测量发布。另设**可观察行动**：参与课程、休息、向朋友交流、请求支持、接受支持。校园中有课业事件和有限容量的支持服务；资源容量、开放时间与队列顺序属于机制规则，不能由 LLM 文本自行改写。

每天阶段建议：① 外部事件/服务容量准备；② 学生获取权限范围内的信息；③ 主体选择一项行动，行动执行时再次核验资格；④ 更新状态和支持队列；⑤ 保存逐主体行动表与群体指标。具体阶段顺序要根据 Society0 `Phase`、`SequenceSchedule` 和机制步骤钩子的真实 API 实现；同一随机 seed 下，对照条件从相同初始人口启动。采用有名字、可记录的随机流，避免政策分支因为多抽一次随机数就悄悄改变外部事件。

政策条件只改变预先声明的机制：

| 条件 | 机制差异 | 共同保持 |
| --- | --- | --- |
| baseline | 基准支持可及性和自然求助流程 | 人口、外部事件、服务总容量、规则参数基线 |
| outreach | 给符合**场景规则**的合成主体一次非诊断性的支持信息触达机会 | 不使用模型秘密状态给个体贴“高危”标签 |
| easier_access | 减少预约障碍或扩展可见预约时段；若容量也调整，作为单独因素记录 | 不混入 outreach 效应 |

主要结果：请求支持人数/比例、成功获得支持人数/比例、从请求到获得支持的等待天数、持续低社会联结的比例、全群体压力分布。次要结果：各初始分层的服务可及性差异、行动次数、容量占用、未完成请求。输出每个 run 的原始计数和明确分母，平均值与种子范围；不以三次种子声称统计显著或现实因果效果。记录所有参数、配置哈希、场景源码提交和 Society0 提交。

## 3. Society0 接线要求

Codex 应先阅读上述引擎文档与以下源码，再实现，不能猜 API：`examples/core_next/rule_run.py`、`examples/core_next/conversation_pilot.py`、`examples/core_next/typed_records.py`、`src/society0/kernel/{actors,interaction,runner,runtime,schedule,results}.py`。优先采用公开 `Plugin` / `actor_plugin` / `rule_driver_plugin` / `interaction_plugin` / `results_plugin` / `runtime_plugin` / `schedule_plugin` / `RunPlan` / `RunContract`。

- `build(config)` 为可导入的同步计划工厂。`RunContract` 写入固定公开配置和依赖/版本、仿真时间、预算；凭据只有环境变量名，不能写真实值。首次只实现 rule backend；预留 driver 选择，但**不宣称已实现 LLM**。如时间许可，提供无外部调用的假模型消费者证明未来可替换。
- 校园机制通过插件声明 schema、initialize 和安装服务，事实与投影由 Society0 权威存储持久化；恢复以完整步骤为边界。不要把 Python 全局字典当作可恢复世界状态。主体 `ActorRecord` 只保存适合其主观视角的资料；课程、服务队列、关系等共同事实由机制拥有。
- 使用主体绑定的 `Information` 暴露课程安排、可用支持服务、本人请求和允许可见的同伴互动。`Actions` 注册具体校园行动，预检及 handler 写入时复检约束，返回 completed/rejected 等明确状态；领域行为通过 action 写入，不从提示词或汇总器修改世界。
- 阶段 `StepResult` 记录指标、逐主体行动表和必要观察，且可从已完成步骤的 `Observation`/`Results` 读取。`analysis.py` 必须从运行事实导出指标，不能重跑策略生成另一份“结果”。输出可给未来网站接入的稳定 `summary.json`、`daily_metrics.csv`、`events.jsonl`（或等价结构），并记录 schema 版本与 run ID。
- 运行使用 `python -m society0.kernel.runner --factory campus_sim.plan:build --config configs/baseline.json --output runs/...`。从第 15 个完整步骤恢复时，使用相同完整时间范围的配置、`--source ... --step 15 --output <new-directory>`；源目录不覆盖，分析时区别完整结果与失败诊断。具体命令以当前 `runner-contract.md` 和 Linux 实际验收为准。

Society0 现有 `workbench-contract.md` 的离线展示依赖其正式 run 目录。第一版额外产出机器可读的校园汇总，留给师兄对接；**不要写网站“场景库”API，也不要假设内网网站源码在本仓库**。

## 4. 实现顺序与停止条件

1. **Day 1，最小闭环**：创建包、配置校验、合成学生、一个规则动作和 2 天运行；从 Society0 已完成结果中读出至少一条行动。先在 Linux 验证引擎固定提交可安装；如果网络受限，Windows 继续完成纯领域模块，保留 Linux 安装错误及网络证据。
2. **Day 2，校园机制**：补全压力事件、有限服务队列、同伴交流/求助/接受支持；定义三个条件仅改变声明机制，日志记录资格拒绝原因和行动结果。验证同种子同条件重复运行的逐日指标一致。
3. **Day 3，实验与恢复**：完成 3×3 批处理、聚合导出、15→30 日恢复对照、异常后只从完整步骤恢复。README 给出一条最小命令和一条批处理命令；生成一份简短真实运行示例（从 Linux 运行结果生成，不手造）。

优先完成上面三步。如果真实引擎接口使某个设计不成立，记录阻断点、最小复现和实际替代方案；不要将未通过的集成测试标为通过。第一版不要求前端、真实学生数据、复杂心理诊断、付费模型或政策预测。后续可在冻结规则基线后，增加小规模 LLM Driver 对照与专业人员审阅。

## 5. 有意义的验收

- Windows：格式/静态检查和纯领域测试；`git status --short` 确保仅提交源码、配置与文档。不要在 Windows 对 Society0 的 POSIX 持久化做假成功。
- Linux：`python3.12 --version`；在本地磁盘创建 venv；安装固定 Society0 Git SHA 和本包；1 个 2 天冒烟 run 成功且 Observation 能读取；9 个正式 run 各 30 个完整步骤，目录互不覆盖；输出没有姓名、学号、联系方式、密钥。
- 同配置同 seed 重跑逐日事件/指标同值；连续 30 天与完整第 15 天恢复到新目录后的权威结果同值；不同条件的配置差异只落在所声明政策字段。统计逻辑检查分母、等待时间的未完成请求处理、指标范围和行动总数与事件日志对账。
- 插件要证明行动资格/资源容量重检、服务队列不会超容量、主体只读到允许资料。对照实验的“outreach 更好”**不是测试断言**；效果方向是待研究结果。
- `docs/LINUX_ACCEPTANCE.md` 实填 OS/Python、引擎 SHA、场景 SHA、9 个 run 路径、运行总耗时、测试结果、失败与限制、成本/网络调用次数；可以概览失败但保留原始事实。结束前给用户简短完成摘要与仍需人工核查的语义问题。

## 6. Windows VS Code → GitHub → Linux 操作

Windows PowerShell（在 VS Code 终端，首次从空仓库克隆；先确认 `git --version` 可用）：

```powershell
git clone https://github.com/PercyZhong/CampusSimulation.git
cd CampusSimulation
git status --short
```

将**本执行文档**放到仓库根目录，命名 `CampusSimulation_MVP_Execution.md`；在 VS Code 的 Codex 中发送下方提示词。Codex 完成后人工审阅 `git diff --stat`、配置和验收说明，再按自己的流程提交并推送。远端仓库当前为空，首个提交可直接建立 `main`：

```powershell
git add .
git commit -m "feat: add campus simulation MVP"
git branch -M main
git push -u origin main
```

Linux（SSH 后，在自己的实验目录；首次克隆；如果以前克隆过则在已存在的目录 `git pull --ff-only origin main`）：

```bash
git clone https://github.com/PercyZhong/CampusSimulation.git
cd CampusSimulation
git rev-parse HEAD
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest -q
```

本包的 `pyproject.toml` 应通过明确 Git SHA 固定 Society0 基础依赖。具体运行命令、批量命令和 `python -m society0.kernel.observation <run_dir>` 由 Codex 实装后写入 README，不要复制未经运行验证的命令。若服务器 GitHub 克隆/安装失败，先检查服务器上的 `git config --get http.proxy`、`git config --get https.proxy` 和 `env | grep -i proxy`；例如指向未启动的 `127.0.0.1:7897` 时，应修复服务器网络配置后重试，不能把网络失败记为代码测试失败。不要在聊天或仓库公开 API 密钥。

## 7. 可直接交给 Windows VS Code Codex 的提示词

```text
你当前位于 CampusSimulation 仓库。请先完整阅读仓库根目录 CampusSimulation_MVP_Execution.md，然后按其“0–5”的范围在这个空仓库实现校园模拟第一版。请直接编写和修改代码、配置、README、测试，不要只给建议。

重要约束：Society0 引擎固定到文档中的完整 Git SHA，先核对该提交的公开源码与示例，不猜 API；它在 Windows 无正式运行合同，因此 Windows 完成纯领域测试与静态检查，Linux 集成测试步骤写入 README 和 docs/LINUX_ACCEPTANCE.md，未实测的结果明确标记待在 Linux 验证。优先跑通规则 Agent 的 2 天闭环，再补校园插件、三个干预条件、3 seeds × 30 天、结果导出和第 15 天恢复等价验证。不要加入真实学生数据、个体自杀预测或风险排名，不把模拟量解释成临床指标。不要开发网站场景库前端，不调用付费模型，不推送密钥或 runs/ 工件。

实施中保持仓库可复现，所有已完成实验的配置、引擎版本与真实读取路径可追溯。运行测试后给出：修改文件清单、运行过的 Windows 检查、应在 Linux 执行的确切命令、Linux 验收表的未完成项和阻断问题。请先从仓库状态与 Society0 的 rule_run.py / conversation_pilot.py / typed_records.py / 运行/交互/结果合同开始阅读，随后持续实施至第一版代码和文档完成。
```

Linux 首次拉取之后，在远程 VS Code/Codex 中补充提示词：

```text
请阅读 CampusSimulation_MVP_Execution.md 第 4–6 节与 docs/LINUX_ACCEPTANCE.md，在当前 Linux 工作树安装固定引擎提交，执行全部真实集成与 9 个 30 天规则实验，核对连续/恢复等价和结果导出。定位并修复真实失败，更新验收文档与 README 的可运行命令；保留原失败证据。不得调用付费模型、上传真实学生数据或把模拟结果解释成真实个人风险。完成后报告实际成功/失败项及运行工件路径，不要声称未运行的检查已通过。
```

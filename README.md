# CampusSimulation

## 班级、教师与心理委员

所有新实验均包含班级、教师和心理委员。命令行可用 `configs/class_support_baseline.json` 运行示例；比较政策时，应让各条件使用相同的人数和种子。

- 学号 `s000`–`s004` 为 `class-000`，`s005`–`s009` 为 `class-001`，依此每 5 人一班；末班可以不足 5 人。每班首位学生兼任心理委员，仍是原来的学生，每班有一名独立教师 `t000`、`t001` 等。
- 教师只依据既有缺课记录，对连续缺课至少两次的本班学生每日最多提供一次学业关怀；这不是心理评估。心理委员只依据同班可观察的缺课记录，每日最多向一名同学发出支持邀请；对方可能接受或婉拒，心理委员看不到模型中的压力与心情状态。
- 现有 `c###` 是提供非临床支持的辅导员，负责服务名额和后续跟进。对等待较久或有连续缺课记录的受助学生，辅导员可以提供专业咨询服务的信息。`professional_referral_offer` 只表示**提供转介信息**；当前没有模拟心理咨询师接诊、诊断或治疗，也不能把该事件计为已接受专业咨询。
- 结果分别记录班级、教师、心理委员数量，以及教师学业关怀、心理委员支持邀请与转介信息次数。新角色仍使用规则行动；学生本身的日常选择继续遵循所选规则或 LLM 驱动。

```bash
python -m campus_sim.cli run --config configs/class_support_baseline.json --output runs/class-support-baseline
python -m campus_sim.cli export runs/class-support-baseline
```

## V074 社交收益与压力校准

当前开发版本在 V073 三时段机制上继续改进：

- 决策上下文包含 `peer_attempt_day` 和 `service_attempt_day`，同一天已经尝试过的行动不会在后续时段重复选择；
- 重复伙伴互动的联结、心情和归属感收益按互动次数递减，并随状态接近上限而减弱；
- 社交回避奖励统一在长期状态更新中结算，避免同一次互动重复扣减；
- 日末不再重复重放最近行动，只对当天新观察到的缺课更新长期状态；
- 心情加入有界回归，压力使用阻尼与小底座，避免 30 天规则运行快速到达 0 或 1；
- 服务窗口末仍在队列中的请求会记录为右删失，不与容量失败混同。

V074 规则驱动 30 天种子 11 检查结果：无效的 `already_contacted_today` 重复拒绝已消失；真实同伴回应为 4 次拒绝、1 次超时；缺课 262 次，最长连续缺课 6 天；压力范围为 0.089–0.335，归属感均值 0.651，社交回避均值 0.323，未出现状态边界饱和。服务请求 36 人，35 人在窗口内完成支持，1 人于第 30 天请求，属于右删失。

本版本尚未运行 Qwen 或 DeepSeek 矩阵，后续模型实验应使用新的 V074 协议和输出目录。

## V073 三时段校园日程

新配置 `configs/three_period_baseline.json` 启用 `periods_per_day: 3`。学生每天在上午、下午、晚上各选择一次行动。课程只在上午开放，求助申请与服务仅在下午开放；排队期间上午仍可上课，其他时段可休息或进行同伴互动。服务名额及队列顺序仍生效。轨迹报告现在显示时段、行动状态及拒绝原因。旧配置默认每天一次行动，既有 V070 运行结果不变。

规则驱动试运行：

```bash
python -m campus_sim.cli run --config configs/three_period_baseline.json --output runs/v073-three-period-rule
python -m campus_sim.cli export runs/v073-three-period-rule
python -m campus_sim.cli trajectory-report runs/v073-three-period-rule
```

## V072 长期状态与记忆扩展

在 V070 冻结结果之外，当前代码增加了长期状态联动、恢复与反复恶化计数、伙伴回应历史与冲突置信度、重要事件记忆衰减，以及滚动窗口轨迹重新分类和类型转移统计。V072/V073 的规则驱动检查已确认状态记录、伙伴记忆和事件记忆能够跨日保留。

基于 Society0 的校园学生行为模拟项目。项目模拟学生的课程、求助、同伴互动、宿舍与社团接触、关系变化、长期状态和行为轨迹，并支持规则驱动、Qwen 和 DeepSeek 三种决策驱动。

> 当前结果是未校准的模型内部场景结果，不用于临床诊断、个体风险判断或现实政策因果结论。

## 当前版本

当前主线已完成 V070 定向外展修复、多驱动评估，以及 V073/V074 行为机制改进：

- 10 天模拟，40 名学生，2 名职员；
- 四个条件：`baseline`、`outreach`、`easier_access`、`targeted_outreach`；
- 三个种子：11、22、33；
- 每种驱动 12 组运行；
- 行为轨迹窗口为末尾 7 个完整日；
- 输出轨迹分型、孤立指标、行动选择、关系网络和干预指标；
- 当前已完成规则、Qwen、DeepSeek 三种驱动的 V070 矩阵；
- V070 修复了定向外展决策阶段缺少 `initial_group` 的问题；
- 模型响应解析支持附加文本，并对不可用行动进行安全回退。
- V073 将每天拆分为上午、下午、晚上三个时段，等待服务期间可上课、休息或进行同伴互动；
- V074 修复同日重复同伴行动，加入社交收益递减并校准压力恢复速度；
- V074 当前只完成规则驱动验证，尚未开展付费模型矩阵。

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

API key 可在 Web 页面的密码输入框中填写，仅用于本次提交；也可在启动 Web 服务前设置环境变量。不要写入配置文件、README 或 Git。终端设置示例：

```bash
read -r -s -p "请输入 Qwen API key: " DASHSCOPE_API_KEY; echo
export DASHSCOPE_API_KEY
read -r -s -p "请输入 DeepSeek API key: " DEEPSEEK_API_KEY; echo
export DEEPSEEK_API_KEY
```

Qwen 使用 `DASHSCOPE_API_KEY`，DeepSeek 使用 `DEEPSEEK_API_KEY`。

## 单次运行

### 本地 Web 实验工作台

安装项目后运行：

```bash
python -m campus_sim.cli web --output runs/workbench --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。页面可组合政策条件、随机种子、学生人数（2–100）、辅导员人数（1–10）、运行天数（1–30）、每日时段、服务名额与开放间隔、求助门槛、外展日和决策驱动。每日服务名额最多为辅导员人数的三倍。每次提交的完整配置保存在 `runs/workbench/configs/`，运行结果保存在同名目录；可在页面查看汇总指标和压力曲线。主体行动轨迹按天展示，每天依次按序号排列辅导员、教师、学生；每位主体每天一行，行动按事件顺序连接，并以有序列表显示每个行动的状态和详情。可按主体、模拟日和行动筛选；心理委员在学生列表中标注。

普通 Linux 用户应使用 `8765` 这样的 1024 以上端口；`876` 属于需要额外权限的低端口。已有 conda 环境时，直接在该环境中安装和运行项目，无需另建 `.venv`。

新运行按中国时间命名为 `政策-驱动-seed种子-YYYYMMDDHHMM`，例如规则驱动 `baseline-rule-seed11-202610201103`，LLM 驱动 `baseline-llm-deepseek-deepseek-chat-seed11-202610201103`；同一分钟内的重复运行追加序号。对照子运行也在政策后标记驱动与模型。旧实验保持原名称。已有运行按启动时间从近到远排列，旧运行按结果文件时间排列。完成并列入表格的运行不再重复显示状态提示。“删除记录”和“清空显示”把名称写入工作台的 `.hidden-runs.json`，此后不再显示，也没有页面恢复入口；实验输出和日志文件仍保留。

LLM 驱动可使用启动 Web 服务前设置的环境变量，也可在页面密码输入框中填写本次运行的 API key。在线密钥只通过当前请求传给运行进程，不写入运行配置和结果；提交成功后输入框会清空。LLM 模式下，学生行动和教师学业关怀均可由模型决策；教师只看到本班前一日缺课且连续缺课至少两次的候选名单，可以选择一名学生并采取简短学业关怀或学习计划支持，也可以暂不关怀。程序会核验选择并限制每天一次，状态变化是未校准的模拟参数。辅导员在 LLM 模式下可以从已获服务且符合条件的学生中选择跟进对象，以及从等待较久或有可观察连续缺课记录的服务对象中选择是否提供专业咨询服务信息；模型不能修改队列、名额或执行临床服务。心理委员专项职责仍由规则驱动。模型调用预算至少等于学生人数 × 天数 × 每日时段数，加上（教师人数 + 辅导员人数）×（天数 − 1）；同伴对话可能消耗额外调用。对照实验可选择 1–4 种政策和 1–10 个不重复随机种子（最多 40 次运行），各运行共用页面上的人数、天数、服务和决策参数；页面分别展示新旧批次的实际组合。运行中的单次实验和对照批次每 5 秒更新进度条，百分比由 Society0 已完成的模拟日计算；任务完成后转入结果列表。Web 服务仅监听本机回环地址。

### 实验分析 Agent：购买并接入模型 API

分析 Agent 在实验结束后另行调用模型；规则驱动实验也可以使用它。建议先用少量已完成运行试用，确认模型返回的报告格式和费用。API 通常按 Token 用量计费，账户购买的是调用额度，并非需要下载的模型文件。

分析模型默认选用 `qwen3.5-plus-2026-02-15`。这是固定快照，便于对比不同时间生成的报告；分析请求关闭该模型的思考模式，以减少额外 Token 消耗并给 JSON 报告保留输出空间。它只影响结果分析，不改变实验运行时的决策模型。请确认百炼控制台中该模型的免费额度适用于所用地域及业务空间。免费额度列表里的“未开启”指“免费额度用完即停”开关尚未开启，并非模型不可调用；如需确保免费额度耗尽后不继续计费，可在控制台为该模型开启此开关。

1. **选择提供商并取得密钥。** 推荐从已有接口的 Qwen 开始：注册阿里云账号，在[百炼控制台](https://bailian.console.aliyun.com/)开通服务，在[API Key 页面](https://help.aliyun.com/zh/model-studio/get-api-key/)创建密钥。百炼默认按量计费；免费额度或充值要求、模型价格以[官方计费页面](https://help.aliyun.com/zh/model-studio/model-pricing)和控制台为准。也可使用 [DeepSeek 开放平台](https://platform.deepseek.com/)创建 API Key，按其[余额与充值说明](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes/)操作。DeepSeek 模型 ID 可能更新，使用其[当前官方文档](https://api-docs.deepseek.com/zh-cn/)列出的 ID，并在页面“分析模型”框中手动填写。
2. **接入 Linux 工作台。** 在浏览器的“实验分析 Agent 设置”选择提供商和模型，将密钥输入独立的密码框。密钥只用于此次报告请求，提交后清空，不保存在实验配置、状态文件或报告中。也可在启动 Web 服务的同一终端执行 `read -r -s -p 'Qwen API Key: ' DASHSCOPE_API_KEY; echo; export DASHSCOPE_API_KEY`，DeepSeek 则使用 `DEEPSEEK_API_KEY`；设置后再启动 Web 服务。不要把密钥写进仓库或 shell 历史。
3. **生成报告。** 在已完成的“选中运行”点击“生成分析报告”；或在已完成的批次旁点击“分析政策对照”。页面显示分析状态、主要发现、证据编号、局限和待验证建议。报告以 JSON 独立保存到运行或批次目录的 `analysis-reports/`，包含分析模型、提示词版本、调用用量与完整证据；“查看最近分析”可重新打开批次报告。重复生成会产生新的文件及新的 API 用量。

分析前程序核对运行完成、逐日记录、指标分母与轨迹人数；批次还要求每个政策 × 种子结果完整、人数/天数/驱动/版本等可比。程序先计算证据，LLM 仅写解释。自动复核检查模型引用的证据编号以及文字中直接写出的数字；缺少证据或包含未经程序附注数字的段落标为“假设”。这项检查不能保证语义正确，报告仍需人工审阅。`mood`、`stress` 是未校准的模型内部变量，校园建议仅用于设计下一轮模拟实验。

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

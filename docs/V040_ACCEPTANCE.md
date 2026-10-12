# 0.4.0 LLM 接入验收进度

## 已验证

- Linux 分支 `feature/platform-v020` 在提交 `96c4c72`：`python -m pytest -q` 为 19 passed，Ruff 为 All checks passed，`git diff --check` 无输出。测试中的五个提供商响应均由本地替身模拟，不产生付费调用。
- DeepSeek `deepseek-chat`：`runs/v040-deepseek-smoke` 使用 2 名合成学生运行 1 天，完成 1 步，0 个诊断错误；成功记录 2 次模型决策，两次行动均为 `attend`。事件详情包含提供商、模型、动作和用量字段，人工检查未发现密钥字段。
- Qwen `qwen-plus`：`runs/v040-qwen-smoke` 使用同一份 2 名学生、1 天配置，完成 1 步，0 个诊断错误；成功记录 2 次模型决策，两次行动均为 `request`，留下 2 个待处理请求。事件详情人工检查未发现密钥字段。
- Web 工作台 `runs/v040-workbench` 的 `/api/models` 显示 DeepSeek 与 Qwen 已配置，OpenAI、Gemini、Anthropic 未配置。通过 `/api/runs` 启动 `baseline-seed11-5c308a86`：Qwen `qwen-plus`、40 名合成学生、1 天、最多 40 次调用；任务状态 `complete`、退出码 0，汇总中 `driver=llm`、`network_calls=40`、`paid_model_calls=40`。
- 以上真实 API 运行的场景提交均为 `96c4c72ffebf3be7790719e944cdb05f15b74ce1`，Society0 固定源码为 `6ba4a6284a8830491cf326fe9840c48c96c742fb`。
- 40 名学生的 Qwen 基线实验误以 30 天配置和 200 次调用上限启动，完成 3 天、120 条已记录决策后，在第 4 天因 TLS 握手超时失败。随后无密钥的 `curl` 连接测试返回 HTTP 400，连接约 0.010 秒、TLS 约 0.106 秒，说明接口当时可达；这不足以证明超时原因。该失败运行保留在 `runs/v040-qwen-baseline-5d`，不作为完整条件比较结果。

## 解释与待验收

两个提供商的小规模运行证明了真实 API 请求、学生行动执行和基本事件记录链路。事件中的调用数只统计完整步骤内成功记录的模型决策；失败请求可能已由提供商计费，但不会出现在该数字中。测试只检查了事件字段名，尚无独立流量或秘密扫描审计。

Web 运行列表显示模型名称和 `model_decision` 筛选已补充，Linux 上 20 个测试及 Ruff 通过；接口返回 40 条模型决策，浏览器截图显示筛选结果。OpenAI、Gemini、Anthropic 尚无真实账户验收。多天完整运行和 3×3 LLM 实验尚未执行，因此目前不能判断第 30 天求助率与支持率是否继续饱和。任何后续比较仍只解释为未经校准的模型内情景结果，不能用于真实学生的个体风险判断。

# V070 定向外展修复与评估

V069 诊断发现：外展准备阶段能按 `initial_group` 触达低联结学生，但学生决策读取接口没有返回 `initial_group`，导致定向外展在决策阶段无法激活。V070 将该字段加入学生可见状态，并保留 V069 的四条件、三种子、十天比较协议。V069 的结果不覆盖，作为修复前记录。

Linux 上先运行测试和静态检查，再分别运行规则、Qwen 或 DeepSeek 的 12 组矩阵：

```bash
python -m campus_sim.cli matrix-run-v070 --root runs --driver rule
python -m campus_sim.cli matrix-report-v070 --root runs --driver rule
```

模型驱动时将 `rule` 替换为 `qwen` 或 `deepseek`，并确保对应 API key 已设置。报告输出为 `runs/v070-matrix-{driver}-report.json`。

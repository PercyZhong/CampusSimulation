# 0.3.0 Linux 技术验收记录

## 环境与版本

- Linux 仓库：`/home/zpj/CampusSimulation`；Conda 环境 `campus-sim-py312`，Python 3.12.14。
- 场景提交：`cb0bbda281c258eb5ca3096d1c6b1a16b3bd4ea4`；Society0 固定源码：`6ba4a6284a8830491cf326fe9840c48c96c742fb`。
- 本记录根据 Linux 终端输出和浏览器截图填写。运行目录位于被 Git 忽略的 `runs/`，与 0.2.0 工件分开保存。

## 已执行的检查

| 检查 | 结果 |
| --- | --- |
| 测试与静态检查 | `python -m pytest -q`：12 passed in 1.37s；`python -m ruff check src tests`：All checks passed；`git diff --check` 无输出。 |
| 两天冒烟 | `runs/v030-smoke-2d`：完成 2 步，0 个诊断错误；40 名合成学生、2 名辅导员，4 条已完成的值班事件。 |
| 3×3 实验 | `runs/v030-experiment` 生成 9 个独立运行和 `comparison.json`；每个运行完成 30 天。 |
| 值班与容量 | 每个运行有 60 条已完成的值班事件，即每天 2 条。逐日核对 `available_slots = min(config.capacity, 当日值班槽位之和)` 与 `0 ≤ served_today ≤ available_slots ≤ config.capacity`，9 个运行全部通过；最高单日服务均为 3/3。最初检查误将 `available_slots` 与未截断的槽位之和直接比较，在 baseline/seed 11 第 1 天失败；修正为配置容量截断后通过。 |
| 恢复 | `runs/v030-resumed-baseline` 从 baseline/seed 11 第 15 个完整步骤恢复至第 30 步，0 个诊断错误；`verify-resume` 返回 `{"equivalent": true, "steps": 30}`。 |
| Web 行动轨迹 | `runs/v030-workbench` 中的两天运行 `baseline-seed11-ae821f4a` 状态为 `complete`。API 按第 1 天和第 2 天筛选 `staff_shift`，每天各返回 `c000`、`c001` 两条 `completed` 事件，每条登记 3 个槽位。浏览器截图确认第 2 天筛选显示 2 条匹配事件。Web 服务随后用 Ctrl+C 停止；工作台状态文件检查为 1 个任务已完成。 |

## 解释与后续

两名辅导员每天各登记 3 个槽位，但配置每日容量为 3，因此实际可用槽位为 3。值班主体只登记资源，不读取学生私有状态。三个条件的服务容量相同。

0.3.0 结果仍是未经经验校准的模型内情景结果。服务规则、值班数值、压力及联结参数和离线语言模板尚待校园专业人员审阅。离线模板不是 LLM；`network_calls=0` 是规则后端的设计声明，未做独立流量审计。不能用于真实学生的个体风险判断、诊断或政策效果预测。

本记录尚未包含 0.3.0 工件归档及跨主机哈希核对；完成后补记。

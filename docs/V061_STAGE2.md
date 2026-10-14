# V061 阶段二：工作人员跟进组件

本阶段增加可选的 `follow_up` 政策组件。组件包含 `enabled` 和 `days_after_support`；开启后，学生完成支持服务若干天后收到一次 `staff_follow_up` 事件。跟进会写入学生的 `recent_events`，并按固定规则小幅降低压力、提高情绪。事件具有一次性标记，不会重复触发。

当前三种正式政策配置将该组件关闭，因此不会改变既有实验结果。集成测试使用独立配置验证跟进触发时机、一次性约束和状态更新。本阶段没有调用 LLM，跟进规则仍属于未经校准的合成假设。

## 阶段验收

- Linux 提交 `615152b`：`pytest` 25 项通过，`ruff check src tests` 与 `git diff --check` 通过。
- 离线冒烟实验：以 `configs/baseline.json` 为基础，将 `end` 设为 6，并设置 `policy_components.follow_up` 为 `{"enabled": true, "days_after_support": 2}`。运行配置保存在被 Git 忽略的 `runs/v061-follow-up-smoke-config.json`，输出目录为 `runs/v061-follow-up-smoke-6d`。
- 运行 ID：`db779844a3b64508a00dc01dff89f2b8`；配置 SHA256：`2f4282f3a870c44295a667d722625f1a527a3ed5db12c97bb8e7a71086def76f`。完成 6 天，诊断错误 0，网络及付费模型调用均为 0。
- 40 名学生中 12 名获得支持，8 条 `staff_follow_up` 事件。逐条核对导出事件后，8 人均仅收到一次跟进，且事件日期恰好比支持日期晚 2 天：支持日 2、3、4 分别对应跟进日 4、5、6。其余已支持学生在第 6 天结束时尚未达到跟进日期。

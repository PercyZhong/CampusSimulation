# V069 行为轨迹分型与孤立指标

评估窗口为最后 7 个完整日。只有学生在窗口内每天都有 `student_state` 快照时才分型；不足 7 天的运行及快照缺失者标记 `insufficient_observation`。这避免把短期观察误判为持续行为。

每位学生获得一个互斥的 `trajectory_type`，按下列优先顺序判定：

1. `persistent_absence`：窗口内至少 3 天记录 `course_absence`。
2. `repeated_help_attempts`：至少 2 天选择 `request`；LLM 使用决策事件，规则驱动使用行动尝试事件。
3. `no_social_contact`：没有记录到与另一名学生的社会接触。
4. `mixed_or_connected`：其余完整观察轨迹。

另有独立的 `isolation_status`：`contact_observed`、`no_contact_despite_opportunity`、`no_observed_opportunity` 或 `insufficient_observation`。社会接触只来自完成的双方对话、实际同课接触、学习小组、宿舍或社团共同在场。课程到课或缺课，以及收到或发出同伴邀请，构成可观察机会。潜在关系边和未发生的场所访问不计作接触或机会；`no_observed_opportunity` 不等于现实中没有社交机会。

`summary.json.trajectory_profiles` 给出窗口、有效人数和分型计数。`trajectory_profiles.jsonl` 逐学生列出缺课、求助尝试、机会与实际接触天数。旧版运行没有每日状态快照时会被标记为观察不足。此指标是模型内部行为描述，不是临床或现实中的孤立诊断。

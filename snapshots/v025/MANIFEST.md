# v025 · 新增 06「认知拓展」：业务解剖 + 决策复盘

- 版本：v025
- 时间：2026-11-03（北京时间）
- 类型：结构升级

## 背景

现有五条线都在训练「组织内部 + HR 框架内」的判断。用户希望补上两类刷新认知的维度：

1. **业务解剖**：向外看，解决「HR 懂人但不懂生意」。
2. **决策复盘**：向后看，校准自己的判断，而不是只学新框架。

## 排期

| 时间 | 模式 | 内容 |
| --- | --- | --- |
| 周二 | `business_anatomy` | 业务解剖；蓝沃 AI / 智能装备事业线按 ISO 周奇偶轮换 |
| 周六 | `decision_review` | 决策复盘；复盘本周一个真实判断 |
| 其余日期 | `null` | 不出现，不增加日常负担 |

## 变更

| 文件 | 变更 |
| --- | --- |
| `.github/prompt/system.md` | 新增模块 6「认知拓展」及两种模式的字段/边界；JSON schema 新增 `cognition`；字数上限 2000（有认知卡时） |
| `scripts/generate.py` | 新增 `cognition_plan()`；prompt 注入当日模式/BU/写法；校验 mode、bu、facts、questions、deliverable；非认知日强制 null；新增 `render_cognition` 全宽卡片与事实列表；摘要/focus 同步 |
| `scripts/notify.py` | 推送摘要新增「认知拓展」块 |
| `templates/report.html` | 青色系 `--cognition`、卡片/图标/badge、`fact-list` 样式 |
| `README.md` | 模块表新增 06 |
| `docs/index.html` | chips、lede、meta description 同步 |

## 字段

业务解剖：`framework`、3 条 `facts`、`insight`、`implication`（对人才/编制的含义）、`blind_spot`、`questions`、`action`；`deliverable` 空。

决策复盘：`framework`、2–3 条 `facts`（示例须标注「示例」，真实内容「待你补充」）、`insight`、`implication`（下次调整）、`blind_spot`、`questions`、`action`；`deliverable` 必填（本周决策复盘卡）。

## 验证

- `py_compile` 通过。
- 轮转：2026-11-03 周二 → 业务解剖 / 智能装备事业线；11-07 周六 → 决策复盘；11-02 周一 → null。
- 校验：缺失 cognition、决策复盘缺交付物、非认知日出现 cognition 均被拒绝；两种完整样例通过。
- 渲染：5 卡、`card-cognition`、`fact-list`、无占位符。
- 摘要与推送均包含「认知拓展」。
- prompt：认知日指定 mode/BU 与 2000 字上限；非认知日强制 null。

## 回滚

恢复 v024 的 `.github/prompt/system.md`、`scripts/generate.py`、`scripts/notify.py`、`templates/report.html`、`README.md`、`docs/index.html`；删除 `cognition` 数据即可，无迁移。

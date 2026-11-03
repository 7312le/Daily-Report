# v023 · 日报升级为 HR 一号位系统

- 版本：v023
- 时间：2026-11-03 02:01（北京时间）
- 类型：结构升级

## 背景

原日报把终点定义成「HR 专家 + Agent 构建者」：L5 是写 SOP / 做培训，A5 是做原型，只有 M5 指向高管。训练出的是「很会写、很会搭工具的 HR 专家」，不是 HR 一号位。

本轮按方案 A 改造：改终点、补两条能力轴（影响力、人才发展），并注入公司情境与年度节奏。

## 输入

- 公司：上海精智实业股份有限公司
- 两个事业部：蓝沃 AI（工业 AI）、智能装备事业线（非标自动化）
- 行业信息推送只围绕工业 AI 与非标自动化

## 变更

| 文件 | 变更 |
| --- | --- |
| `data/profile.md` | 新增组织画像：公司、双事业部、行业特征、内容约束；未知字段留「待补充」 |
| `.github/prompt/system.md` | 终局改为 HR 一号位；01 线扩为 HRBP / COE / OD / TD；M5 拆 M5a / M5b；新增 04 HRD 实战；Agent 降至每周 3 次；隔周高管英语；字数 1500→1800 |
| `scripts/generate.py` | 按星期轮转 Agent、HRD、英语；注入年度 HR 日历与组织画像；校验当日轮转；新增 hrd 渲染与 card-wide 布局 |
| `templates/report.html` | HRD 卡片配色、深度交付物框、年度节奏行 |
| `scripts/notify.py` | 推送摘要适配新模块与英语模式 |
| `README.md` | 同步新结构与行业范围 |
| `docs/index.html` | 首页文案、chips、meta description 同步 |

## 模块频率

| 模块 | 频率 |
| --- | --- |
| 01 组织与人才 | 每日，HRBP / COE / OD / TD 轮转 |
| 02 Agent | 周一 / 三 / 五 + 每月 1 日，其余为 null |
| 03 经营与影响 | 每日，M1–M4 / M5a / M5b |
| 04 HRD 实战 | 每日；周六 deep，五个 track 轮转 |
| 05 英语 | 每日；隔周周三高管英语 |

## 验证

- `py_compile` 通过。
- 轮转测试（2026-11-02 至 11-09）：Agent 一 / 三 / 五为 True，周六 deep，隔周周三为高管英语。
- 正例：agent=null（4 卡）、agent 存在（5 卡）、deep（交付物必填）均通过校验与渲染。
- 反例：非生成日出现 agent、deep 缺交付物、track 不符、M6 均被拒绝。
- 渲染产物无未替换占位符；推送摘要包含全部新模块。

## 回滚

恢复 v022 的 `.github/prompt/system.md`、`scripts/generate.py`、`scripts/notify.py`、`templates/report.html`、`README.md`、`docs/index.html`，并删除 `data/profile.md`。

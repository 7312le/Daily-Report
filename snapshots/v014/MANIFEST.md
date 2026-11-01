# 版本快照 v014

- 时间：2026-11-01
- 类型：日报内容体系全面升级（四维度 → 三主线 + 英语）
- 基线：v013

## 背景

用户要求把日报 Agent 从「技能栈 / 财富自由 / 认知 / 英语」四维度，升级为「AI 原生 HR 高管」成长系统：

- 终极目标：成为国内第一批 AI 原生 HR 高管（组织诊断 + Agent 重构 HR 工作流 + 经营视角）。
- 三条主线：HRBP & COE（L1–L5）、Agent 体系化（A1–A5）、公司经营管理（M1–M5）。
- 每日四个模块，每个知识点必须带「今晚动作」；周六输出任务、每月 1 日 Agent 里程碑、季度首周影响力目标。
- 英语改为金山词霸词汇 + 高频短语 + AI Talk + 每日 1 个真实可打开的短视频。

## 相对 v013 的变更

| 文件 | 变更 |
| --- | --- |
| `.github/prompt/system.md` | 完全重写：角色定义、三条主线阶段表、四模块字段规则、英语视频推荐规则、全新 JSON Schema（`stage` / `hrbp_coe` / `agent` / `management` / `english` / `preview`）、约束与禁止事项、输出前自检 |
| `scripts/generate.py` | 新的顶层字段与校验；`stage` 容错解析（L1–L5 / A1–A5 / M1–M5）；`questions` 数组规范化；`output_task` / `milestone` / `influence_goal` 可选字段（「无」等占位词自动清空）；`video` 对象校验；新增四个卡片渲染器与视频链接卡；`build_user_prompt` 增加周六 / 每月 1 日 / 季度首周规则；`load_recent_topics` 兼容旧四维度 JSON；摘要与占位符改为 `STAGES` / `FOCUS` / `PREVIEW` |
| `templates/report.html` | 重写版式：深色 Hero + 阶段条，四张卡片（HRBP & COE 全宽、Agent 与经营管理双列、英语全宽），输出任务 / 里程碑 / 影响力提示框，英语短视频链接卡（新窗口打开，`rel="noopener noreferrer"`），移动端单列 |
| `scripts/notify.py` | `build_markdown()` 改为新结构（阶段、四模块、视频链接），Server酱标题改为「AI 原生 HR 高管日报」；渠道逻辑（WeCom / Server酱、重试、截断）不变 |
| `docs/index.html` | 首页标题、描述、引导语、四个标签改为三主线 + 英语；页脚改为「Powered by DeepSeek API · Server酱推送」，列表标记区间不变 |

未变更但与本轮配套的文件：`.github/workflows/generate.yml`、`.github/workflows/pages.yml`、`requirements.txt`、`.gitignore`。

## 行为说明

- 生成链路不变：`generate.yml` → `generate.py`（DeepSeek Anthropic 兼容端点）→ HTML/JSON → 首页 → 提交 → 派发 `pages.yml` → Server酱推送。
- 旧版日报 HTML/JSON 原样保留；`load_recent_topics()` 会从旧 JSON 的 `skills` / `wealth` / `cognition` 中提取主题用于去重。
- 视频链接缺失时，英语卡只渲染词汇 / 短语 / AI Talk 三块，不留下空占位。

## 验证记录

- 待推送后手动触发 `generate.yml`，确认：生成成功、四卡片渲染、首页更新、Pages 部署、Server酱收到新标题推送。

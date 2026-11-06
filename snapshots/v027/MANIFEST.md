# v027 · 课程表对齐人社部 + CIPD，案例改用免费公开源

- 版本：v027
- 时间：2026-11-06（北京时间）
- 类型：课程体系升级

## 背景

用户确认采用建议：人社部《企业人力资源管理师》标准打底，CIPD Profession Map 补战略 / OD；案例先用免费公开源，之后再考虑中欧 / 清华订阅。

## 课程表

18 个领域、60 课：

| 部分 | 领域 | 课数 | 说明 |
| --- | --- | --- | --- |
| 人社部 | 人力资源规划、招聘与配置、培训与开发、绩效管理、薪酬福利管理、劳动关系管理 | 36 | 每模块 6 课，按四级→一级递进，课名带 `level` |
| CIPD 核心知识 | People practice、Culture and behaviour、Business acumen、Evidence-based practice、Technology and people、Change | 12 | 每域 2 课 |
| CIPD 专业领域 | OD 与组织设计、Workforce planning、People analytics、Talent management、Employee experience、Strategic reward | 12 | 每域 2 课 |

领域新增 `source` / `group` / `level`，日报显示「人社部四级」「CIPD 核心知识」等来源标签。

## 案例库

- 案例新增 `source_url` / `source_type`。
- 选择器优先：同领域 → 30 天内未用 → `source_type=免费公开源` 且有链接。
- 新增 8 个免费公开案例：SHRM H&M HR 转型、SHRM 招聘与保留情境、CIPD 招聘与人才规划案例集、CIPD HMRC 变革、北森上海家化 / 特变电工 / 中南高科 / 周大福。
- 原有 18 个案例保留；Netflix / Amazon / Google 补官方链接；未附单篇链接的标为「公开报道综合」，排序靠后。
- 日报与推送中的案例来源改为可点击链接。

## 变更

| 文件 | 变更 |
| --- | --- |
| `data/curriculum.json` | 重写为 18 领域 × 60 课；新增 source / group / level |
| `data/cases.json` | 重写为 26 条；新增 source_url / source_type；新增 8 条免费公开案例 |
| `scripts/generate.py` | 课程标签；案例优先级；来源链接渲染；进度注入 source/group/level |
| `scripts/notify.py` | 推送显示课程来源与案例链接 |
| `templates/report.html` | 来源链接样式 |
| `.github/prompt/system.md` | 说明课程来自人社部 / CIPD |
| `README.md` | 课程体系、案例来源与新增规则 |
| `docs/index.html` | lede 同步 |
| `data/curriculum_state.json` | 重置为新课程体系的进度 |

## 验证

- 离线 fixture：第 1 课 `mof-plan-l01`、人社部四级、案例 `cipd-resourcing-talent`（免费、有链接）、下一课 `mof-plan-l02`、7 天复习、渲染与推送通过。
- 真实生成 `reports/2026-11-06.json/html`：第 1/60 课《人力资源规划到底规划什么》，人社部四级，案例 CIPD 招聘与人才规划案例集，无 Agent / 落地日 / 复习。
- `py_compile`、JSON 校验、`git diff --check` 通过；推送 dry-run 含课程来源与案例链接。

## 回滚

恢复 v026 的 `data/curriculum.json`、`data/cases.json`、`data/curriculum_state.json`、`scripts/generate.py`、`scripts/notify.py`、`templates/report.html`、`.github/prompt/system.md`、`README.md`、`docs/index.html` 即可。

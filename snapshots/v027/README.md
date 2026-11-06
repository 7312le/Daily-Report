# Daily-Report

每日自动生成并推送的「每日 HR 系统课」，以及上海 / 江苏机会雷达。

服务对象：正在系统补 HR 基础、目标方向是 HRD / HR 一号位的学员。

## 两个定时任务

| 任务 | 时间（北京） | 说明 |
| --- | --- | --- |
| 日报 `generate.yml` | 每天 07:00 | 今日一课 + 案例 + 小测 + 英语；周三加 Agent 卡；周六加落地日 |
| 机会雷达 `radar.yml` | 每天 14:00 | 扫描上海 / 江苏官方公开页面，只推新增，归档到 `radar/` |

## 日报结构

| 模块 | 频率 | 说明 |
| --- | --- | --- |
| 01 今日一课 | 每日 | 按知识地图推进，一课只讲一个概念；含一句话定义、为什么重要、怎么用、常见误区、术语翻译 |
| 02 案例照镜子 | 每日 | 从案例库选一个公开案例，说明问题、做法、公开结果、能否迁移 |
| 03 小测一题 | 每日 | 一个情境题，3 个选项，附答案解析 |
| 04 Agent 小卡 | 周三 | 与当天课程独立，选一个能在 30 分钟内搭出原型的 HR 场景工具 |
| 05 英语 5 分钟 | 每日 | 词汇、句型、AI Talk；隔周周三切换为高管英语 |
| 06 周六落地日 | 周六 | 只给一个可选的落地问题，不要求提供公司信息 |

不再按年度 HR 日历滚动，也不强制把每天的内容挂到某一家公司或某个事业部上。

## 学习机制

- **课程表** `data/curriculum.json`：18 个领域 × 60 课。人社部《企业人力资源管理师》六大模块 36 课打底（按四级→一级递进），CIPD Profession Map 核心知识 12 课 + 专业领域 12 课补战略与 OD。按顺序学习。
- **学习状态** `data/curriculum_state.json`：记录每课的学习日期、7 天 / 30 天复习标记、案例使用日期。日报生成成功后才会推进进度；生成失败或推送失败不会消耗课程。
- **案例库** `data/cases.json`：优先使用带链接的免费公开源（SHRM、CIPD、北森、官方文化页等），按领域标签匹配，30 天内不重复；标注「公开报道综合」的条目暂未附单篇链接，选择时会排在后面。
- **间隔复习**：某课学完 7 天后，当天日报会多一条 3 分钟复习提示；30 天后再复习一次。
- **轮换规则**：60 课全部学完后，从最久没学的一课重新开始，不会中断。

想加课：在 `data/curriculum.json` 对应领域下追加一条 `{ "id": "...", "title": "...", "level": "..." }` 即可，`id` 不能重复。
想加案例：在 `data/cases.json` 追加一条，`tags` 填领域 id（如 `mof-recruiting`），建议同时填 `source_url` 和 `source_type: "免费公开源"`，字段参考现有条目。

## 机会雷达

- 源与关键词：`data/radar_sources.json`
- 去重库：`data/radar_seen.json`（按 URL 哈希去重，首次运行只建基线）
- 页面：`radar/YYYY-MM-DD.html` 与 `radar/index.html`
- 手动试跑：Actions → Opportunity Radar → Run workflow → 勾选 `dry_run`，只抓取打印，不写文件、不推送
- 约束：只访问 `.gov.cn` 公开页面，不登录、不绕过验证、不做代办代抢、不接返利

## 在 Mac 本地运行

仓库本身是跨平台的（CI 跑在 Ubuntu 上），Mac 上只需要准备一个 Python 环境。

**一键方式**：双击项目根目录的 `本地运行.command`，按菜单先选「安装 / 更新依赖」，之后可以生成日报、试跑机会雷达、预览推送。

**命令行方式**：

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env        # 填入 ANTHROPIC_API_KEY 等，见下
.venv/bin/python scripts/generate.py
```

`.env` 已在 `.gitignore` 里，不会进仓库。三个模型变量：

| 变量 | 说明 |
| --- | --- |
| `ANTHROPIC_API_KEY` | 必填，模型服务的 key |
| `ANTHROPIC_BASE_URL` | 可选，Anthropic 兼容端点；留空走官方 api.anthropic.com |
| `ANTHROPIC_MODEL` | 可选，默认 `claude-sonnet-5`；换服务商时一起改 |

## 推送

在仓库 Secrets 中配置任意一个即可：

- `SERVERCHAN_SENDKEY`：Server酱（微信）
- `WECOM_WEBHOOK`：企业微信群机器人

未配置时任务正常执行，仅跳过推送。推送摘要包含今日课名、一句话定义、案例判断、小测答案和英语句型。

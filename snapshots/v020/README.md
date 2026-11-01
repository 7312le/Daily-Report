# Daily-Report

每日自动生成并推送的「AI 原生 HR 高管」日报，以及上海 / 江苏机会雷达。

## 两个定时任务

| 任务 | 时间（北京） | 说明 |
| --- | --- | --- |
| 日报 `generate.yml` | 每天 07:00 | 三条主线的学习任务 + 英语精进，归档到 `reports/` |
| 机会雷达 `radar.yml` | 每天 14:00 | 扫描上海 / 江苏官方公开页面，只推新增，归档到 `radar/` |

## 机会雷达

- 源与关键词：`data/radar_sources.json`
- 去重库：`data/radar_seen.json`（按 URL 哈希去重，首次运行只建基线）
- 页面：`radar/YYYY-MM-DD.html` 与 `radar/index.html`
- 手动试跑：Actions → Opportunity Radar → Run workflow → 勾选 `dry_run`，只抓取打印，不写文件、不推送
- 约束：只访问 `.gov.cn` 公开页面，不登录、不绕过验证、不做代办代抢、不接返利

## 推送

在仓库 Secrets 中配置任意一个即可：

- `SERVERCHAN_SENDKEY`：Server酱（微信）
- `WECOM_WEBHOOK`：企业微信群机器人

未配置时任务正常执行，仅跳过推送。

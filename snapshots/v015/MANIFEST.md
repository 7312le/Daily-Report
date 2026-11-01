# 版本快照 v015

- 时间：2026-11-01
- 类型：品牌标题更换
- 基线：v014

## 背景

用户要求日报标题改为 **Shaun Daily Report**。

## 相对 v014 的变更

| 文件 | 变更 |
| --- | --- |
| `scripts/generate.py` | 页面 `<title>` 与 Hero 标题改为 `Shaun Daily Report · YYYY-MM-DD` |
| `templates/report.html` | 页脚品牌改为 `Shaun Daily Report` |
| `scripts/notify.py` | Server酱推送标题与 markdown 一级标题改为 `Shaun Daily Report · 日期`；argparse 描述同步 |
| `docs/index.html` | 首页 `<title>`、Hero H1、页脚品牌改为 `Shaun Daily Report` |

## 保持不变

- 日报内容结构、三主线阶段、推送渠道、DeepSeek 配置均不变。
- 历史日报文件不改动，仅在下次生成时使用新标题。

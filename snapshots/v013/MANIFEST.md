# 版本快照 v013

- 时间：2026-10-31
- 类型：新增推送渠道（Server酱 → 微信）
- 基线：v012

## 背景

用户不使用企业微信，希望日报提醒直接到个人微信或邮箱。选择 Server酱（微信服务号推送，免费额度 5 条/天，日报 1 条/天足够）。

## 相对 v012 的变更

| 文件 | 变更 |
| --- | --- |
| `scripts/notify.py` | 新增 `SERVERCHAN_SENDKEY` 渠道：`serverchan_endpoint()` 自动识别 Turbo（`https://sctapi.ftqq.com/{key}.send`）与 Server酱³（`https://{uid}.push.ft07.com/send/{key}.send`）；`send_serverchan()` 带 3 次重试；`main()` 支持两渠道并存，任一失败返回 1 |
| `.github/workflows/generate.yml` | job 级新增 `SERVERCHAN_SENDKEY`；通知步骤更名并改为 `WECOM_WEBHOOK != '' \|\| SERVERCHAN_SENDKEY != ''` 时执行 |

## 行为说明

- 两个渠道都配置 → 同时推送；都未配置 → 打印 `[skip]` 正常退出。
- Server酱正文复用 `build_markdown()`（含完整日报链接）。
- 错误日志经 `safe_error()` 处理后不包含 URL/SendKey。

## 验证记录

- 待用户配置 `SERVERCHAN_SENDKEY` 后重跑 `generate.yml`，确认 `[ok] Server酱通知已发送` 且微信收到消息。

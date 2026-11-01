# 版本快照 v010

- 时间：2026-10-31
- 类型：缺陷修复（企业微信 Webhook 缺少协议头）
- 基线：v009

## 背景

run 41183626043：日报生成、提交、Pages 部署全部成功，但通知步骤重试 3 次均失败：

```
[warn] 企业微信发送失败（1/3）：MissingSchema: Invalid URL '***': No scheme supplied.
[error] 企业微信通知发送失败
```

`MissingSchema` 说明 `WECOM_WEBHOOK` 的值不含 `http(s)://`（GitHub 日志把整个 URL 掩码为 `***`）。

## 相对 v009 的变更

| 文件 | 变更 |
| --- | --- |
| `scripts/notify.py` | 新增 `normalize_webhook()`：去除首尾空白与包裹引号；缺少协议头时自动补 `https://`；`main()` 使用该函数读取 `WECOM_WEBHOOK` |

## 验证记录

- 待重跑 `generate.yml`，确认日志出现 `[ok] 企业微信通知已发送`。

## 备注

- 若 Secret 中粘贴的是完整 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...`，本轮改动不影响原有行为。

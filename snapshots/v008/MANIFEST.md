# 版本快照 v008

- 时间：2026-10-31
- 类型：可观测性改进（鉴权错误输出 API 原文）
- 基线：v007

## 背景

Secret `ANTHROPIC_API_KEY` 更新后（07:59:10Z），run 41182787278 仍返回 401，但脚本只打印笼统的「ANTHROPIC_API_KEY 无效或已过期」，无法判断是 Key 格式错误、已吊销、还是账号问题。

## 相对 v007 的变更

| 文件 | 变更 |
| --- | --- |
| `scripts/generate.py` | `main()` 捕获 `authentication.AuthenticationError as exc`，输出 API 原始信息：`ANTHROPIC_API_KEY 无效或已过期：invalid x-api-key` 等 |

## 安全性

- API 的 401 响应体不含密钥本身；GitHub Actions 仍会对 Secret 做 `***` 掩码。

## 验证记录

- 需重新运行 `generate.yml` 查看新增的原始错误信息。

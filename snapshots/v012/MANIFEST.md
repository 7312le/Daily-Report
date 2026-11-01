# 版本快照 v012

- 时间：2026-10-31
- 类型：诊断增强（输出 Webhook 主机与路径，不含 key）
- 基线：v011

## 背景

v011 探测证明 GitHub runner 可正常访问 `qyapi.weixin.qq.com`（DNS 正常，HTTPS 200，0.17s 建立连接）。因此 notify 的超时只可能来自 Secret 中 Webhook 指向的**另一个主机**无法连通。

## 相对 v011 的变更

| 文件 | 变更 |
| --- | --- |
| `.github/workflows/hello-test.yml` | 新增 `Inspect webhook host` 步骤：解析 `WECOM_WEBHOOK`，只输出 scheme/host/port/path、key 长度与原始长度，不打印 key |

## 安全性

- 输出的 `host` 为公开域名；`key` 仅以长度形式出现，日志中仍受 GitHub Secret 掩码保护。

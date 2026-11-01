# 版本快照 v011

- 时间：2026-10-31
- 类型：诊断增强（企业微信连通性探测）
- 基线：v010

## 背景

v010 修复协议头后，run 41183812972 通知步骤仍失败：`ConnectTimeout ... port=443 ... connect timeout=15`（host 被 GitHub 掩码为 `***`）。需判断是 GitHub runner 无法访问企业微信，还是 Webhook 地址本身有误。

## 相对 v010 的变更

| 文件 | 变更 |
| --- | --- |
| `.github/workflows/hello-test.yml` | 新增 `Probe WeCom reachability` 步骤：对 `qyapi.weixin.qq.com` 做 DNS 解析与 HTTPS 连接探测，输出 http_code / connect / total 耗时 |

## 验证记录

- 待运行 Hello Test 查看探测结果。

## 备注

- 该步骤 `continue-on-error: true`，失败不影响冒烟测试结论。

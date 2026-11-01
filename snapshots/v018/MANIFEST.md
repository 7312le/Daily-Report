# v018 · 探测脚本加固（IPv4 回退 + 备用源）

- 版本：v018
- 时间：2026-11-01 19:51
- 类型：修复 / 增强（仅影响探测工具）

## 背景

v017 首次探测 14/16 成功：
- 江苏商务厅 `http://swt.jiangsu.gov.cn/` → `URLError: [Errno 101] Network is unreachable`（疑似域名优先解析 IPv6，海外 runner 无 IPv6 路由）。
- 江苏文旅厅 `http://wlt.jiangsu.gov.cn/` → `HTTP 521`（CDN 回源失败，改用 https 复测）。
- 上海一网通办返回 200 但 0KB，前端渲染，不适合直接抓列表。

## 本轮变更

| 文件 | 说明 |
| --- | --- |
| `scripts/probe_sources.py` | 请求依次尝试：证书校验 → 强制 IPv4 → 跳过证书校验 → 跳过校验 + IPv4；HTTPError 直接视为结果不再重试 |
| `data/radar_probe.json` | 江苏商务厅改 https；新增备用域名 `https://jsswt.jiangsu.gov.cn/`；江苏文旅厅改 https |

## 验证

- 推送后 `radar-probe.yml` 自动重跑，对比 16 + 1 个目标的二次结果。

## 回滚

恢复 v017 对应文件即可。

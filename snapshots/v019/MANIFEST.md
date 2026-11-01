# v019 · 探测清单补充：江苏商务厅政策栏目

- 版本：v019
- 时间：2026-11-01 19:56
- 类型：探测清单补充

## 背景

`swt.jiangsu.gov.cn` 与 `jsswt.jiangsu.gov.cn` 经 DNS 查询只有 AAAA 记录（如 `240e:6a0:20::ffff`），是 IPv6-only 站点；GitHub Actions runner 无 IPv6 路由，强制 IPv4 也无法访问。

## 本轮变更

| 文件 | 说明 |
| --- | --- |
| `data/radar_probe.json` | 新增江苏商务厅真实发布源 `doc.jiangsu.gov.cn` 的两个栏目：政策公告（col93272，消费品以旧换新等）、公告通知（col78712） |

## 说明

`doc.jiangsu.gov.cn` 与江苏文旅厅同走加速乐 CDN（IPv4 可用），但江苏文旅厅经该 CDN 返回 521，需实测确认商务栏目是否同样受限。

## 回滚

恢复 v018 的 `radar_probe.json` 即可。

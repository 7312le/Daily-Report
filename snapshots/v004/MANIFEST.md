# 版本快照 v004

- 时间：2026-10-27
- 类型：最终加固版
- 说明：在 v003 基础上增加 Pages 派发竞态保护与企业微信响应类型校验。

## 相对 v003 的变更

| 文件 | 变更 |
| --- | --- |
| `.github/workflows/generate.yml` | 派发 `pages.yml` 前 `sleep 10`，等待 GitHub 索引刚推送的提交 |
| `scripts/notify.py` | 校验企业微信响应必须是 JSON 对象，避免异常响应导致未捕获错误 |
| `dialog.md` | 追加用户原始需求全文与本阶段加固记录 |

## 验证记录

- Pyright：语法通过（仅提示本地未安装 `anthropic` / `requests`）。
- 两份 workflow YAML 解析通过。
- 前序 Pyodide 冒烟测试已覆盖：JSON 清洗、四卡片渲染、首页更新、企微消息构造与 UTF-8 截断。

## 回滚方式

将本目录内容复制回项目根目录，即可恢复到本版本。v001–v003 全部保留。

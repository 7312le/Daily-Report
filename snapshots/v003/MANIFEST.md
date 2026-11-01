# 版本快照 v003

- 时间：2026-10-27
- 类型：修正版（Pages 部署触发）
- 说明：在 v002 基础上修复 GITHUB_TOKEN 推送不触发下游 workflow 的问题。

## 相对 v002 的变更

| 文件 | 变更 |
| --- | --- |
| `.github/workflows/generate.yml` | 增加 `actions: write` 权限；提交推送后执行 `gh workflow run pages.yml --ref main` 显式派发部署 |

## 变更原因

GitHub Actions 使用仓库默认 `GITHUB_TOKEN` 产生的 `push` 事件不会创建新的 workflow run（防递归），
因此日报提交后 `pages.yml` 不会因 push 自动运行。`workflow_dispatch` 是该限制的例外，
故在生成工作流末尾显式派发一次 Pages 部署。

## 验证记录

- 两份 workflow YAML 重新解析通过。
- 步骤顺序：Checkout → Set up Python → Install dependencies → Generate → Notify → Commit and push → Trigger Pages deployment。
- 其余文件与 v002 一致（Pyodide 冒烟测试、html-validate 均已在 v002 阶段通过）。

## 回滚方式

将本目录内容复制回项目根目录，即可恢复到本版本。v001 / v002 保留不动。

# 版本快照 v006

- 时间：2026-10-28
- 类型：配置排障 + 通知可选化
- 基线：v004（v005 为交付检查点，无代码变更）

## 背景

GitHub Actions 左侧不显示任何工作流、Settings → Pages 无 Build and deployment 区域。

## 根因

远程默认分支 `origin/main` 上不存在 `.github/` 目录（`git ls-tree -r origin/main` 仅有 `README.md` 与 `日报/`）。含工作流的提交 `2de2cb0` 仍留在本地未推送。GitHub 只列出默认分支上已存在的 workflow 文件，因此 Actions 为空——与 YAML 语法无关。

## 相对 v004 的变更

| 文件 | 变更 |
| --- | --- |
| `.github/workflows/generate.yml` | 新增 job 级 `env: WECOM_WEBHOOK`；通知步骤加 `if: ${{ env.WECOM_WEBHOOK != '' }}`，未配置时整步跳过 |
| `scripts/notify.py` | 缺少 `WECOM_WEBHOOK` 时由 `return 1` 改为打印 `[skip]` 并 `return 0` |
| `.github/workflows/hello-test.yml` | 新增，纯 `workflow_dispatch` 冒烟测试，校验 runner 与关键文件 |

## 验证记录

- `js-yaml` 解析 `generate.yml` / `pages.yml` / `hello-test.yml`：全部通过。
- workflow 文件无 Tab 缩进。
- 推送后 Actions 实跑、Pages 是否可启用未在本轮验证（取决于仓库可见性与账号套餐）。

## 回滚方式

将 `snapshots/v004/` 内容复制回项目根目录即可恢复通知失败即报错的行为。

# 版本快照 v016

- 时间：2026-11-01
- 类型：英语短视频改为「视频池轮换 + 直接链接」
- 基线：v015

## 背景

用户反馈：短视频点进去是 B 站搜索页，而不是具体视频。要求每天只给一条可直接打开的视频链接。

## 方案

模型不再生成视频链接（无法验证真实性，容易给出搜索页或失效链接）。改为：

- 新增 `data/videos.json`：人工整理的 B 站英语学习视频池，全部使用 `https://www.bilibili.com/video/BV.../` 直接播放页，含标题、难度、跟读任务。
- `scripts/generate.py`：按日期序数在视频池中轮换，生成后覆盖 `english.video`；池文件缺失或格式错误时跳过视频，不影响日报。
- `.github/prompt/system.md`：要求模型将 `english.video` 各字段留空，禁止输出搜索页或编造链接。

## 相对 v015 的变更

| 文件 | 变更 |
| --- | --- |
| `data/videos.json` | 新增，14 条 B 站英语学习视频（职场口语、会议、面试、听力跟读） |
| `scripts/generate.py` | 新增 `load_video_pool()` / `pick_daily_video()`，生成后注入当日视频；Prompt 规则同步 |
| `.github/prompt/system.md` | 第四节改为「视频由系统注入」，JSON 示例 video 字段留空，自检项同步 |

## 维护方式

新增视频时，先人工打开确认可播放，再向 `data/videos.json` 追加 `platform` / `title` / `url` / `level` / `task`。

## 验证记录

- 待重跑 `generate.yml`：确认 `english.video.url` 为 `www.bilibili.com/video/BV...`，不含 `search.bilibili.com`。

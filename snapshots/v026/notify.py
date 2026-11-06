#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当日「每日 HR 系统课」摘要推送到企业微信群机器人或 Server酱（微信）。

- WECOM_WEBHOOK：企业微信群机器人 Webhook 地址（GitHub Secrets，可选）
- SERVERCHAN_SENDKEY：Server酱 SendKey（GitHub Secrets，可选）
  自动识别 Turbo（SCT/数字开头）与 Server酱³（sctp<uid>t...）两套地址
- 两个渠道都配置时同时推送；都未配置时打印 [skip] 并正常退出
- PAGES_BASE_URL：GitHub Pages 站点根地址（GitHub Variables，可选）
  未配置时根据 GITHUB_REPOSITORY 自动推导为 https://owner.github.io/repo
- 默认取 reports/ 下最新一期 JSON，也可用 --date YYYY-MM-DD 指定
- 正文按 4000 字节截断并保留链接，兼容企业微信 4096 字节上限
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"

WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 企业微信 markdown 消息内容上限为 4096 字节，预留少量余量
MAX_CONTENT_BYTES = 4000
RETRY_TIMES = 3
URL_RE = re.compile(r"https?://\S+")

# Server酱两套接口：Turbo 用 sctapi，Server酱³ 用数字子域
SERVERCHAN_TURBO_URL = "https://sctapi.ftqq.com/{key}.send"
SERVERCHAN_SC3_URL = "https://{uid}.push.ft07.com/send/{key}.send"
SERVERCHAN_SC3_RE = re.compile(r"^sctp(\d+)t")


class NotifyError(RuntimeError):
    """通知流程中的可预期错误。"""


def safe_error(exc: Exception) -> str:
    """去掉错误信息中的 URL，避免 Webhook 密钥泄漏到日志。"""
    return URL_RE.sub("<url>", f"{type(exc).__name__}: {exc}")


def clip(value: object, limit: int = 80) -> str:
    """压成单行并截断，适配企业微信 markdown。"""
    text = " ".join(str(value if value is not None else "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def truncate_utf8(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节安全截断，避免中文被截成半个字符。"""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text

    suffix = "\n\n…（内容过长已截断，点击链接查看完整日报）"
    keep = max(0, max_bytes - len(suffix.encode("utf-8")))
    truncated = raw[:keep].decode("utf-8", errors="ignore").rstrip()
    return truncated + suffix


def weekday_cn(date_str: str) -> str:
    try:
        return WEEKDAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except ValueError:
        return ""


def resolve_pages_base() -> str:
    """优先使用 PAGES_BASE_URL，其次从 GITHUB_REPOSITORY 推导。"""
    base = os.environ.get("PAGES_BASE_URL", "").strip()
    if base:
        return base.rstrip("/")

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in repo:
        owner, name = repo.split("/", 1)
        owner, name = owner.strip(), name.strip()
        if owner and name:
            # 用户主页仓库（owner.github.io）没有子路径
            if name.lower() == f"{owner.lower()}.github.io":
                return f"https://{owner}.github.io"
            return f"https://{owner}.github.io/{name}".rstrip("/")

    raise NotifyError(
        "缺少 PAGES_BASE_URL，且无法从 GITHUB_REPOSITORY 推导 Pages 地址"
    )


def discover_report_path(date_arg: str | None) -> Path:
    """定位要发送的日报 JSON。"""
    if date_arg:
        if not DATE_RE.match(date_arg):
            raise NotifyError(f"--date 格式错误：{date_arg}（应为 YYYY-MM-DD）")
        path = REPORTS_DIR / f"{date_arg}.json"
        if not path.exists():
            raise NotifyError(f"未找到指定日期的日报：{path}")
        return path

    candidates = sorted(REPORTS_DIR.glob("*.json"))
    if not candidates:
        raise NotifyError(f"{REPORTS_DIR} 下没有可用的日报 JSON")
    return candidates[-1]


def load_report(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NotifyError(f"读取日报失败：{path.name}（{safe_error(exc)}）") from exc

    if not isinstance(data, dict):
        raise NotifyError(f"日报格式错误：{path.name} 根节点不是对象")
    if not DATE_RE.match(str(data.get("date", "")).strip()):
        raise NotifyError(f"日报缺少合法的 date 字段：{path.name}")
    return data


def _block(data: dict, key: str) -> dict:
    block = data.get(key)
    return block if isinstance(block, dict) else {}


def build_markdown(data: dict, report_url: str) -> str:
    """构造企业微信 / Server酱 支持的 markdown 内容。"""
    date_str = str(data.get("date", "")).strip()
    weekday = weekday_cn(date_str)
    heading_date = f"{date_str} {weekday}".strip()

    lines: list[str] = [f"# 📘 每日 HR 系统课 · {heading_date}", ""]

    progress = _block(data, "progress")
    if progress:
        domain = clip(progress.get("domain"), 20)
        domain_index = clip(progress.get("domain_index"), 4)
        domain_total = clip(progress.get("domain_total"), 4)
        lesson_no = clip(progress.get("lesson_no"), 4)
        total = clip(progress.get("total"), 4)
        lines.extend(
            [
                f"> 领域 {domain_index}/{domain_total} · {domain}"
                f" ｜ 第 {lesson_no}/{total} 课",
                "",
            ]
        )

    review = _block(data, "review")
    if review:
        lines.append(
            f"**🔁 今日复习｜第 {clip(review.get('lesson_no'), 4)} 课"
            f"《{clip(review.get('lesson_title'), 30)}》**"
        )
        if review.get("prompt"):
            lines.append(f"回忆：{clip(review.get('prompt'), 70)}")
        lines.append("")

    # --- 今日一课 ---
    lesson = _block(data, "lesson")
    if lesson:
        title = clip(lesson.get("title"), 40)
        no = clip(lesson.get("lesson_no"), 4)
        total = clip((_block(data, "progress")).get("total"), 4)
        label = f"第 {no}/{total} 课｜{title}" if total else title
        lines.append(f"**📚 今日一课｜{label}**")
        if lesson.get("one_liner"):
            lines.append(f"一句话：{clip(lesson.get('one_liner'), 60)}")
        if lesson.get("why"):
            lines.append(f"为什么：{clip(lesson.get('why'), 70)}")
        steps = lesson.get("how_to_use")
        if isinstance(steps, list) and steps:
            lines.append(f"怎么用：{clip(' → '.join(str(s) for s in steps), 90)}")
        misconceptions = lesson.get("misconceptions")
        if isinstance(misconceptions, list) and misconceptions:
            lines.append(
                f"误区：{clip('；'.join(str(m) for m in misconceptions[:2]), 80)}"
            )
        glossary = lesson.get("glossary")
        if isinstance(glossary, list) and glossary:
            terms = "；".join(
                f"{clip(item.get('term'), 12)}={clip(item.get('plain'), 28)}"
                for item in glossary
                if isinstance(item, dict)
            )
            if terms:
                lines.append(f"术语：{clip(terms, 90)}")
        if lesson.get("action"):
            lines.append(f"今晚：{clip(lesson.get('action'), 70)}")
        lines.append("")

    # --- 案例照镜子 ---
    case = _block(data, "case")
    if case:
        lines.append(f"**🪞 案例照镜子｜{clip(case.get('title'), 36)}**")
        if case.get("problem"):
            lines.append(f"问题：{clip(case.get('problem'), 60)}")
        if case.get("practice"):
            lines.append(f"做法：{clip(case.get('practice'), 70)}")
        if case.get("transfer"):
            lines.append(f"迁移：{clip(case.get('transfer'), 70)}")
        if case.get("judgement"):
            lines.append(f"判断：{clip(case.get('judgement'), 60)}")
        lines.append("")

    # --- 小测一题 ---
    quiz = _block(data, "quiz")
    if quiz:
        lines.append("**✅ 小测一题**")
        if quiz.get("question"):
            lines.append(f"情境：{clip(quiz.get('question'), 70)}")
        options = quiz.get("options")
        if isinstance(options, list) and options:
            letters = "ABC"
            option_text = " / ".join(
                f"{letters[index]}. {clip(option, 30)}"
                for index, option in enumerate(options)
                if index < len(letters)
            )
            lines.append(f"选项：{option_text}")
        if quiz.get("answer"):
            lines.append(f"答案：{clip(quiz.get('answer'), 4)}")
        if quiz.get("explanation"):
            lines.append(f"解析：{clip(quiz.get('explanation'), 80)}")
        lines.append("")

    # --- Agent 小卡 ---
    agent = _block(data, "agent")
    if agent:
        lines.append(f"**🤖 Agent 小卡｜{clip(agent.get('title'), 36)}**")
        if agent.get("goal"):
            lines.append(f"目标：{clip(agent.get('goal'), 60)}")
        if agent.get("steps"):
            lines.append(f"怎么做：{clip(agent.get('steps'), 70)}")
        if agent.get("action"):
            lines.append(f"今晚：{clip(agent.get('action'), 60)}")
        lines.append("")

    # --- 英语 ---
    english = _block(data, "english")
    if english:
        mode = clip(english.get("mode"), 8)
        phrase = clip(english.get("phrase"), 30)
        head = f"🌍 英语｜{mode}" if mode else "🌍 英语"
        lines.append(f"**{head}｜{phrase}**" if phrase else f"**{head}**")
        if english.get("words"):
            lines.append(f"词汇：{clip(english.get('words'), 70)}")
        if english.get("ai_talk"):
            lines.append(f"AI Talk：{clip(english.get('ai_talk'), 60)}")

        video = english.get("video") or {}
        if isinstance(video, dict) and video.get("url"):
            v_title = clip(video.get("title"), 40)
            lines.append(f"视频：[{v_title}]({clip(video.get('url'), 200)})")
        lines.append("")

    # --- 周六落地日 ---
    landing = _block(data, "landing")
    if landing:
        lines.append("**🧩 周六落地日（可选）**")
        if landing.get("question"):
            lines.append(f"问题：{clip(landing.get('question'), 80)}")
        if landing.get("note"):
            lines.append(f"提示：{clip(landing.get('note'), 60)}")
        lines.append("")

    preview = clip(data.get("preview"), 70)
    if preview:
        lines.extend([f"🔭 {preview}", ""])

    # 链接单独预留位置，避免被截断导致无法点击
    link_block = f"\n[👉 阅读完整日报]({report_url})\n"
    body = "\n".join(lines).rstrip()
    budget = max(0, MAX_CONTENT_BYTES - len(link_block.encode("utf-8")))
    content = truncate_utf8(body, budget) + link_block

    # 额外安全网：极端情况下仍超限，则直接截断（此时链接可能丢失）
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        content = truncate_utf8(content, MAX_CONTENT_BYTES)

    return content


def normalize_webhook(raw: str) -> str:
    """补全 Webhook 协议头，兼容粘贴时漏掉 https:// 或带引号的情况。"""
    value = raw.strip().strip('"').strip("'")
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    return value


def serverchan_endpoint(sendkey: str) -> str:
    """根据 SendKey 前缀选择 Turbo 或 Server酱³ 的推送地址。"""
    key = sendkey.strip()
    if not key:
        raise NotifyError("SERVERCHAN_SENDKEY 为空")
    if key.startswith("sctp"):
        match = SERVERCHAN_SC3_RE.match(key)
        if not match:
            raise NotifyError("Server酱³ SendKey 格式不正确（应为 sctp<uid>t...）")
        return SERVERCHAN_SC3_URL.format(uid=match.group(1), key=key)
    return SERVERCHAN_TURBO_URL.format(key=key)


def send_serverchan(sendkey: str, title: str, content: str) -> None:
    """POST 到 Server酱，正文支持 Markdown。"""
    url = serverchan_endpoint(sendkey)
    last_error = ""

    for attempt in range(1, RETRY_TIMES + 1):
        try:
            response = requests.post(
                url,
                data={"title": title, "desp": content},
                timeout=15,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise NotifyError("Server酱返回了非预期的响应格式")
            code = result.get("code")
            if code != 0:
                raise NotifyError(
                    f"Server酱返回错误 code={code} message={result.get('message')}"
                )
            print("[ok] Server酱通知已发送", file=sys.stderr)
            return
        except (requests.RequestException, ValueError, NotifyError) as exc:
            last_error = safe_error(exc)
            print(
                f"[warn] Server酱发送失败（{attempt}/{RETRY_TIMES}）：{last_error}",
                file=sys.stderr,
            )
            if attempt < RETRY_TIMES:
                time.sleep(2 * attempt)

    raise NotifyError(f"Server酱通知发送失败：{last_error}")


def send_wecom(webhook: str, content: str) -> None:
    """POST 到企业微信机器人；对网络错误与业务错误统一重试。"""
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    last_error = ""

    for attempt in range(1, RETRY_TIMES + 1):
        try:
            response = requests.post(webhook, json=payload, timeout=15)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise NotifyError("企业微信返回了非预期的响应格式")
            errcode = result.get("errcode")
            if errcode != 0:
                raise NotifyError(
                    f"企业微信返回错误 errcode={errcode} errmsg={result.get('errmsg')}"
                )
            print("[ok] 企业微信通知已发送", file=sys.stderr)
            return
        except (requests.RequestException, ValueError, NotifyError) as exc:
            last_error = safe_error(exc)
            print(
                f"[warn] 企业微信发送失败（{attempt}/{RETRY_TIMES}）：{last_error}",
                file=sys.stderr,
            )
            if attempt < RETRY_TIMES:
                time.sleep(2 * attempt)

    raise NotifyError(f"企业微信通知发送失败：{last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="推送 Shaun Daily Report 到企业微信 / Server酱（微信）")
    parser.add_argument("--date", help="指定日期 YYYY-MM-DD，默认取最新一期")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送")
    args = parser.parse_args()

    # 环境变量检查放在流程开头。
    # 未配置 Webhook 视为「未启用通知」：打印日志后正常退出，
    # 不返回非零码，避免在 GitHub Actions 里被误判为失败。
    webhook = normalize_webhook(os.environ.get("WECOM_WEBHOOK", ""))
    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not webhook and not sendkey and not args.dry_run:
        print("[skip] 未配置 WECOM_WEBHOOK / SERVERCHAN_SENDKEY，跳过推送")
        return 0

    try:
        base_url = resolve_pages_base()
    except NotifyError as exc:
        if args.dry_run:
            base_url = "https://example.github.io/example"
            print(f"[warn] {exc}（dry-run 使用占位链接）", file=sys.stderr)
        else:
            print(f"[error] {exc}", file=sys.stderr)
            return 1

    try:
        report_path = discover_report_path(args.date)
        data = load_report(report_path)
        date_str = str(data["date"]).strip()
        report_url = f"{base_url}/reports/{date_str}.html"
        content = build_markdown(data, report_url)
    except NotifyError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 顶层兜底
        print(f"[error] 构造通知失败：{safe_error(exc)}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(content)
        print(
            f"\n[dry-run] 内容长度 {len(content.encode('utf-8'))} 字节，"
            f"来源 {report_path.name}",
            file=sys.stderr,
        )
        return 0

    failed = 0

    if webhook:
        try:
            send_wecom(webhook, content)
        except NotifyError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            failed += 1

    if sendkey:
        title = f"每日 HR 系统课 · {date_str}"
        try:
            send_serverchan(sendkey, title, content)
        except NotifyError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

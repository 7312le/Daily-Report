#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当日日报摘要推送到企业微信群机器人。

- WECOM_WEBHOOK：群机器人 Webhook 地址（GitHub Secrets）
- PAGES_BASE_URL：GitHub Pages 站点根地址（GitHub Variables，可选）
  未配置时根据 GITHUB_REPOSITORY 自动推导为 https://owner.github.io/repo
- 默认取 reports/ 下最新一期 JSON，也可用 --date YYYY-MM-DD 指定
- 企业微信 markdown 消息上限 4096 字节，脚本会自动截断正文并保留链接
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


def build_markdown(data: dict, report_url: str) -> str:
    """构造企业微信机器人支持的 markdown 内容。"""
    date_str = str(data.get("date", "")).strip()
    weekday = weekday_cn(date_str)
    heading_date = f"{date_str} {weekday}".strip()

    lines: list[str] = [f"# 📘 个人成长日报 · {heading_date}", ""]

    one_line = clip(data.get("one_line"), 90)
    if one_line:
        lines.extend([f"> {one_line}", ""])

    # --- 技能栈 ---
    skills = data.get("skills") or {}
    if isinstance(skills, dict) and skills:
        topic = clip(skills.get("topic"), 34)
        lines.append(f"**🧭 技能栈｜{topic}**")
        if skills.get("concept"):
            lines.append(f"概念：{clip(skills.get('concept'), 70)}")
        if skills.get("action"):
            lines.append(f"今晚：{clip(skills.get('action'), 60)}")
        lines.append("")

    # --- 财富自由 ---
    wealth = data.get("wealth") or {}
    if isinstance(wealth, dict) and wealth:
        topic = clip(wealth.get("topic"), 34)
        lines.append(f"**💰 财富自由｜{topic}**")
        if wealth.get("concept"):
            lines.append(f"核心：{clip(wealth.get('concept'), 70)}")
        if wealth.get("pitfall"):
            lines.append(f"误区：{clip(wealth.get('pitfall'), 60)}")
        if wealth.get("source"):
            lines.append(f"信源：{clip(wealth.get('source'), 50)}")
        lines.append("")

    # --- 认知 ---
    cognition = data.get("cognition") or {}
    if isinstance(cognition, dict) and cognition:
        model = clip(cognition.get("model"), 30)
        lines.append(f"**🧠 认知｜{model}**")
        if cognition.get("explain"):
            lines.append(f"解释：{clip(cognition.get('explain'), 70)}")
        if cognition.get("reflection"):
            lines.append(f"反思：{clip(cognition.get('reflection'), 60)}")
        lines.append("")

    # --- 英语 ---
    english = data.get("english") or {}
    if isinstance(english, dict) and english:
        grammar = english.get("grammar") or {}
        point = clip(grammar.get("point"), 30) if isinstance(grammar, dict) else ""
        title = f"**🌍 英语｜{point}**" if point else "**🌍 英语**"
        lines.append(title)

        words = [
            clip(item.get("word"), 24)
            for item in (english.get("vocab") or [])
            if isinstance(item, dict) and item.get("word")
        ]
        if words:
            lines.append("词汇：" + " / ".join(words[:5]))

        dialogue = english.get("dialogue") or {}
        if isinstance(dialogue, dict) and dialogue.get("scenario"):
            lines.append(f"对话：{clip(dialogue.get('scenario'), 60)}")

        listening = english.get("listening") or {}
        if isinstance(listening, dict) and listening.get("task"):
            lines.append(f"跟读：{clip(listening.get('task'), 60)}")
        lines.append("")

    tomorrow = clip(data.get("tomorrow_preview"), 70)
    if tomorrow:
        lines.extend([f"🔭 明日预览：{tomorrow}", ""])

    # 链接单独预留位置，避免被截断导致无法点击
    link_block = f"\n[👉 阅读完整日报]({report_url})\n"
    body = "\n".join(lines).rstrip()
    budget = max(0, MAX_CONTENT_BYTES - len(link_block.encode("utf-8")))
    content = truncate_utf8(body, budget) + link_block

    # 额外安全网：极端情况下仍超限，则直接截断（此时链接可能丢失）
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        content = truncate_utf8(content, MAX_CONTENT_BYTES)

    return content


def send_wecom(webhook: str, content: str) -> None:
    """POST 到企业微信机器人；对网络错误与业务错误统一重试。"""
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    last_error = ""

    for attempt in range(1, RETRY_TIMES + 1):
        try:
            response = requests.post(webhook, json=payload, timeout=15)
            response.raise_for_status()
            result = response.json()
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
    parser = argparse.ArgumentParser(description="发送个人成长日报到企业微信")
    parser.add_argument("--date", help="指定日期 YYYY-MM-DD，默认取最新一期")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送")
    args = parser.parse_args()

    # 环境变量检查放在流程开头
    webhook = os.environ.get("WECOM_WEBHOOK", "").strip()
    if not webhook and not args.dry_run:
        print("[error] 缺少环境变量 WECOM_WEBHOOK", file=sys.stderr)
        return 1

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

    try:
        send_wecom(webhook, content)
    except NotifyError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

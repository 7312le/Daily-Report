#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机会雷达：扫描上海 / 江苏官方公开页面，筛出消费券、补贴、发票抽奖、长期资格等机会。

设计约束：
- 只访问 data/radar_sources.json 白名单中的公开页面，不登录、不绕过验证；
- 只收录 .gov.cn 链接，链接直达官方原文；
- 关键词规则命中才收录，重复条目按 URL 去重，只推送新增；
- 首次运行只建立基线，不推送历史条目；
- 不生成链接、不补写截止时间，避免模型编造。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    urlencode,
    urldefrag,
    urljoin,
    urlparse,
    urlunparse,
)

import requests

from notify import (
    MAX_CONTENT_BYTES,
    normalize_webhook,
    resolve_pages_base,
    send_serverchan,
    send_wecom,
    truncate_utf8,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "data" / "radar_sources.json"
SEEN_PATH = ROOT / "data" / "radar_seen.json"
RADAR_DIR = ROOT / "radar"
TEMPLATE_PATH = ROOT / "templates" / "radar.html"

BEIJING = timezone(timedelta(hours=8))
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "ShaunRadar/1.0"
)
SECTION_HINTS = (
    "通知公告", "公告通知", "通知通告", "公示公告",
    "政策文件", "政策法规", "政策发布", "最新文件",
    "政务公开", "信息公开", "新闻发布", "要闻动态", "工作动态",
)
NAV_WORDS = {"更多", "更多>>", "more", "查看", "详情", "首页", "登录", "注册"}

MAX_SECTIONS = 5
MAX_SEEN = 3000
MAX_PAGE_ITEMS = 12
MAX_BASELINE_ITEMS = 24
MAX_PUSH_ITEMS = 6
REQUEST_TIMEOUT = 25
RETRY_TIMES = 2


class RadarError(RuntimeError):
    """可预期的雷达流程错误。"""


# ---------------------------------------------------------------
# HTML 解析
# ---------------------------------------------------------------
class LinkParser(HTMLParser):
    """收集 <a href> 及其可见文本；文本过短时回退到 title 属性。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href = ""
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._title = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "base" and values.get("href"):
            self.base_href = values["href"]
            return
        if tag == "a":
            self._href = values.get("href")
            self._title = values.get("title", "").strip()
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        text = " ".join("".join(self._text).split())
        if len(text) < 6 and self._title:
            text = " ".join(self._title.split())
        if text:
            self.links.append((self._href, text))
        self._href = None
        self._title = ""
        self._text = []


def parse_links(page_html: str, page_url: str) -> list[tuple[str, str]]:
    parser = LinkParser()
    try:
        parser.feed(page_html)
    except Exception:  # noqa: BLE001 - 个别页面结构异常不应中断整轮
        return []
    base = urljoin(page_url, parser.base_href) if parser.base_href else page_url
    links: list[tuple[str, str]] = []
    for href, text in parser.links:
        href = href.strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = urljoin(base, href)
        if url.startswith(("http://", "https://")):
            links.append((url, text))
    return links


def is_gov_link(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "gov.cn" or host.endswith(".gov.cn")


def normalize_url(url: str) -> str:
    cleaned = urldefrag(url)[0]
    parsed = urlparse(cleaned)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), "")
    )


# ---------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------
def fetch_page(url: str) -> tuple[str, str]:
    last_error: Exception | None = None
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            if not response.encoding or response.encoding.lower() in (
                "iso-8859-1",
                "ascii",
            ):
                response.encoding = response.apparent_encoding or "utf-8"
            return response.text, response.url
        except requests.RequestException as exc:
            last_error = exc
            if attempt < RETRY_TIMES:
                time.sleep(2 * attempt)
    raise RadarError(f"抓取失败：{type(last_error).__name__}")


def discover_sections(home_url: str, links: list[tuple[str, str]]) -> list[str]:
    home_host = (urlparse(home_url).hostname or "").lower()
    seen: set[str] = set()
    sections: list[str] = []
    for url, text in links:
        normalized = normalize_url(url)
        if normalized in seen or not is_gov_link(normalized):
            continue
        if (urlparse(normalized).hostname or "").lower() != home_host:
            continue
        label = text.strip()
        if len(label) > 24 or label.lower() in NAV_WORDS:
            continue
        if not any(hint in label for hint in SECTION_HINTS):
            continue
        seen.add(normalized)
        sections.append(normalized)
        if len(sections) >= MAX_SECTIONS:
            break
    return sections


# ---------------------------------------------------------------
# 规则匹配
# ---------------------------------------------------------------
def classify(title: str, rules: list[dict], excludes: list[str]) -> str | None:
    if not title or any(word in title for word in excludes):
        return None
    for rule in rules:
        words = rule.get("any") or []
        if not any(word in title for word in words):
            continue
        context = rule.get("context") or []
        if context and not any(word in title for word in context):
            continue
        return str(rule.get("category", "")).strip() or None
    return None


def clean_title(title: str) -> str:
    text = " ".join(title.split()).strip(" 　·>-—|")
    return text


class SourceCollector:
    def __init__(self, source: dict, rules: list[dict], excludes: list[str]) -> None:
        self.source = source
        self.rules = rules
        self.excludes = excludes
        self.name = str(source.get("name", "")).strip() or "未知来源"
        self.region = str(source.get("region", "")).strip() or "全国"
        self.pages_ok = 0
        self.pages_failed = 0
        self.candidates: dict[str, dict] = {}

    def _add_links(self, links: list[tuple[str, str]]) -> None:
        for url, raw_title in links:
            url = normalize_url(url)
            if not is_gov_link(url):
                continue
            title = clean_title(raw_title)
            if not (8 <= len(title) <= 120):
                continue
            if title.lower() in NAV_WORDS:
                continue
            category = classify(title, self.rules, self.excludes)
            if not category:
                continue
            if url in self.candidates:
                continue
            self.candidates[url] = {
                "title": title,
                "url": url,
                "source": self.name,
                "region": self.region,
                "category": category,
            }

    def collect(self) -> list[dict]:
        primary = str(self.source.get("url", "")).strip()
        explicit_sections = [
            str(item).strip()
            for item in (self.source.get("sections") or [])
            if str(item).strip()
        ]
        if not primary.startswith(("http://", "https://")):
            print(f"[warn] {self.name} 缺少合法 url，跳过", file=sys.stderr)
            return []

        pages: list[str] = [primary]
        section_links: list[str] = []

        try:
            page_html, final_url = fetch_page(primary)
        except RadarError as exc:
            print(f"[warn] {self.name} 首页失败：{exc}", file=sys.stderr)
            self.pages_failed += 1
            page_html, final_url, primary_links = "", primary, []
        else:
            self.pages_ok += 1
            primary_links = parse_links(page_html, final_url)
            self._add_links(primary_links)
            if explicit_sections:
                section_links = explicit_sections
            else:
                section_links = discover_sections(final_url, primary_links)

        for section in section_links:
            if section in pages:
                continue
            pages.append(section)
            time.sleep(0.8)
            try:
                section_html, section_final = fetch_page(section)
            except RadarError as exc:
                print(f"[warn] {self.name} 栏目失败：{exc}", file=sys.stderr)
                self.pages_failed += 1
                continue
            self.pages_ok += 1
            self._add_links(parse_links(section_html, section_final))

        print(
            f"[scan] {self.name}: {len(self.candidates)} 条命中 "
            f"（页面 {self.pages_ok} 成功 / {self.pages_failed} 失败）",
            file=sys.stderr,
        )
        return list(self.candidates.values())


# ---------------------------------------------------------------
# 去重存储
# ---------------------------------------------------------------
def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {"updated_at": "", "items": {}}
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warn] 读取去重库失败，按首次运行处理：{exc}", file=sys.stderr)
        return {"updated_at": "", "items": {}}
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"updated_at": "", "items": {}}
    return data


def save_seen(data: dict) -> None:
    items = data.get("items") or {}
    ordered = dict(
        sorted(
            items.items(),
            key=lambda pair: str(pair[1].get("last_seen", "")),
            reverse=True,
        )[:MAX_SEEN]
    )
    payload = {"updated_at": data.get("updated_at", ""), "items": ordered}
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def item_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------
def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def render_item(item: dict) -> str:
    return (
        '    <article class="item">\n'
        '      <div class="item-meta">\n'
        f'        <span class="badge badge-region">{esc(item.get("region"))}</span>\n'
        f'        <span class="badge badge-cat">{esc(item.get("category"))}</span>\n'
        f'        <span class="src">{esc(item.get("source"))}</span>\n'
        "      </div>\n"
        f'      <h3><a href="{esc(item.get("url"))}" target="_blank" '
        f'rel="noopener noreferrer">{esc(item.get("title"))}</a></h3>\n'
        '      <div class="hint">点击标题打开官方原文；办理条件与截止时间以原文为准。</div>\n'
        "    </article>"
    )


def render_page(
    template: str,
    date_str: str,
    weekday: str,
    summary: str,
    note: str,
    items: list[dict],
    empty_text: str,
    generated_at: str,
) -> str:
    if items:
        items_html = '  <section class="list">\n' + "\n".join(
            render_item(item) for item in items
        ) + "\n  </section>"
    else:
        items_html = (
            f'  <div class="empty">{esc(empty_text)}</div>'
        )

    replacements = {
        "{{DATE}}": esc(date_str),
        "{{WEEKDAY}}": esc(weekday),
        "{{SUMMARY}}": esc(summary),
        "{{NOTE}}": note,
        "{{ITEMS}}": items_html,
        "{{GENERATED_AT}}": esc(generated_at),
    }
    output = template
    for key, value in replacements.items():
        output = output.replace(key, value)
    return output


def index_page(entries: list[dict]) -> str:
    rows: list[str] = []
    for entry in entries:
        titles = "".join(
            f'<li><a href="{esc(entry["date"])}.html">{esc(title)}</a></li>'
            for title in entry["titles"][:2]
        )
        rows.append(
            f'      <li class="day">\n'
            f'        <a class="day-head" href="{esc(entry["date"])}.html">\n'
            f'          <span>{esc(entry["date"])}</span>\n'
            f'          <span class="count">{entry["count"]} 条</span>\n'
            f"        </a>\n"
            f'        <ul class="titles">{titles}</ul>\n'
            f"      </li>"
        )
    body = "\n".join(rows) if rows else '      <li class="empty">还没有雷达记录。</li>'
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>机会雷达 · 归档</title>
<style>
  body {{ margin:0; padding:32px 16px 56px; background:#f5f6f8; color:#16181d;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.7; }}
  .wrap {{ max-width:880px; margin:0 auto; }}
  .eyebrow {{ font-size:12px; letter-spacing:.18em; text-transform:uppercase; color:#0f766e; font-weight:600; }}
  h1 {{ font-size:30px; margin:8px 0 6px; }}
  .meta {{ color:#6b7280; font-size:14px; }}
  ul {{ list-style:none; padding:0; margin:22px 0 0; display:grid; gap:12px; }}
  li.day {{ background:#fff; border:1px solid #e6e8ec; border-radius:14px; padding:16px 18px; }}
  .day-head {{ display:flex; justify-content:space-between; color:#16181d; text-decoration:none; font-weight:600; }}
  .count {{ color:#0f766e; font-size:13px; }}
  .titles {{ margin:8px 0 0; display:block; }}
  .titles li {{ font-size:14px; margin:4px 0; }}
  .titles a {{ color:#6b7280; text-decoration:none; }}
  .titles a:hover {{ color:#0f766e; text-decoration:underline; }}
  .empty {{ color:#6b7280; text-align:center; padding:32px; background:#fff; border:1px dashed #e6e8ec; border-radius:14px; }}
  .back {{ margin-top:28px; font-size:14px; }}
  .back a {{ color:#0f766e; text-decoration:none; }}
  footer {{ margin-top:36px; padding-top:16px; border-top:1px solid #e6e8ec; color:#6b7280; font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow">Opportunity Radar</div>
  <h1>机会雷达 · 归档</h1>
  <div class="meta">上海 / 江苏官方机会，每日 14:00 扫描</div>
  <ul>
{body}
  </ul>
  <p class="back"><a href="../index.html">← 返回日报首页</a></p>
  <footer>仅收录政府公开页面，链接直达原文；不代办、不代抢、不接返利。</footer>
</div>
</body>
</html>
"""


def write_index() -> None:
    entries: list[dict] = []
    for path in sorted(RADAR_DIR.glob("*.json"), reverse=True):
        date_str = path.stem
        if not DATE_RE.match(date_str):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = data.get("items") or []
        titles = [str(item.get("title", "")) for item in items if item.get("title")]
        entries.append(
            {
                "date": date_str,
                "count": int(data.get("new_count") or len(items)),
                "titles": titles,
            }
        )
    (RADAR_DIR / "index.html").write_text(index_page(entries), encoding="utf-8")


# ---------------------------------------------------------------
# 推送
# ---------------------------------------------------------------
def build_markdown(
    date_str: str,
    weekday: str,
    items: list[dict],
    total: int,
    page_url: str,
    baseline: bool,
    scanned: int,
) -> str:
    lines = [f"# 🧭 机会雷达 · {date_str} {weekday}", ""]
    if baseline:
        lines.append(
            f"雷达已上线：完成基线扫描，收录 {total} 条历史条目（扫描 {scanned} 个源）。"
            "明日起只推送新增。"
        )
    else:
        lines.append(f"今日新增 {total} 条，展示 {len(items)} 条（扫描 {scanned} 个源）。")
    lines.append("")

    for index, item in enumerate(items, 1):
        lines.append(
            f"**{index}. [{item.get('region')}·{item.get('category')}] "
            f"{item.get('title')}**"
        )
        lines.append(f"[打开原文]({item.get('url')})")
        lines.append("")

    if not baseline and total > len(items):
        lines.append(f"另有 {total - len(items)} 条，见归档页。")
        lines.append("")

    # 链接单独预留，避免正文过长把归档链接截掉
    link_block = f"\n\n[👉 查看机会雷达]({page_url})\n"
    body = "\n".join(lines).rstrip()
    budget = max(0, MAX_CONTENT_BYTES - len(link_block.encode("utf-8")))
    content = truncate_utf8(body, budget) + link_block
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        content = truncate_utf8(content, MAX_CONTENT_BYTES)
    return content


def notify(
    date_str: str,
    weekday: str,
    items: list[dict],
    total: int,
    baseline: bool,
    scanned: int,
    dry_run: bool,
) -> None:
    webhook = normalize_webhook(os.environ.get("WECOM_WEBHOOK", ""))
    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()

    if dry_run:
        content = build_markdown(
            date_str,
            weekday,
            items,
            total,
            "https://example.github.io/example/radar/"
            f"{date_str}.html",
            baseline,
            scanned,
        )
        print("[dry-run] 推送预览：")
        print(content)
        return

    if not webhook and not sendkey:
        print("[skip] 未配置 WECOM_WEBHOOK / SERVERCHAN_SENDKEY，跳过机会雷达推送")
        return
    if not items and not baseline:
        print("[skip] 今日无新增，不推送")
        return

    try:
        base_url = resolve_pages_base()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 无法确定 Pages 地址，跳过推送：{exc}", file=sys.stderr)
        return

    page_url = f"{base_url}/radar/{date_str}.html"
    content = build_markdown(
        date_str, weekday, items, total, page_url, baseline, scanned
    )

    if webhook:
        try:
            send_wecom(webhook, content)
        except Exception as exc:  # noqa: BLE001 - 通知失败不阻断归档
            print(f"[warn] 企业微信推送失败：{exc}", file=sys.stderr)
    if sendkey:
        try:
            send_serverchan(sendkey, f"机会雷达 · {date_str}", content)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Server酱推送失败：{exc}", file=sys.stderr)


# ---------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="机会雷达：扫描官方源并推送新增机会")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印，不写文件、不推送")
    args = parser.parse_args()

    try:
        config = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[error] 读取源配置失败：{exc}", file=sys.stderr)
        return 1

    sources = [item for item in (config.get("sources") or []) if isinstance(item, dict)]
    rules = [item for item in (config.get("rules") or []) if isinstance(item, dict)]
    excludes = [str(word) for word in (config.get("exclude_keywords") or [])]
    if not sources or not rules:
        print("[error] 源配置缺少 sources 或 rules", file=sys.stderr)
        return 1

    now = datetime.now(BEIJING)
    date_str = now.strftime("%Y-%m-%d")
    weekday = WEEKDAYS[now.weekday()]
    generated_at = now.strftime("%Y-%m-%d %H:%M")

    seen = load_seen()
    seen_items = seen.get("items")
    if not isinstance(seen_items, dict):
        seen_items = {}
    # 保持引用一致：后续写入 seen_items 才能被 save_seen 保存
    seen["items"] = seen_items
    baseline = not seen_items

    candidates: list[dict] = []
    scanned = 0
    failed_sources = 0
    for source in sources:
        collector = SourceCollector(source, rules, excludes)
        try:
            items = collector.collect()
        except Exception as exc:  # noqa: BLE001 - 单源异常不阻断
            print(f"[warn] {collector.name} 采集异常：{exc}", file=sys.stderr)
            failed_sources += 1
            continue
        if collector.pages_ok:
            scanned += 1
        if not collector.pages_ok:
            failed_sources += 1
        candidates.extend(items)

    if scanned == 0:
        print("[error] 所有源均抓取失败，终止本轮", file=sys.stderr)
        return 1

    # 按分类优先级与来源顺序稳定排序
    category_order = [str(rule.get("category", "")) for rule in rules]
    source_order = {
        str(source.get("name", "")): index for index, source in enumerate(sources)
    }
    candidates.sort(
        key=lambda item: (
            category_order.index(item["category"])
            if item["category"] in category_order
            else len(category_order),
            source_order.get(item["source"], len(source_order)),
            item["title"],
        )
    )

    new_items: list[dict] = []
    for item in candidates:
        key = item_key(item["url"])
        record = seen_items.get(key)
        if record:
            record["last_seen"] = date_str
            continue
        entry = dict(item)
        entry["first_seen"] = date_str
        entry["last_seen"] = date_str
        seen_items[key] = entry
        new_items.append(entry)

    if baseline:
        page_items = new_items[:MAX_BASELINE_ITEMS]
        total = len(new_items)
        summary = f"基线收录 {total} 条 · 扫描 {scanned} 个源"
        note = (
            '<div class="note">首次运行：本次建立基线，不推送历史条目；'
            "明日起只推送新增。</div>"
        )
        push_items: list[dict] = []
    else:
        page_items = new_items[:MAX_PAGE_ITEMS]
        total = len(new_items)
        summary = f"新增 {total} 条 · 扫描 {scanned} 个源"
        note = (
            '<div class="note">今日无新增，雷达继续每日扫描。</div>'
            if not new_items
            else ""
        )
        push_items = new_items[:MAX_PUSH_ITEMS]

    if args.dry_run:
        print(
            f"[dry-run] {date_str} 扫描 {scanned} 个源，失败 {failed_sources} 个，"
            f"命中 {len(candidates)} 条，新增 {len(new_items)} 条"
        )
        for item in new_items[:40]:
            print(f"  [new] {item['region']}·{item['category']} | {item['title']} | {item['url']}")
        if len(new_items) > 40:
            print(f"  ... 另有 {len(new_items) - 40} 条未打印")
        notify(
            date_str, weekday, push_items, total, baseline, scanned, dry_run=True
        )
        return 0

    RADAR_DIR.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    page_html = render_page(
        template=template,
        date_str=date_str,
        weekday=weekday,
        summary=summary,
        note=note,
        items=page_items,
        empty_text="今日无新增。雷达每天 14:00 继续扫描。",
        generated_at=generated_at,
    )
    (RADAR_DIR / f"{date_str}.html").write_text(page_html, encoding="utf-8")
    (RADAR_DIR / f"{date_str}.json").write_text(
        json.dumps(
            {
                "date": date_str,
                "generated_at": generated_at,
                "baseline": baseline,
                "scanned": scanned,
                "new_count": total,
                "items": new_items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_index()

    seen["updated_at"] = generated_at
    save_seen(seen)

    print(
        f"[done] {date_str}: 扫描 {scanned} 个源（失败 {failed_sources}），"
        f"命中 {len(candidates)} 条，新增 {total} 条"
    )
    notify(date_str, weekday, push_items, total, baseline, scanned, dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机会雷达 · 官方源可达性探测。

按 data/radar_probe.json 逐个请求，输出状态码、耗时、大小、最终地址与页面标题。
用于确认 GitHub Actions（海外 IP）能否稳定访问国内政务源。
只读公开页面，不做任何写入或绕过。
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = ROOT / "data" / "radar_probe.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def decode_title(raw: bytes, content_type: str) -> str:
    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1)
    try:
        text = raw.decode(charset, errors="ignore")
    except LookupError:
        text = raw.decode("utf-8", errors="ignore")
    title = TITLE_RE.search(text)
    return " ".join(title.group(1).split()) if title else ""


def _ipv4_only(*args, **kwargs):
    results = _ORIG_GETADDRINFO(*args, **kwargs)
    ipv4 = [item for item in results if item[0] == socket.AF_INET]
    return ipv4 or results


_ORIG_GETADDRINFO = socket.getaddrinfo


def _open(request: urllib.request.Request, context: ssl.SSLContext, ipv4_only: bool):
    if not ipv4_only:
        return urllib.request.urlopen(request, timeout=25, context=context)
    original = socket.getaddrinfo
    socket.getaddrinfo = _ipv4_only
    try:
        return urllib.request.urlopen(request, timeout=25, context=context)
    finally:
        socket.getaddrinfo = original


def fetch(url: str) -> tuple[int, float, int, str, str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    verified = ssl.create_default_context()
    unverified = ssl._create_unverified_context()  # noqa: SLF001 - 仅用于探测证书异常

    started = time.time()
    response = None
    last_error: Exception | None = None
    # 依次尝试：证书校验 → 强制 IPv4 → 跳过证书校验 → 跳过校验 + IPv4。
    # 海外 runner 常因没有 IPv6 路由报 Errno 101，强制 IPv4 即可恢复。
    for context, ipv4_only in (
        (verified, False),
        (verified, True),
        (unverified, False),
        (unverified, True),
    ):
        try:
            response = _open(request, context, ipv4_only)
            break
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            last_error = exc
            reason = getattr(exc, "reason", exc)
            if not isinstance(reason, OSError):
                raise
    if response is None:
        raise last_error or urllib.error.URLError("unknown error")

    with response:
        raw = response.read(300_000)
        elapsed = time.time() - started
        return (
            response.status,
            elapsed,
            len(raw),
            response.geturl(),
            response.headers.get("Content-Type", ""),
            decode_title(raw, response.headers.get("Content-Type", "")),
        )


def main() -> int:
    try:
        entries = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[error] 读取探测清单失败：{exc}", file=sys.stderr)
        return 1

    if not isinstance(entries, list):
        print("[error] 探测清单必须是数组", file=sys.stderr)
        return 1

    ok = 0
    for item in entries:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        print(f"\n=== {name} ({url})")
        if not url.startswith(("http://", "https://")):
            print("[skip] 地址不合法")
            continue

        try:
            status, elapsed, size, final_url, content_type, title = fetch(url)
        except urllib.error.HTTPError as exc:
            print(f"[HTTP {exc.code}] {exc.reason}")
            continue
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            print(f"[fail] {type(exc).__name__}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - 探测脚本不因单站异常中断
            print(f"[fail] {type(exc).__name__}: {exc}")
            continue

        ok += 1
        print(
            f"[{status}] {elapsed:.2f}s {size / 1024:.0f}KB "
            f"type={content_type or '-'}"
        )
        print(f"final={final_url}")
        print(f"title={title or '(空)'}")

    print(f"\n[summary] 成功 {ok}/{len(entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

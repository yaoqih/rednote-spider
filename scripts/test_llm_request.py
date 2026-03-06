#!/usr/bin/env python3
"""Minimal connectivity test for OpenAI-compatible chat completions."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from urllib import error, request

from dotenv import load_dotenv


load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a minimal chat/completions request")
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPPORTUNITY_LLM_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI-compatible base URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("OPPORTUNITY_LLM_API_KEY", ""),
        help="API key (default: OPPORTUNITY_LLM_API_KEY)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPPORTUNITY_LLM_MODEL", "gpt-4.1-mini"),
        help="Model name (default: OPPORTUNITY_LLM_MODEL)",
    )
    parser.add_argument("--message", default="ping", help="User message")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout seconds")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if not args.api_key.strip():
        raise SystemExit("missing api key: pass --api-key or set OPPORTUNITY_LLM_API_KEY")

    url = args.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.message}],
        "temperature": 0.1,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = request.Request(
        url=url,
        data=body,
        headers={
            "Authorization": f"Bearer {args.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "rednote-spider/1.0",
        },
        method="POST",
    )

    ctx = ssl.create_default_context()
    try:
        with request.urlopen(req, timeout=args.timeout, context=ctx) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            print(f"status={resp.status}")
            print(text)
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        print(f"http_error={exc.code}")
        print(text)
        raise SystemExit(1) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"request_error={exc.__class__.__name__}: {exc}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()

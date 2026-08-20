"""
console.py — Cho phép in tiếng Việt ra màn hình dòng lệnh.

Console Windows mặc định dùng bảng mã cp1252, gặp ký tự tiếng Việt là ném
UnicodeEncodeError và làm chết cả chương trình. Gọi setup() một lần lúc khởi
động là xong.
"""
from __future__ import annotations

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:                                               # noqa: BLE001
            pass

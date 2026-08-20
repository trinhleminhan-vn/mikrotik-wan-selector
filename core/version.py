"""
version.py — Xét phiên bản RouterOS và chọn "phương ngữ" lệnh phù hợp.

Vì sao cần: cú pháp tạo bảng định tuyến khác nhau hẳn giữa v6 và v7.

    v7:  /routing table add fib name=sel-viettel
         /ip route add dst-address=0.0.0.0/0 gateway=pppoe-out1 routing-table=sel-viettel

    v6:  (không có menu /routing table)
         /ip route add dst-address=0.0.0.0/0 gateway=pppoe-out1 routing-mark=sel-viettel

Chạy lệnh v7 trên router v6 sẽ báo lỗi giữa chừng — nguy hiểm vì lúc đó cấu hình
đã ghi được một nửa. Nên phải chọn đúng phương ngữ TRƯỚC khi sinh lệnh.
"""
from __future__ import annotations

import re

# Mốc phiên bản
MIN_V6 = (6, 40, 0)      # dưới mức này thì không đỡ nổi
GOOD_V7 = (7, 10, 0)     # từ đây trở lên là yên tâm
NEWEST_KNOWN = "7.23.x"  # bản mới nhất mà tool đã được kiểm chứng


def parse(version: str) -> tuple[int, int, int]:
    """'7.23.2 (stable)' -> (7, 23, 2). Không đọc được thì trả (0,0,0)."""
    m = re.match(r"\s*(\d+)\.(\d+)(?:\.(\d+))?", version or "")
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def dialect_for(version: str) -> str:
    """'v7' hoặc 'v6' — quyết định cú pháp sinh lệnh."""
    return "v6" if parse(version)[0] == 6 else "v7"


def assess(version: str, free_mb: float = 0.0) -> dict:
    """
    Trả về:
        level   : 'ok' | 'warn' | 'legacy' | 'blocked'
        dialect : 'v7' | 'v6'
        title   : câu tóm tắt
        hints   : các việc nên làm
    'blocked' nghĩa là KHÔNG cho áp dụng.
    """
    v = parse(version)
    dialect = dialect_for(version)
    shown = version or "không đọc được"

    if v == (0, 0, 0):
        return {
            "level": "blocked", "dialect": dialect,
            "title": "Không đọc được phiên bản RouterOS.",
            "hints": ["Thiết bị có thể không phải MikroTik, hoặc tài khoản bị hạn chế quyền đọc."],
        }

    if v[0] >= 8:
        return {
            "level": "warn", "dialect": "v7",
            "title": f"RouterOS {shown} mới hơn bản tool đã kiểm chứng ({NEWEST_KNOWN}).",
            "hints": [
                "Cú pháp lệnh có thể đã đổi. Hãy xem kỹ phần xem trước trước khi áp dụng.",
                "Dead-man switch vẫn bật nên nếu hỏng thì router tự hoàn tác.",
            ],
        }

    if v[0] == 7:
        if v >= GOOD_V7:
            return {"level": "ok", "dialect": "v7",
                    "title": f"RouterOS {shown} — phiên bản phù hợp.", "hints": []}
        return {
            "level": "warn", "dialect": "v7",
            "title": f"RouterOS {shown} là bản v7 đời đầu.",
            "hints": [
                "Vẫn chạy được, nhưng v7.0–7.9 còn nhiều lỗi ở phần định tuyến và VRRP.",
                "Nên nâng lên 7.10 trở lên trước khi dùng cho hệ thống chạy thật.",
                "Xem mục 'Nâng cấp RouterOS' trong README.",
            ],
        }

    if v[0] == 6:
        if v < MIN_V6:
            return {
                "level": "blocked", "dialect": "v6",
                "title": f"RouterOS {shown} quá cũ, tool không hỗ trợ.",
                "hints": [
                    f"Cần tối thiểu {MIN_V6[0]}.{MIN_V6[1]}.",
                    "Nâng cấp dần: 6.x cũ → 6.49.x → rồi mới lên 7.x. "
                    "Không nhảy thẳng từ bản rất cũ lên v7.",
                ],
            }
        return {
            "level": "legacy", "dialect": "v6",
            "title": f"RouterOS {shown} — dùng cú pháp v6.",
            "hints": [
                "Tool tự chuyển sang cú pháp v6 (dùng routing-mark thay cho routing-table).",
                "Phần v6 CHƯA được kiểm chứng trên thiết bị thật — hãy xem kỹ phần xem trước.",
                "Khuyến nghị nâng lên v7.10+ khi có điều kiện: v6 đã ngừng phát triển tính năng.",
            ],
        }

    return {
        "level": "blocked", "dialect": dialect,
        "title": f"RouterOS {shown} không nằm trong phạm vi hỗ trợ.",
        "hints": ["Tool hỗ trợ RouterOS 6.40 trở lên."],
    }


def disk_warning(free_mb: float, board: str = "") -> str | None:
    """
    Cảnh báo dung lượng trước khi nâng cấp RouterOS.

    Gói cài RouterOS cho dòng mmips (hEX, hAP) khoảng 12–18 MB. Thiết bị flash
    16 MB mà chỉ còn vài MB trống thì lệnh nâng cấp sẽ thất bại giữa chừng.
    """
    if free_mb <= 0:
        return None
    if free_mb < 20:
        return (
            f"Ổ đĩa router chỉ còn {free_mb:.1f} MiB trống. Gói cài RouterOS thường "
            f"12–18 MB, nên NÂNG CẤP CÓ THỂ THẤT BẠI. Trước khi nâng cấp hãy xoá bớt "
            f"file trong Files (backup cũ, .npk cũ, log) — xem mục 'Nâng cấp RouterOS' "
            f"trong README."
        )
    return None

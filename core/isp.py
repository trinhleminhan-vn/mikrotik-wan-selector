"""
isp.py — Đoán tên nhà mạng từ IP public (dùng RDAP, không cần API key).

Mục đích: khi admin mở app lên, tool đã tự điền sẵn "Viettel" / "VNPT" / "FPT"
cho từng đường PPPoE thay vì bắt gõ tay và đoán mò xem cổng nào là nhà mạng nào.
Comment trong cấu hình router thường sai hoặc cũ, IP public thì không nói dối.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

# Khớp theo netname/org trả về từ RDAP -> tên hiển thị quen thuộc ở Việt Nam
KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("VIETTEL",),                          "Viettel"),
    (("VNPT", "VDC", "VIETNAM POST"),       "VNPT"),
    (("FPT",),                              "FPT"),
    (("CMC",),                              "CMC"),
    (("SCTV",),                             "SCTV"),
    (("MOBIFONE",),                         "MobiFone"),
    (("NETNAM",),                           "NetNam"),
    (("HTC-ITC", "HANOI TELECOM"),          "Hanoi Telecom"),
    (("VIETTELIDC",),                       "Viettel IDC"),
]

RDAP_ENDPOINTS = (
    "https://rdap.apnic.net/ip/{ip}",
    "https://rdap.org/ip/{ip}",
)


def _fetch(url: str, timeout: float) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/rdap+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def lookup(ip: str, timeout: float = 12.0) -> tuple[str | None, str]:
    """
    Trả về (tên nhà mạng đoán được, mô tả thô).

    Tên có thể là None nếu không khớp từ khoá nào — khi đó admin tự gõ.
    Hàm này KHÔNG bao giờ ném lỗi: không có mạng thì trả về (None, "").
    """
    data = None
    for tpl in RDAP_ENDPOINTS:
        data = _fetch(tpl.format(ip=ip), timeout)
        if data:
            break
    if not data:
        return None, ""

    parts: list[str] = []
    if data.get("name"):
        parts.append(str(data["name"]))
    for ent in data.get("entities", []) or []:
        vcard = ent.get("vcardArray")
        if not vcard or len(vcard) < 2:
            continue
        for fld in vcard[1]:
            if len(fld) >= 4 and fld[0] == "fn":
                parts.append(str(fld[3]))
    for rem in data.get("remarks", []) or []:
        for line in rem.get("description", []) or []:
            parts.append(str(line))

    raw = " | ".join(dict.fromkeys(parts))
    upper = raw.upper()
    for keys, label in KEYWORDS:
        if any(k in upper for k in keys):
            return label, raw
    return None, raw

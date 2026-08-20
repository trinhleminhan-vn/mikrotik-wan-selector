"""
build.py — Ghép client dùng chung + profiles của từng khách thành bộ cài.

Đây là lý do không cần "sinh app riêng cho từng khách": mã nguồn client là
MỘT bản duy nhất, thứ khác nhau chỉ là file cấu hình nhỏ đi kèm. Sửa một lỗi
là mọi khách đều được vá, không phải build lại N bản.

Hai thứ hay hỏng khi bộ cài đi qua lại giữa các hệ điều hành, xử lý sẵn ở đây:
  - Kết thúc dòng: CRLF cho Windows, LF cho macOS/Linux. Chỉ một ký tự \\r lọt
    vào file .command là macOS báo lỗi rất khó hiểu.
  - Quyền chạy: 0755 ghi thẳng vào metadata Unix của file zip, nên giải nén
    trên macOS là bấm đúp chạy được ngay, không phải chmod.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import stat
import zipfile

# (tên file nguồn trong client/, tên trong gói, kiểu xuống dòng, có thực thi không)
LAYOUT: dict[str, list[tuple[str, str, str, bool]]] = {
    "Windows": [
        ("ChonNhaMang.cmd", "ChonNhaMang.cmd", "crlf", False),
        ("WanSwitch.ps1", "WanSwitch.ps1", "keep", False),      # giữ nguyên BOM
        ("README-Windows.txt", "HUONG-DAN.txt", "crlf", False),
    ],
    "macOS": [
        ("ChonNhaMang.command", "ChonNhaMang.command", "lf", True),
        ("wanswitch.sh", "wanswitch.sh", "lf", True),
        ("README-macOS.txt", "HUONG-DAN.txt", "lf", False),
    ],
}

ZIP_DATE = (2026, 1, 1, 0, 0, 0)        # cố định để build lại cho ra file giống hệt


def _eol(data: bytes, mode: str) -> bytes:
    if mode == "keep":
        return data
    body = data.replace(b"\r\n", b"\n")
    return body.replace(b"\n", b"\r\n") if mode == "crlf" else body


def _sh_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def profiles_sh(data: dict) -> str:
    """Bản cấu hình cho shell — không cần jq hay python trên máy khách."""
    lines = [
        "# Sinh tự động, đừng sửa tay.",
        f"SYSTEM_NAME={_sh_quote(data.get('system_name', 'Chọn nhà mạng ra Internet'))}",
        f"LAN_PREFIX={_sh_quote(_lan_prefix(data))}",
        f"PROFILE_COUNT={len(data['profiles'])}",
    ]
    for i, p in enumerate(data["profiles"], start=1):
        lines += [
            f"P{i}_NAME={_sh_quote(p['name'])}",
            f"P{i}_GW={_sh_quote(p['gateway'])}",
            f"P{i}_DETAIL={_sh_quote(p.get('detail', ''))}",
            f"P{i}_DEFAULT={1 if p.get('is_default') else 0}",
        ]
    return "\n".join(lines) + "\n"


def _lan_prefix(data: dict) -> str:
    subnet = str(data.get("lan_subnet", ""))
    octets = subnet.split("/")[0].split(".")
    return ".".join(octets[:3]) + "." if len(octets) >= 3 else ""


def _profile_table(data: dict) -> str:
    width = max((len(p["name"]) for p in data["profiles"]), default=10)
    rows = [
        "",
        "",
        "CÁC LỰA CHỌN CỦA HỆ THỐNG NÀY",
        "------------------------------------------------------------------------",
        f"  Lớp mạng LAN : {data.get('lan_subnet', '?')}",
        "",
        f"  {'Tên lựa chọn'.ljust(width)}   Gateway            Ý nghĩa",
    ]
    for p in data["profiles"]:
        rows.append(f"  {p['name'].ljust(width)}   {p['gateway']:<18} {p.get('detail', '')}")
    rows += [
        "",
        "  Gõ đúng 'Tên lựa chọn' (cả dấu tiếng Việt) khi dùng bằng dòng lệnh.",
        "",
    ]
    return "\n".join(rows)


def build_packages(data: dict, out_dir: pathlib.Path,
                   client_dir: pathlib.Path,
                   name_prefix: str = "ChonNhaMang") -> list[pathlib.Path]:
    if not data.get("profiles"):
        raise ValueError("Không có profile nào để đóng gói.")
    if not _lan_prefix(data):
        raise ValueError("Thiếu 'lan_subnet' — client sẽ không biết tìm card mạng nào.")

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    sh_bytes = profiles_sh(data).encode("utf-8")

    made: list[pathlib.Path] = []
    for plat, files in LAYOUT.items():
        pkg = f"{name_prefix}-{plat}"
        folder = out_dir / pkg
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        zpath = out_dir / f"{pkg}.zip"

        entries: list[tuple[str, bytes, bool]] = []
        for src, dst, eol, is_exec in files:
            sp = client_dir / src
            if not sp.exists():
                raise FileNotFoundError(f"Thiếu file client: {sp}")
            blob = sp.read_bytes()
            if dst == "HUONG-DAN.txt":
                # Nối thêm bảng lựa chọn thật của khách này vào cuối hướng dẫn,
                # để người dùng cuối không phải mở profiles.json ra đọc.
                blob += _profile_table(data).encode("utf-8")
            entries.append((dst, _eol(blob, eol), is_exec))

        # file cấu hình riêng của khách
        if plat == "Windows":
            entries.append(("profiles.json", _eol(json_bytes, "keep"), False))
        else:
            entries.append(("profiles.sh", _eol(sh_bytes, "lf"), False))
            entries.append(("profiles.json", _eol(json_bytes, "keep"), False))

        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for dst, blob, is_exec in entries:
                target = folder / dst
                target.write_bytes(blob)
                if is_exec:
                    target.chmod(target.stat().st_mode | stat.S_IEXEC)

                info = zipfile.ZipInfo(f"{pkg}/{dst}", date_time=ZIP_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3                      # Unix
                info.external_attr = (0o755 if is_exec else 0o644) << 16
                z.writestr(info, blob)

        made += [folder, zpath]
    return made

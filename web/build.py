#!/usr/bin/env python3
"""
build.py — Ghép web/src thành MỘT file index.html tự chứa.

Vì sao gộp làm một file: trang này được thả vô website của người khác, hoặc mở
thẳng từ ổ đĩa. Một file thì chép đi đâu cũng chạy, không lo thiếu asset, không
lo đường dẫn, không cần server.

Nội dung client/ được nhúng thành hằng số JS, nên trang tự đóng gói được app
người dùng mà không cần gọi mạng.

    python web/build.py            ->  web/dist/index.html
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "src"
DIST = ROOT / "web" / "dist"
CLIENT = ROOT / "client"

# Đúng những file mà buildPackage() trong app.js đi tìm.
EMBED = [
    "ChonNhaMang.cmd",
    "WanSwitch.ps1",
    "README-Windows.txt",
    "ChonNhaMang.command",
    "wanswitch.sh",
    "README-macOS.txt",
]


def main() -> int:
    html = (SRC / "index.html").read_text(encoding="utf-8")
    app = (SRC / "app.js").read_text(encoding="utf-8")

    files: dict[str, str] = {}
    for name in EMBED:
        p = CLIENT / name
        if not p.exists():
            print(f"THIẾU: {p}", file=sys.stderr)
            return 1
        # utf-8-sig: WanSwitch.ps1 lưu kèm BOM, bỏ BOM ở đây rồi app.js gắn lại
        # khi đóng gói, tránh nhét hai BOM chồng nhau.
        files[name] = p.read_text(encoding="utf-8-sig")

    embed = "const CLIENT_FILES = " + json.dumps(files, ensure_ascii=False) + ";"

    # </script> nằm trong chuỗi JS sẽ đóng sớm thẻ script của trang.
    embed = embed.replace("</script>", "<\\/script>")
    app = app.replace("</script>", "<\\/script>")

    for marker, payload in (("/*__CLIENT_FILES__*/", embed), ("/*__APP_JS__*/", app)):
        if marker not in html:
            print(f"Không thấy mốc {marker} trong index.html", file=sys.stderr)
            return 1
        html = html.replace(marker, payload, 1)

    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / "index.html"
    out.write_text(html, encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"{out}  {kb:.0f} KB  (nhúng {len(files)} file client)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

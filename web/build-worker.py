#!/usr/bin/env python3
"""
build-worker.py — Gói trang sinh cấu hình thành một Cloudflare Worker.

Vì sao đi đường Worker chứ không thêm file vào source website:

  * API quản trị của site từ chối HTML thô (đúng như vậy: nhận HTML thô nghĩa là
    một khoá API lọt ra ngoài là chèn được script vào mọi phiên truy cập). Mà
    trang này sống bằng JavaScript.
  * Worker route hẹp hơn thì Cloudflare ưu tiên nó, nên chỉ đúng nhánh
    /tools/mikrotik-wan-selector bị chặn lại. Mọi đường khác đi thẳng về site cũ,
    không sửa một dòng nào của site.
  * Gỡ bỏ = xoá route. Không để lại dấu vết.

    python web/build-worker.py            -> web/worker/{src/worker.js, wrangler.toml}
    cd web/worker && npx wrangler deploy
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "dist" / "index.html"
OUT = ROOT / "web" / "worker"

# Đổi ở đây nếu muốn đường dẫn khác. Không có dấu / ở cuối.
BASE_PATH = "/tools/mikrotik-wan-selector"
ZONE = "trinhleminhan.com"
WORKER_NAME = "mikrotik-wan-selector"


def js_template_literal(text: str) -> str:
    """Nhét HTML vào template literal của JS. Ba thứ phải thoát, đúng thứ tự."""
    return (text.replace("\\", "\\\\")
                .replace("`", "\\`")
                .replace("${", "\\${"))


WORKER = '''// Sinh tự động bởi web/build-worker.py — đừng sửa tay, sửa web/src/ rồi build lại.
//
// Worker này chỉ phục vụ ĐÚNG một nhánh đường dẫn. Mọi request khác được trả về
// cho origin y nguyên, nên gắn vào tên miền đang chạy cũng không ảnh hưởng gì.

const BASE = %(base)r;

const HTML = `%(html)s`;

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const p = url.pathname.replace(/\\/+$/, '');      // bỏ dấu / thừa ở cuối

    // Route cua Cloudflare khop ca /BASE lan /BASE/*, nen phai tu don duong dan.
    // Duong dan phu -> 301 ve dia chi chuan, khoi bi coi la trung noi dung.
    if (p !== BASE) {
      if (url.pathname.startsWith(BASE + '/')) {
        return Response.redirect(url.origin + BASE, 301);
      }
      return fetch(request);                         // khong phai cua minh -> tra ve site
    }

    if (request.method === 'HEAD') {
      return new Response(null, { headers: headers() });
    }
    if (request.method !== 'GET') {
      return new Response('Method Not Allowed', { status: 405, headers: { allow: 'GET, HEAD' } });
    }
    return new Response(HTML, { headers: headers() });
  },
};

function headers() {
  return {
    'content-type': 'text/html; charset=utf-8',
    'cache-control': 'public, max-age=300',
    // Trang không gọi mạng, không nhúng gì từ ngoài. Khoá chặt luôn cho chắc,
    // để người dùng tin được câu "dữ liệu router không đi đâu hết".
    'content-security-policy':
      "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; " +
      "img-src data:; form-action 'none'; base-uri 'none'; frame-ancestors 'self'",
    'referrer-policy': 'no-referrer',
    'x-content-type-options': 'nosniff',
  };
}
'''

TOML = '''name = "%(name)s"
main = "src/worker.js"
compatibility_date = "2026-01-01"

# Route hẹp: chỉ nhánh này chạy qua Worker, còn lại về site như cũ.
# Muốn gỡ thì xoá route trong dashboard, hoặc `npx wrangler delete`.
routes = [
  { pattern = "%(zone)s%(base)s", zone_name = "%(zone)s" },
  { pattern = "%(zone)s%(base)s/*", zone_name = "%(zone)s" },
]
'''


def main() -> int:
    if not DIST.exists():
        print(f"Chưa có {DIST}. Chạy `python web/build.py` trước.", file=sys.stderr)
        return 1

    html = DIST.read_text(encoding="utf-8")
    (OUT / "src").mkdir(parents=True, exist_ok=True)

    worker = WORKER % {"base": BASE_PATH, "html": js_template_literal(html)}
    (OUT / "src" / "worker.js").write_text(worker, encoding="utf-8")
    (OUT / "wrangler.toml").write_text(
        TOML % {"name": WORKER_NAME, "zone": ZONE, "base": BASE_PATH}, encoding="utf-8")

    kb = (OUT / "src" / "worker.js").stat().st_size / 1024
    print(f"{OUT / 'src' / 'worker.js'}  {kb:.0f} KB")
    print(f"{OUT / 'wrangler.toml'}")
    print(f"\nSẽ phục vụ tại:  https://{ZONE}{BASE_PATH}")
    print("Deploy:          cd web/worker && npx wrangler deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

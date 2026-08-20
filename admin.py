#!/usr/bin/env python3
"""
admin.py — App quản trị dạng web chạy tại chỗ.

    python admin.py

Mở http://127.0.0.1:8777 trong trình duyệt. Điền thông tin SSH, bấm khảo sát,
đặt tên nhà mạng, xem trước, áp dụng. Xong là tải luôn bộ cài cho máy con.

Chỉ dùng thư viện chuẩn của Python cho phần web — không cần cài thêm gì ngoài
paramiko và PyYAML vốn đã cần cho phần lõi.

Máy chủ CHỈ lắng nghe trên 127.0.0.1, không mở ra mạng.
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import console                     # noqa: E402
console.setup()

from core import apply as apply_mod          # noqa: E402
from core import keys as keys_mod            # noqa: E402
from core import keysetup                    # noqa: E402
from core import plan as plan_mod            # noqa: E402
from core import survey as survey_mod        # noqa: E402
from core import version as ver              # noqa: E402
from core.model import Config, RouterAuth    # noqa: E402
from core.ros import ConnectError, Ros       # noqa: E402
from packager.build import build_packages    # noqa: E402

HOST, PORT = "127.0.0.1", 8777
DIST = ROOT / "dist"
PALETTE = ["#EE0033", "#0068B3", "#F37021", "#00897B", "#7B1FA2", "#5D4037"]


# --------------------------------------------------------------------------
# Nghiệp vụ — mỗi hàm nhận dict từ trình duyệt, trả về dict cho trình duyệt
# --------------------------------------------------------------------------

def _auth(d: dict) -> RouterAuth:
    return RouterAuth(
        host=(d.get("host") or "192.168.88.1").strip(),
        port=int(d.get("port") or 22),
        user=(d.get("user") or "admin").strip(),
        key=(d.get("key") or "").strip() or None,
        key_data=(d.get("key_data") or "").strip() or None,
        password=(d.get("password") or "").strip() or None,
    )


def api_keys(_body: dict) -> dict:
    """Danh sách SSH key tìm được trên máy, để người dùng bấm chọn."""
    extra = [ROOT / "keys", ROOT.parent / "keys"]
    return {"keys": keys_mod.discover(extra)}


KEYDIR = ROOT / "keys"


def api_genkey(body: dict) -> dict:
    """Tạo cặp khoá mới, kèm sẵn hướng dẫn nạp vào Winbox."""
    try:
        info = keys_mod.generate(
            KEYDIR,
            name=(body.get("name") or "wanselector").strip(),
            kind=(body.get("kind") or "rsa"),
            bits=int(body.get("bits") or 2048),
            comment=(body.get("comment") or "wanselector"),
        )
    except ValueError as e:
        return {"error": str(e)}

    user = (body.get("user") or "admin").strip()
    info["import_cmd"] = (f"/user ssh-keys import "
                          f"public-key-file={info['filename']} user={user}")
    return {"ok": True, "key": info}


def api_installkey(body: dict) -> dict:
    """
    Nạp thẳng khoá lên router bằng phiên đang có (thường là đăng nhập bằng mật khẩu).

    Có kiểm chứng: mở một kết nối MỚI bằng khoá vừa nạp. Không vào được thì gỡ
    khoá đó ra, trả router về đúng như trước.
    """
    auth = _auth(body)
    priv = (body.get("private") or "").strip()
    pub = (body.get("public") or "").strip()
    if not priv or not pub:
        return {"error": "Chưa có khoá để nạp. Bấm 'Tạo khoá mới' trước."}
    with Ros(auth, log_dir=ROOT / "logs") as ros:
        return keysetup.install(ros, auth, pub, priv)


def _config(d: dict) -> Config:
    return Config.from_dict({
        "router": d.get("router", {}),
        "tag": d.get("tag") or "WANSEL",
        "prefix": d.get("prefix") or "sel",
        "lan": d.get("lan", {}),
        "system_name": d.get("system_name") or "Chọn nhà mạng ra Internet",
        "failover": bool(d.get("failover")),
        "wans": d.get("wans", []),
        "profiles": d.get("profiles", []),
        "pinned": d.get("pinned", []),
    })


def api_survey(body: dict) -> dict:
    auth = _auth(body)
    with Ros(auth, log_dir=ROOT / "logs") as ros:
        f = survey_mod.survey(ros, lookup_isp=True)

    wans, profiles = [], []
    for i, p in enumerate(f.pppoe):
        wans.append({"interface": p.name,
                     "name": p.isp or p.name,
                     "color": PALETTE[i % len(PALETTE)],
                     "ip": p.local_address or "",
                     "running": p.running})
        profiles.append({"name": p.isp or p.name, "wans": [p.name], "on": True})

    if len(f.pppoe) > 1:
        profiles.append({"name": " + ".join(w["name"] for w in wans),
                         "wans": [w["interface"] for w in wans], "on": True})
    profiles.append({"name": "Mặc định", "wans": [], "on": True})

    free_mb = round(f.free_hdd_kib / 1024, 1)
    verdict = ver.assess(f.version, free_mb)
    verdict["disk"] = ver.disk_warning(free_mb, f.board)

    return {
        "router": {"identity": f.identity, "board": f.board, "version": f.version,
                   "free_mb": free_mb},
        "version": verdict,
        "lan": {"interface": f.lan_interface, "subnet": f.lan_subnet,
                "gateway": f.lan_gateway},
        "wans": wans,
        "profiles": profiles,
        "downstream": [d.as_dict() for d in f.downstream],
        "used_vrids": sorted(f.used_vrids),
    }


def _build_plan(body: dict):
    cfg = _config(body)
    ros = Ros(_auth(body.get("router", {})), log_dir=ROOT / "logs")
    ros.connect()
    facts = survey_mod.survey(ros, tag=cfg.tag, lookup_isp=False,
                              lan_interface=cfg.lan_interface)
    return cfg, facts, plan_mod.build(cfg, facts), ros


def api_plan(body: dict) -> dict:
    cfg, _facts, pl, ros = _build_plan(body)
    try:
        return {
            "ok": pl.ok,
            "errors": pl.errors,
            "warnings": pl.warnings,
            "commands": pl.commands,
            "allocations": [
                {"name": a.profile.name, "gateway": a.gateway,
                 "vrid": a.vrid, "table": a.table}
                for a in pl.allocations
            ],
        }
    finally:
        ros.close()


def api_apply(body: dict) -> dict:
    cfg, _facts, pl, ros = _build_plan(body)
    steps: list[dict] = []

    def step(name: str, ok: bool, detail: str = "") -> None:
        steps.append({"name": name, "ok": ok, "detail": detail})

    try:
        if not pl.ok:
            return {"ok": False, "steps": [], "errors": pl.errors}

        files = apply_mod.backup(ros, ROOT / "backup", label=f"pre-{cfg.tag}")
        step("Backup cấu hình hiện tại", True,
             ", ".join(f.name for f in files))
        backup_dir = str((ROOT / "backup").resolve())

        apply_mod.install_deadman(ros, cfg, pl, minutes=5)
        step("Bật dead-man switch (5 phút)", True,
             "mất kết nối giữa chừng thì router tự hoàn tác")

        apply_mod.run_plan(ros, pl)
        step("Áp dụng cấu hình", True,
             f"{len([c for c in pl.commands if not c.lstrip().startswith('#')])} lệnh")

        time.sleep(5)                       # chờ VRRP lên master
        res = apply_mod.verify(ros, cfg, pl)
        for name, ok, detail in res.checks:
            step(name, ok, detail)

        if not res.ok:
            step("KIỂM TRA KHÔNG ĐẠT — dead-man vẫn bật, router sẽ tự hoàn tác",
                 False, "hoặc bấm Hoàn tác ngay")
            return {"ok": False, "steps": steps}

        apply_mod.remove_deadman(ros, cfg)
        step("Gỡ dead-man switch", True, "cấu hình đã ổn định")
        return {"ok": True, "steps": steps, "backup_dir": backup_dir,
                "backup_files": [f.name for f in files]}
    finally:
        ros.close()


def api_rollback(body: dict) -> dict:
    cfg, _facts, pl, ros = _build_plan(body)
    try:
        apply_mod.rollback(ros, cfg, pl)
        return {"ok": True, "message": f"Đã gỡ mọi đối tượng mang nhãn [{cfg.tag}]."}
    finally:
        ros.close()


def api_package(body: dict) -> dict:
    cfg, _facts, pl, ros = _build_plan(body)
    try:
        if not pl.ok:
            return {"ok": False, "errors": pl.errors}
        data = pl.client_profiles(cfg)
    finally:
        ros.close()

    build_packages(data, DIST, client_dir=ROOT / "client")
    return {
        "ok": True,
        "profiles": data,
        "files": [
            {"name": "ChonNhaMang-Windows.zip", "label": "Bộ cài cho Windows"},
            {"name": "ChonNhaMang-macOS.zip", "label": "Bộ cài cho macOS / Linux"},
        ],
        "folder": str(DIST),
    }


ROUTES = {
    "/api/keys": api_keys,
    "/api/genkey": api_genkey,
    "/api/installkey": api_installkey,
    "/api/survey": api_survey,
    "/api/plan": api_plan,
    "/api/apply": api_apply,
    "/api/rollback": api_rollback,
    "/api/package": api_package,
}


# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "wanselector-admin"

    def log_message(self, fmt: str, *args) -> None:        # bớt ồn
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            html = (ROOT / "admin" / "index.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")

        if u.path == "/download-key":
            name = (parse_qs(u.query).get("f") or [""])[0]
            target = (KEYDIR / name).resolve()
            if (not str(target).startswith(str(KEYDIR.resolve()))
                    or target.suffix != ".pub" or not target.is_file()):
                return self._send(404, b"khong tim thay", "text/plain; charset=utf-8")
            blob = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return

        if u.path == "/download":
            name = (parse_qs(u.query).get("f") or [""])[0]
            target = (DIST / name).resolve()
            # chỉ cho tải file nằm trong dist/, chặn ../ đi ra ngoài
            if not str(target).startswith(str(DIST.resolve())) or not target.is_file():
                return self._send(404, b"khong tim thay", "text/plain; charset=utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()
            self.wfile.write(target.read_bytes())
            return

        self._send(404, b"404", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        u = urlparse(self.path)
        fn = ROUTES.get(u.path)
        if not fn:
            return self._send(404, b'{"error":"khong co API nay"}', "application/json")

        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            result = fn(body)
        except ConnectError as e:
            result = {"error": e.message, "hints": e.hints, "detail": e.detail}
        except SystemExit as e:
            result = {"error": str(e)}
        except Exception as e:                           # noqa: BLE001
            traceback.print_exc()
            result = {"error": f"{type(e).__name__}: {e}"}

        self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")


def main() -> None:
    DIST.mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"App quản trị đang chạy tại {url}")
    print("Nhấn Ctrl+C để dừng.")
    if "--no-open" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")


if __name__ == "__main__":
    main()

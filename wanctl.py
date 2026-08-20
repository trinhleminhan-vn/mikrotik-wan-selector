#!/usr/bin/env python3
"""
wanctl — Công cụ dòng lệnh dựng tính năng "chọn nhà mạng theo gateway" trên MikroTik.

    python wanctl.py survey   -r 192.168.88.1 -u admin -k key      # chỉ đọc
    python wanctl.py init     -r 192.168.88.1 -u admin -k key -o cauhinh.yaml
    python wanctl.py plan     -c cauhinh.yaml                      # xem trước, không ghi gì
    python wanctl.py apply    -c cauhinh.yaml --yes
    python wanctl.py verify   -c cauhinh.yaml
    python wanctl.py rollback -c cauhinh.yaml --yes
    python wanctl.py package  -c cauhinh.yaml -o dist/

Mọi lệnh ghi vào router đều đi qua: backup -> dead-man switch -> apply ->
verify -> gỡ dead-man. Không có cách nào bỏ qua chuỗi đó.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core import console                       # noqa: E402
console.setup()

from core import apply as apply_mod              # noqa: E402
from core import plan as plan_mod                # noqa: E402
from core import survey as survey_mod            # noqa: E402
from core.model import Config, RouterAuth        # noqa: E402
from core.ros import ConnectError, Ros           # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent
LOGS = ROOT / "logs"

C_OK, C_WARN, C_ERR, C_DIM, C_RST = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def head(t: str) -> None:
    print(f"\n\033[1m{t}\033[0m\n" + "─" * min(len(t), 70))


def load_config(path: str) -> Config:
    p = pathlib.Path(path)
    if not p.exists():
        raise SystemExit(f"Không tìm thấy file cấu hình: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Config.from_dict(raw)


def auth_from_args(a: argparse.Namespace) -> RouterAuth:
    return RouterAuth(host=a.router, port=a.port, user=a.user,
                      key=a.key, password=a.password)


def connect(auth: RouterAuth, echo: bool = False) -> Ros:
    r = Ros(auth, log_dir=LOGS, echo=echo)
    r.connect()
    return r


# ---------------------------------------------------------------- lệnh con

def cmd_survey(a: argparse.Namespace) -> int:
    auth = load_config(a.config).auth if a.config else auth_from_args(a)
    with connect(auth) as ros:
        facts = survey_mod.survey(ros, lookup_isp=not a.no_isp)
    head("HIỆN TRẠNG ROUTER")
    print(apply_mod.summarize_facts(facts))
    if facts.downstream_routers:
        print(f"\n{C_WARN}Router phụ dưới LAN:{C_RST}")
        for gw, dst in facts.downstream_routers.items():
            print(f"   {gw} phục vụ {dst}")
    return 0


def cmd_init(a: argparse.Namespace) -> int:
    auth = auth_from_args(a)
    with connect(auth) as ros:
        facts = survey_mod.survey(ros, lookup_isp=not a.no_isp)

    head("HIỆN TRẠNG ROUTER")
    print(apply_mod.summarize_facts(facts))

    wans, profiles = [], []
    palette = ["#EE0033", "#0068B3", "#F37021", "#00897B", "#7B1FA2", "#5D4037"]
    for i, p in enumerate(facts.pppoe):
        name = p.isp or p.name
        wans.append({"interface": p.name, "name": name, "color": palette[i % len(palette)]})
        profiles.append({"name": name, "wans": [p.name]})

    if len(facts.pppoe) > 1:                       # gợi ý sẵn một profile chia tải
        profiles.append({
            "name": " + ".join(w["name"] for w in wans),
            "wans": [w["interface"] for w in wans],
        })
    profiles.append({"name": "Mặc định"})           # không khai 'wans' = giữ nguyên hành vi cũ

    doc = {
        "router": {"host": auth.host, "port": auth.port, "user": auth.user,
                   **({"key": auth.key} if auth.key else {})},
        "tag": a.tag,
        "prefix": a.prefix,
        "lan": {"interface": facts.lan_interface,
                "subnet": facts.lan_subnet,
                "gateway": facts.lan_gateway},
        "system_name": "Chọn nhà mạng ra Internet",
        "failover": False,
        "wans": wans,
        "profiles": profiles,
    }

    out = pathlib.Path(a.output)
    out.write_text(
        "# File cấu hình sinh tự động từ hiện trạng router.\n"
        "# Sửa lại tên nhà mạng / thêm bớt profile rồi chạy:  wanctl.py plan -c "
        f"{out.name}\n\n" +
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    print(f"\n{C_OK}Đã ghi {out}{C_RST}  — mở ra sửa tên nhà mạng nếu cần, "
          f"rồi chạy: python wanctl.py plan -c {out.name}")
    return 0


def _build(a: argparse.Namespace) -> tuple[Config, object, plan_mod.Plan, Ros]:
    cfg = load_config(a.config)
    ros = connect(cfg.auth)
    facts = survey_mod.survey(ros, tag=cfg.tag, lookup_isp=False,
                              lan_interface=cfg.lan_interface)
    pl = plan_mod.build(cfg, facts)
    return cfg, facts, pl, ros


def _print_plan(cfg: Config, pl: plan_mod.Plan) -> None:
    head("CẤP PHÁT TÀI NGUYÊN")
    print(f"  {'Lựa chọn':<24} {'Gateway':<16} {'VRID':<6} {'Bảng định tuyến'}")
    for al in pl.allocations:
        vrid = str(al.vrid) if al.vrid else "-"
        print(f"  {al.profile.name:<24} {al.gateway or '-':<16} {vrid:<6} {al.table or '(giữ nguyên)'}")

    if pl.warnings:
        head("CẢNH BÁO")
        for w in pl.warnings:
            print(f"  {C_WARN}![{C_RST} {w}")

    if pl.errors:
        head("LỖI — chưa thể áp dụng")
        for e in pl.errors:
            print(f"  {C_ERR}x{C_RST} {e}")
        return

    head(f"{len([c for c in pl.commands if not c.lstrip().startswith('#')])} LỆNH SẼ CHẠY TRÊN ROUTER")
    for c in pl.commands:
        print(f"  {C_DIM}{c}{C_RST}" if c.lstrip().startswith("#") else f"  {c}")


def cmd_plan(a: argparse.Namespace) -> int:
    cfg, facts, pl, ros = _build(a)
    try:
        head("HIỆN TRẠNG ROUTER")
        print(apply_mod.summarize_facts(facts))          # type: ignore[arg-type]
        _print_plan(cfg, pl)
        print(f"\n{C_DIM}Đây là bản xem trước — chưa có gì được ghi lên router.{C_RST}")
        return 0 if pl.ok else 1
    finally:
        ros.close()


def cmd_apply(a: argparse.Namespace) -> int:
    cfg, facts, pl, ros = _build(a)
    try:
        head("HIỆN TRẠNG ROUTER")
        print(apply_mod.summarize_facts(facts))          # type: ignore[arg-type]
        _print_plan(cfg, pl)
        if not pl.ok:
            return 1
        if not a.yes:
            print(f"\n{C_WARN}Chưa chạy gì. Thêm --yes để thực sự áp dụng.{C_RST}")
            return 0

        head("1/5 BACKUP")
        for f in apply_mod.backup(ros, ROOT / "backup", label=f"pre-{cfg.tag}"):
            print(f"  {C_OK}✔{C_RST} {f}  ({f.stat().st_size} bytes)")

        head(f"2/5 DEAD-MAN SWITCH ({a.deadman} phút)")
        apply_mod.install_deadman(ros, cfg, pl, minutes=a.deadman)
        print(f"  {C_OK}✔{C_RST} Nếu mất kết nối giữa chừng, router tự hoàn tác sau {a.deadman} phút.")

        head("3/5 ÁP DỤNG")
        apply_mod.run_plan(ros, pl)
        print(f"  {C_OK}✔{C_RST} Đã chạy xong danh sách lệnh.")

        head("4/5 KIỂM TRA")
        import time
        time.sleep(5)                       # chờ VRRP chuyển sang master
        res = apply_mod.verify(ros, cfg, pl)
        for name, ok, detail in res.checks:
            icon = f"{C_OK}✔{C_RST}" if ok else f"{C_ERR}✗{C_RST}"
            print(f"  {icon} {name}" + (f"  {C_DIM}({detail}){C_RST}" if detail else ""))

        if not res.ok:
            print(f"\n{C_ERR}Kiểm tra KHÔNG đạt.{C_RST} Dead-man switch vẫn đang bật — "
                  f"router sẽ tự hoàn tác. Hoặc hoàn tác ngay:\n"
                  f"  python wanctl.py rollback -c {a.config} --yes")
            return 1

        head("5/5 GỠ DEAD-MAN SWITCH")
        apply_mod.remove_deadman(ros, cfg)
        print(f"  {C_OK}✔{C_RST} Xong. Script hoàn tác {cfg.tag}-ROLLBACK vẫn nằm trên router.")

        head("BƯỚC TIẾP THEO")
        print(f"  python wanctl.py package -c {a.config} -o dist/")
        return 0
    finally:
        ros.close()


def cmd_verify(a: argparse.Namespace) -> int:
    cfg, _facts, pl, ros = _build(a)
    try:
        res = apply_mod.verify(ros, cfg, pl)
        head("KIỂM TRA")
        for name, ok, detail in res.checks:
            icon = f"{C_OK}✔{C_RST}" if ok else f"{C_ERR}✗{C_RST}"
            print(f"  {icon} {name}" + (f"  {C_DIM}({detail}){C_RST}" if detail else ""))
        return 0 if res.ok else 1
    finally:
        ros.close()


def cmd_rollback(a: argparse.Namespace) -> int:
    cfg, _facts, pl, ros = _build(a)
    try:
        if not a.yes:
            head(f"SẼ GỠ MỌI ĐỐI TƯỢNG MANG NHÃN [{cfg.tag}]")
            for step in apply_mod.rollback_source(cfg, pl).replace('\\"', '"').split("; "):
                print(f"  {step}")
            print(f"\n{C_WARN}Chưa chạy gì. Thêm --yes để thực hiện.{C_RST}")
            return 0
        apply_mod.rollback(ros, cfg, pl)
        print(f"{C_OK}Đã gỡ xong.{C_RST} Muốn khôi phục 100% nguyên trạng thì dùng file "
              f"trong backup/ qua Winbox (Files → Restore).")
        return 0
    finally:
        ros.close()


def cmd_genkey(a: argparse.Namespace) -> int:
    from core import keys as keys_mod
    from core import keysetup

    try:
        info = keys_mod.generate(ROOT / "keys", name=a.name, kind=a.kind, bits=a.bits)
    except ValueError as e:
        print(f"{C_ERR}{e}{C_RST}")
        return 1

    head(f"ĐÃ TẠO KHOÁ {info['kind']}")
    print(f"  Khoá riêng     : {info['private']}   {C_DIM}(giữ trên máy, không đưa cho ai){C_RST}")
    print(f"  Khoá công khai : {info['public']}")

    if not a.install:
        head("NẠP VÀO ROUTER BẰNG WINBOX")
        print(f"  1. Winbox → Files → kéo thả {info['filename']} vào")
        print(f"  2. Winbox → New Terminal → dán:")
        print(f"     /user ssh-keys import public-key-file={info['filename']} user={a.user}")
        print(f"\n{C_DIM}  Hoặc thêm --install để tool tự nạp (cần mật khẩu router).{C_RST}")
        return 0

    head("TỰ NẠP LÊN ROUTER")
    auth = auth_from_args(a)
    with connect(auth) as ros:
        res = keysetup.install(ros, auth, info["public"], info["private"])
    for s in res.get("steps", []):
        print(f"  {C_OK}•{C_RST} {s}")
    icon = C_OK if res["ok"] else C_ERR
    print(f"\n  {icon}{res['message']}{C_RST}")
    return 0 if res["ok"] else 1


def cmd_package(a: argparse.Namespace) -> int:
    from packager.build import build_packages

    cfg, _facts, pl, ros = _build(a)
    try:
        if not pl.ok:
            _print_plan(cfg, pl)
            return 1
        data = pl.client_profiles(cfg)
    finally:
        ros.close()

    out = pathlib.Path(a.output)
    made = build_packages(data, out, client_dir=ROOT / "client")
    head("ĐÃ ĐÓNG GÓI")
    for f in made:
        print(f"  {C_OK}✔{C_RST} {f}  ({f.stat().st_size} bytes)")
    print(f"\n{C_DIM}profiles.json trong mỗi gói:{C_RST}")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- argparse

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="wanctl", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def conn_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("-r", "--router", default="192.168.88.1")
        p.add_argument("-p", "--port", type=int, default=22)
        p.add_argument("-u", "--user", default="admin")
        p.add_argument("-k", "--key", help="đường dẫn SSH private key (khuyến khích)")
        p.add_argument("-P", "--password", help="mật khẩu (chỉ khi không có key)")
        p.add_argument("--no-isp", action="store_true", help="bỏ qua tra cứu nhà mạng qua Internet")

    s = sub.add_parser("survey", help="đọc hiện trạng router (chỉ đọc)")
    conn_args(s)
    s.add_argument("-c", "--config", help="đọc thông tin kết nối từ file cấu hình")
    s.set_defaults(func=cmd_survey)

    s = sub.add_parser("init", help="sinh file cấu hình mẫu từ hiện trạng router")
    conn_args(s)
    s.add_argument("-o", "--output", default="cauhinh.yaml")
    s.add_argument("--tag", default="WANSEL")
    s.add_argument("--prefix", default="sel")
    s.set_defaults(func=cmd_init)

    for name, fn, helptxt in (
        ("plan", cmd_plan, "xem trước danh sách lệnh, không ghi gì"),
        ("verify", cmd_verify, "kiểm tra cấu hình đang chạy có đúng không"),
    ):
        s = sub.add_parser(name, help=helptxt)
        s.add_argument("-c", "--config", required=True)
        s.set_defaults(func=fn)

    s = sub.add_parser("apply", help="áp dụng lên router (có backup + dead-man)")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("--yes", action="store_true", help="xác nhận thực sự ghi lên router")
    s.add_argument("--deadman", type=int, default=5, help="số phút của dead-man switch")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("rollback", help="gỡ toàn bộ thay đổi theo nhãn")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_rollback)

    s = sub.add_parser("genkey", help="tạo khoá SSH mới (kèm hướng dẫn nạp vào router)")
    conn_args(s)
    s.add_argument("-n", "--name", default="wanselector", help="tên file khoá")
    s.add_argument("--kind", choices=["rsa", "ed25519"], default="rsa",
                   help="rsa hợp mọi đời RouterOS; ed25519 chỉ dùng được từ v7")
    s.add_argument("--bits", type=int, default=2048)
    s.add_argument("--install", action="store_true",
                   help="tự nạp luôn lên router (cần đăng nhập được bằng mật khẩu)")
    s.set_defaults(func=cmd_genkey)

    s = sub.add_parser("package", help="đóng gói app cho người dùng cuối")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("-o", "--output", default="dist")
    s.set_defaults(func=cmd_package)

    a = ap.parse_args()
    try:
        return a.func(a)
    except ConnectError as e:
        # Lỗi kết nối đã được diễn giải sẵn — in ra cho người dùng đọc được,
        # thay vì quăng nguyên traceback của paramiko.
        print(f"\n{C_ERR}{e.message}{C_RST}")
        for h in e.hints:
            print(f"  • {h}")
        if e.detail:
            print(f"{C_DIM}  (chi tiết kỹ thuật: {e.detail}){C_RST}")
        return 2
    except KeyboardInterrupt:
        print("\nĐã huỷ.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

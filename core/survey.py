"""
survey.py — Đọc hiện trạng router thành RouterFacts.

Toàn bộ module này CHỈ ĐỌC, không sửa gì trên router. Đây là nền tảng để bộ
sinh lệnh không phải hardcode bất cứ thứ gì: tên bridge, lớp mạng, VRID còn
trống, vị trí chèn mangle... đều lấy từ router thật.
"""
from __future__ import annotations

import ipaddress

from . import downstream as ds_mod
from . import isp as isp_mod
from .model import MangleRule, PppoeClient, RouterFacts
from .ros import Ros

# Interface không bao giờ được coi là LAN
WAN_LIKE_PREFIXES = ("pppoe", "l2tp", "pptp", "sstp", "ovpn", "wg", "gre", "eoip", "lte")

# Các action mangle cho thấy router đã có chính sách định tuyến riêng —
# rule của chúng ta phải đứng TRƯỚC chúng.
POLICY_ACTIONS = {"mark-connection", "mark-routing", "mark-packet", "route"}


def survey(ros: Ros, *, tag: str | None = None, lookup_isp: bool = True,
           lan_interface: str | None = None) -> RouterFacts:
    """
    `tag` là nhãn của chính tool này. Các đối tượng mang nhãn đó sẽ được LOẠI
    khỏi danh sách "đang bị chiếm" — nhờ vậy chạy lại lần hai không bị coi là
    xung đột với chính mình (tính idempotent).
    """
    f = RouterFacts()

    # ---------- thông tin máy ----------
    # /system resource là menu chỉ có 'get', không hỗ trợ 'print terse'.
    f.identity = ros.scalar(":put [/system identity get name]")
    f.version = ros.scalar(":put [/system resource get version]")
    f.board = ros.scalar(":put [/system resource get board-name]")
    try:
        f.free_hdd_kib = int(ros.scalar(":put [/system resource get free-hdd-space]")) / 1024
    except ValueError:
        f.free_hdd_kib = 0.0

    # ---------- interface ----------
    f.interfaces = {r["name"] for r in ros.rows("/interface") if r.get("name")}

    # ---------- địa chỉ IP ----------
    addrs = ros.rows("/ip address")
    for a in addrs:
        if _is_ours(a, tag):
            continue
        ip = (a.get("address") or "").split("/")[0]
        if ip:
            f.used_ips.add(ip)

    # ---------- PPPoE ----------
    pppoe_addr = {a.get("interface"): (a.get("address") or "").split("/")[0]
                  for a in addrs if a.get("interface", "").startswith("pppoe")}
    for p in ros.rows("/interface pppoe-client"):
        name = p.get("name", "")
        if not name:
            continue
        link = PppoeClient(
            name=name,
            interface=p.get("interface", ""),
            user=p.get("user", ""),
            running="R" in (p.get(".flags") or ""),
            local_address=pppoe_addr.get(name) or None,
        )
        if lookup_isp and link.local_address:
            guess, _raw = isp_mod.lookup(link.local_address)
            link.isp = guess
        f.pppoe.append(link)

    # ---------- LAN ----------
    f.lan_interface = lan_interface or _detect_lan_interface(ros, addrs)
    for a in addrs:
        if a.get("interface") != f.lan_interface:
            continue
        if "D" in (a.get(".flags") or ""):      # bỏ địa chỉ động
            continue
        if _is_ours(a, tag):                    # bỏ gateway do chính tool tạo
            continue
        cidr = a.get("address", "")
        if "/" not in cidr:
            continue
        prefix = int(cidr.split("/")[1])
        if prefix >= 31:                        # /32 không phải mạng LAN
            continue
        f.lan_gateway = cidr.split("/")[0]
        f.lan_subnet = str(ipaddress.ip_network(cidr, strict=False))
        break

    # ---------- VRRP ----------
    for v in ros.rows("/interface vrrp"):
        if _is_ours(v, tag):
            continue
        try:
            f.used_vrids.add(int(v.get("vrid", "0")))
        except ValueError:
            pass

    # ---------- bảng định tuyến (RouterOS v7) ----------
    for t in ros.rows("/routing table"):
        if t.get("name"):
            f.routing_tables.add(t["name"])

    # ---------- DHCP pool ----------
    for p in ros.rows("/ip pool"):
        if p.get("name"):
            f.dhcp_pools[p["name"]] = [r.strip() for r in (p.get("ranges") or "").split(",") if r.strip()]

    # ---------- mangle ----------
    for i, m in enumerate(ros.rows("/ip firewall mangle")):
        f.mangle.append(MangleRule(
            index=i,
            chain=m.get("chain", ""),
            action=m.get("action", ""),
            comment=m.get("comment", ""),
            raw=m,
        ))

    # ---------- router phụ nằm dưới LAN ----------
    if f.lan_subnet:
        f.downstream = ds_mod.detect(ros, f.lan_subnet, f.lan_gateway)
        net = ipaddress.ip_network(f.lan_subnet)
        for r in ros.rows("/ip route"):
            gw = r.get("gateway", "")
            dst = r.get("dst-address", "")
            if not gw or not dst or dst == "0.0.0.0/0":
                continue
            try:
                if ipaddress.ip_address(gw) in net:
                    f.downstream_routers[gw] = dst
            except ValueError:
                continue

    return f


# --------------------------------------------------------------------------

def _is_ours(row: dict[str, str], tag: str | None) -> bool:
    return bool(tag) and f"[{tag}]" in (row.get("comment") or "")


def _detect_lan_interface(ros: Ros, addrs: list[dict[str, str]]) -> str:
    """
    Ưu tiên interface mà DHCP server đang phục vụ — đó gần như luôn là LAN
    thật sự, đáng tin hơn nhiều so với đoán theo tên bridge.
    """
    for s in ros.rows("/ip dhcp-server"):
        if "X" in (s.get(".flags") or ""):
            continue
        if s.get("interface"):
            return s["interface"]

    bridges = {b["name"] for b in ros.rows("/interface bridge") if b.get("name")}
    for a in addrs:
        iface = a.get("interface", "")
        if iface in bridges and "D" not in (a.get(".flags") or ""):
            return iface

    for a in addrs:
        iface = a.get("interface", "")
        if "D" in (a.get(".flags") or ""):
            continue
        if any(iface.startswith(p) for p in WAN_LIKE_PREFIXES):
            continue
        try:
            if ipaddress.ip_address(a.get("address", "0.0.0.0").split("/")[0]).is_private:
                return iface
        except ValueError:
            continue
    return ""


def mangle_insert_index(facts: RouterFacts, tag: str) -> int:
    """
    Vị trí nên chèn rule của chúng ta: ngay TRƯỚC rule prerouting đầu tiên
    của người khác có đánh dấu (mark-*). Như vậy lựa chọn của người dùng luôn
    thắng cơ chế cân bằng tải sẵn có, mà các rule accept đứng trước (thường là
    miễn trừ LAN↔LAN) vẫn được tôn trọng.

    Không tìm thấy rule nào -> chèn vào cuối.
    """
    ours = f"[{tag}]"
    for r in facts.mangle:
        if ours in r.comment:
            continue
        if r.chain == "prerouting" and r.action in POLICY_ACTIONS:
            return r.index
    return len(facts.mangle)

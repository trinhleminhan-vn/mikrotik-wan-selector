"""
downstream.py — Phát hiện router phụ nằm dưới LAN.

Vì sao quan trọng: nếu một máy nằm SAU một router phụ có NAT, thì đổi gateway
trên máy đó hoàn toàn vô nghĩa — gói chỉ đi tới router phụ rồi bị NAT, khi lên
tới MikroTik thì mọi máy đều mang chung một địa chỉ nguồn. Không cách nào phân
biệt được máy nào chọn nhà mạng nào.

Đây là giới hạn vật lý của thiết kế, không phải lỗi có thể vá. Thứ tool làm được
là **phát hiện sớm và nói thẳng**, thay vì để admin cài xong rồi mới phát hiện
nửa số máy không dùng được.

Ba tín hiệu, độ tin cậy giảm dần:

  1. Static route trỏ vào một IP trong LAN  -> chắc chắn là router
  2. Rất nhiều kết nối đồng thời từ một IP  -> gần như chắc chắn đang NAT hộ máy khác
  3. Tên máy / class-id giống thiết bị mạng -> chỉ là nghi ngờ
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from .ros import Ros

# Ngưỡng số kết nối đồng thời để nghi một IP đang NAT hộ nhiều máy.
# Một PC dùng bình thường hiếm khi vượt 150; router NAT cho chục máy thì vượt xa.
BUSY_CONN = 150

VENDOR_HINTS = (
    "googlewifi", "google nest", "nest wifi", "tp-link", "tplink", "archer",
    "asus", "rt-ac", "rt-ax", "linksys", "netgear", "orbi", "dd-wrt", "openwrt",
    "tenda", "totolink", "mercusys", "xiaomi", "redmi", "draytek", "unifi",
    "deco", "eero", "huawei", "zte", "gpon", "igate", "router", "gateway",
)


@dataclass
class Downstream:
    address: str
    mac: str = ""
    hostname: str = ""
    serves: list[str] = field(default_factory=list)   # các mạng nó phục vụ
    conn_count: int = 0
    reasons: list[str] = field(default_factory=list)
    confidence: str = "nghi ngờ"                       # 'chắc chắn' | 'rất có thể' | 'nghi ngờ'
    # 'nat'   : đang NAT hộ nhiều máy -> máy phía sau KHÔNG chọn được nhà mạng
    # 'route' : chỉ là cổng ra cho một mạng khác (VPN, site-to-site) -> vô hại
    kind: str = "route"

    def as_dict(self) -> dict:
        return {
            "address": self.address, "mac": self.mac, "hostname": self.hostname,
            "serves": self.serves, "conn_count": self.conn_count,
            "reasons": self.reasons, "confidence": self.confidence, "kind": self.kind,
        }


def detect(ros: Ros, lan_subnet: str, lan_gateway: str = "") -> list[Downstream]:
    """Quét router phụ trong lớp LAN. Chỉ đọc, không sửa gì."""
    if not lan_subnet:
        return []
    net = ipaddress.ip_network(lan_subnet)
    found: dict[str, Downstream] = {}

    def get(ip: str) -> Downstream:
        if ip not in found:
            found[ip] = Downstream(address=ip)
        return found[ip]

    def in_lan(ip: str) -> bool:
        try:
            return ipaddress.ip_address(ip) in net and ip != lan_gateway
        except ValueError:
            return False

    # ---- 1. static route trỏ vào một IP trong LAN: chắc chắn là router ----
    for r in ros.rows("/ip route"):
        gw, dst = r.get("gateway", ""), r.get("dst-address", "")
        if not gw or not dst or dst == "0.0.0.0/0" or not in_lan(gw):
            continue
        d = get(gw)
        d.serves.append(dst)
        d.confidence = "chắc chắn"
        if "có route tĩnh trỏ qua nó" not in " ".join(d.reasons):
            d.reasons.append(f"router có route tĩnh trỏ qua nó (tới {dst})")

    # ---- 2. quá nhiều kết nối đồng thời từ một IP: nhiều khả năng đang NAT ----
    counts: dict[str, int] = {}
    for c in ros.rows("/ip firewall connection"):
        src = (c.get("src-address") or "").split(":")[0]
        if in_lan(src):
            counts[src] = counts.get(src, 0) + 1
    for ip, n in counts.items():
        if n < BUSY_CONN:
            continue
        d = get(ip)
        d.conn_count = n
        d.reasons.append(f"đang giữ {n} kết nối cùng lúc — nhiều hơn một máy đơn lẻ rất nhiều")
        d.kind = "nat"          # dấu hiệu rõ nhất của việc NAT hộ nhiều máy
        if d.confidence != "chắc chắn":
            d.confidence = "rất có thể"

    # ---- 3. tên máy / class-id nghe như thiết bị mạng: chỉ là nghi ngờ ----
    for lease in ros.rows("/ip dhcp-server lease"):
        ip = lease.get("address", "")
        if not in_lan(ip):
            continue
        blob = " ".join([lease.get("host-name", ""), lease.get("class-id", "")]).lower()
        hit = next((k for k in VENDOR_HINTS if k in blob), None)
        if not hit:
            continue
        d = get(ip)
        d.reasons.append(f"tên/định danh DHCP chứa {hit!r}")
        if hit not in ("router", "gateway"):
            d.kind = "nat"

    # ---- bổ sung MAC và tên máy cho những cái đã tìm ra ----
    for a in ros.rows("/ip arp"):
        ip = a.get("address", "")
        if ip in found and a.get("mac-address"):
            found[ip].mac = a["mac-address"]
    for lease in ros.rows("/ip dhcp-server lease"):
        ip = lease.get("address", "")
        if ip in found:
            found[ip].hostname = lease.get("host-name", "") or found[ip].hostname

    order = {"chắc chắn": 0, "rất có thể": 1, "nghi ngờ": 2}
    return sorted(found.values(), key=lambda d: (order[d.confidence], d.address))


def advice(items: list[Downstream]) -> list[str]:
    """
    Lời khuyên xử lý, viết cho người vận hành đọc.

    Chỉ cảnh báo về thiết bị đang NAT hộ máy khác. Một cổng ra cho mạng VPN
    (kiểu NAS làm site-to-site) cũng là "router phụ" về mặt kỹ thuật, nhưng
    không có máy con nào nằm sau nó cần chọn nhà mạng — cảnh báo là gây nhiễu.
    """
    items = [d for d in items if d.kind == "nat"]
    if not items:
        return []
    names = ", ".join(f"{d.address}" + (f" ({d.hostname})" if d.hostname else "") for d in items)
    return [
        f"Phát hiện thiết bị nhiều khả năng là router phụ: {names}.",
        "Máy nằm SAU các thiết bị đó KHÔNG đổi được nhà mạng bằng cách đổi gateway — "
        "gói bị NAT lần hai nên MikroTik thấy mọi máy đều chung một địa chỉ nguồn.",
        "Cách 1 (triệt để): chuyển router phụ sang chế độ Bridge / Access Point. "
        "Khi đó máy con nhận IP thẳng từ MikroTik và dùng được như máy cắm trực tiếp.",
        "Cách 2 (nhanh): ghim cả cụm phía sau vào một nhà mạng cố định — dùng mục "
        "'Ghim theo địa chỉ' trong file cấu hình. Không chọn được theo từng máy, "
        "nhưng ít nhất kiểm soát được cả cụm.",
    ]

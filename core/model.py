"""
model.py — Các kiểu dữ liệu dùng chung.

Ý tưởng cốt lõi: KHÔNG hardcode gì về router. Admin mô tả mong muốn trong
`Config`, tool đọc hiện trạng router vào `RouterFacts`, rồi bộ sinh lệnh
(core/plan.py) ghép hai thứ đó lại thành danh sách lệnh RouterOS cụ thể.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------
# Mô tả mong muốn của admin (đọc từ file YAML)
# --------------------------------------------------------------------------

@dataclass
class RouterAuth:
    host: str = "192.168.88.1"
    port: int = 22
    user: str = "admin"
    key: str | None = None          # đường dẫn private key (khuyến khích)
    key_data: str | None = None     # nội dung key, khi người dùng chọn file
                                    # bằng hộp thoại của trình duyệt (không biết đường dẫn)
    password: str | None = None     # chỉ dùng khi không có key

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RouterAuth:
        return cls(
            host=str(d.get("host", "192.168.88.1")),
            port=int(d.get("port", 22)),
            user=str(d.get("user", "admin")),
            key=d.get("key"),
            key_data=d.get("key_data"),
            password=d.get("password"),
        )


@dataclass
class WanLink:
    """Một đường ra Internet. `interface` là tên interface thật trên router."""
    interface: str
    name: str                       # tên hiển thị: Viettel, VNPT, FPT...
    color: str = "#546E7A"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WanLink:
        if not d.get("interface"):
            raise ValueError("mỗi mục trong 'wans' phải có 'interface'")
        return cls(
            interface=str(d["interface"]),
            name=str(d.get("name") or d["interface"]),
            color=str(d.get("color", "#546E7A")),
        )


@dataclass
class Profile:
    """
    Một lựa chọn mà người dùng cuối sẽ thấy thành một cái nút.

    `wans` rỗng  -> profile "mặc định": không đánh dấu gì, giữ nguyên hành vi
                    sẵn có của router (PCC/ECMP/failover mà admin đã cấu hình).
    `wans` 1 phần tử -> đi riêng một nhà mạng.
    `wans` nhiều phần tử -> ECMP chia tải giữa các đường được chọn.
    """
    name: str
    wans: list[str] = field(default_factory=list)
    gateway: str | None = None      # để trống -> tool tự cấp phát
    color: str | None = None

    @property
    def is_default(self) -> bool:
        return not self.wans

    def slug(self) -> str:
        s = re.sub(r"[^a-zA-Z0-9]+", "-", self.name.strip().lower()).strip("-")
        return s or "profile"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Profile:
        if not d.get("name"):
            raise ValueError("mỗi mục trong 'profiles' phải có 'name'")
        wans = d.get("wans") or []
        if isinstance(wans, str):
            wans = [wans]
        return cls(
            name=str(d["name"]),
            wans=[str(w) for w in wans],
            gateway=d.get("gateway"),
            color=d.get("color"),
        )


@dataclass
class PinnedGroup:
    """
    Ghim một nhóm địa chỉ nguồn vào một lựa chọn cố định.

    Dùng cho những máy KHÔNG tự chọn được: nằm sau router phụ có NAT, hoặc là
    thiết bị không cài được app (camera, máy in, điện thoại). Định tuyến theo
    ĐỊA CHỈ NGUỒN thay vì theo gateway.
    """
    name: str
    match: list[str]                # IP hoặc dải CIDR
    profile: str                    # tên profile đã khai trong 'profiles'

    def slug(self) -> str:
        s = re.sub(r"[^a-zA-Z0-9]+", "-", self.name.strip().lower()).strip("-")
        return s or "pin"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PinnedGroup:
        m = d.get("match") or []
        if isinstance(m, str):
            m = [m]
        return cls(name=str(d.get("name") or "nhóm"),
                   match=[str(x).strip() for x in m if str(x).strip()],
                   profile=str(d.get("profile") or ""))


@dataclass
class Config:
    auth: RouterAuth
    wans: list[WanLink]
    profiles: list[Profile]
    pinned: list[PinnedGroup] = field(default_factory=list)

    tag: str = "WANSEL"             # nhãn gắn vào comment, dùng để rollback
    prefix: str = "sel"             # tiền tố đặt tên interface/bảng định tuyến

    lan_interface: str | None = None    # để trống -> tự dò
    lan_subnet: str | None = None       # để trống -> tự dò
    lan_gateway: str | None = None      # để trống -> tự dò (IP router trên LAN)

    gateway_pool: str | None = None     # "192.168.88.2-192.168.88.9"; trống -> tự chọn
    vrid_base: int = 200

    failover: bool = False          # bật route dự phòng chéo khi WAN chính chết
    check_gateway: bool = True      # check-gateway=ping trên route chính

    # Dải địa chỉ KHÔNG áp định tuyến chọn WAN (nội bộ, multicast...)
    bypass_networks: list[str] = field(default_factory=lambda: [
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "100.64.0.0/10", "169.254.0.0/16", "224.0.0.0/4",
    ])

    system_name: str = "Chọn nhà mạng ra Internet"

    # ---- kiểm tra tính hợp lệ ------------------------------------------
    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.wans:
            errs.append("Chưa khai báo đường WAN nào trong 'wans'.")
        if not self.profiles:
            errs.append("Chưa khai báo profile nào trong 'profiles'.")

        known = {w.interface for w in self.wans}
        for p in self.profiles:
            for w in p.wans:
                if w not in known:
                    errs.append(
                        f"Profile {p.name!r} tham chiếu WAN {w!r} không có trong 'wans'."
                    )

        names = [p.name for p in self.profiles]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            errs.append(f"Tên profile bị trùng: {', '.join(sorted(dup))}")

        if sum(1 for p in self.profiles if p.is_default) > 1:
            errs.append("Chỉ được có tối đa MỘT profile mặc định (profile không khai 'wans').")

        by_name = {p.name: p for p in self.profiles}
        for g in self.pinned:
            if not g.match:
                errs.append(f"Nhóm ghim {g.name!r} chưa khai địa chỉ nào trong 'match'.")
            for m in g.match:
                try:
                    ipaddress.ip_network(m, strict=False)
                except ValueError:
                    errs.append(f"Nhóm ghim {g.name!r}: {m!r} không phải IP/dải hợp lệ.")
            target = by_name.get(g.profile)
            if target is None:
                errs.append(f"Nhóm ghim {g.name!r} trỏ tới lựa chọn {g.profile!r} không tồn tại.")
            elif target.is_default:
                errs.append(
                    f"Nhóm ghim {g.name!r} đang ghim vào lựa chọn mặc định — không cần thiết, "
                    f"vì không ghim thì chúng đã đi theo mặc định rồi.")

        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.tag):
            errs.append("'tag' chỉ được gồm chữ, số, gạch ngang và gạch dưới.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.prefix):
            errs.append("'prefix' chỉ được gồm chữ, số, gạch ngang và gạch dưới.")

        for p in self.profiles:
            if p.gateway:
                try:
                    ipaddress.IPv4Address(p.gateway)
                except ValueError:
                    errs.append(f"Profile {p.name!r}: gateway {p.gateway!r} không phải IPv4 hợp lệ.")

        return errs

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        lan = d.get("lan") or {}
        return cls(
            auth=RouterAuth.from_dict(d.get("router") or {}),
            wans=[WanLink.from_dict(x) for x in (d.get("wans") or [])],
            profiles=[Profile.from_dict(x) for x in (d.get("profiles") or [])],
            pinned=[PinnedGroup.from_dict(x) for x in (d.get("pinned") or [])],
            tag=str(d.get("tag", "WANSEL")),
            prefix=str(d.get("prefix", "sel")),
            lan_interface=lan.get("interface"),
            lan_subnet=lan.get("subnet"),
            lan_gateway=lan.get("gateway"),
            gateway_pool=d.get("gateway_pool"),
            vrid_base=int(d.get("vrid_base", 200)),
            failover=bool(d.get("failover", False)),
            check_gateway=bool(d.get("check_gateway", True)),
            bypass_networks=list(d.get("bypass_networks") or cls.__dataclass_fields__[
                "bypass_networks"].default_factory()),
            system_name=str(d.get("system_name", "Chọn nhà mạng ra Internet")),
        )


# --------------------------------------------------------------------------
# Hiện trạng router (do core/survey.py đọc về)
# --------------------------------------------------------------------------

@dataclass
class PppoeClient:
    name: str
    interface: str
    user: str
    running: bool
    local_address: str | None = None     # IP public đang dùng
    isp: str | None = None               # tra được từ RDAP


@dataclass
class MangleRule:
    index: int
    chain: str
    action: str
    comment: str
    raw: dict[str, str]


@dataclass
class RouterFacts:
    identity: str = ""
    version: str = ""
    board: str = ""
    free_hdd_kib: float = 0.0

    lan_interface: str = ""
    lan_gateway: str = ""            # IP của router trên LAN
    lan_subnet: str = ""             # dạng CIDR

    pppoe: list[PppoeClient] = field(default_factory=list)
    used_ips: set[str] = field(default_factory=set)
    used_vrids: set[int] = field(default_factory=set)
    routing_tables: set[str] = field(default_factory=set)
    interfaces: set[str] = field(default_factory=set)
    mangle: list[MangleRule] = field(default_factory=list)
    # tên pool -> danh sách dải "a-b"
    dhcp_pools: dict[str, list[str]] = field(default_factory=dict)
    # các IP LAN đang là gateway của static route -> nghi ngờ có router phụ
    downstream_routers: dict[str, str] = field(default_factory=dict)
    # kết quả dò chi tiết của core/downstream.py (list[Downstream])
    downstream: list = field(default_factory=list)

    @property
    def major_version(self) -> int:
        m = re.match(r"(\d+)", self.version or "")
        return int(m.group(1)) if m else 0

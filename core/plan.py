"""
plan.py — Ghép Config (mong muốn) + RouterFacts (hiện trạng) thành danh sách
lệnh RouterOS cụ thể. Module này KHÔNG kết nối router và KHÔNG chạy gì cả.

Tách riêng như vậy để admin luôn xem trước được chính xác từng dòng lệnh sẽ
chạy. Một tool tự động ghi vào router production mà không cho xem diff thì
không ai dám dùng lần thứ hai.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from . import version as ver
from .model import Config, Profile, RouterFacts
from .ros import q
from .survey import POLICY_ACTIONS

MAX_VRID = 255


@dataclass
class Allocation:
    """Kết quả cấp phát tài nguyên cho một profile."""
    profile: Profile
    gateway: str | None = None      # profile mặc định thì dùng IP LAN của router
    vrid: int | None = None
    vrrp_name: str | None = None
    table: str | None = None
    conn_mark: str | None = None


@dataclass
class Plan:
    commands: list[str] = field(default_factory=list)
    allocations: list[Allocation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pool_changes: list[tuple[str, str, str]] = field(default_factory=list)  # (pool, cũ, mới)
    dialect: str = "v7"          # 'v7' | 'v6' — cú pháp tạo bảng định tuyến

    @property
    def ok(self) -> bool:
        return not self.errors

    def client_profiles(self, cfg: Config) -> dict:
        """Dữ liệu để đóng gói cho app phía người dùng cuối."""
        return {
            "system_name": cfg.system_name,
            "lan_subnet": cfg.lan_subnet or "",
            "profiles": [
                {
                    "name": a.profile.name,
                    "gateway": a.gateway,
                    "color": a.profile.color or _default_color(a.profile, cfg),
                    "detail": _detail(a.profile, cfg),
                    "is_default": a.profile.is_default,
                }
                for a in self.allocations if a.gateway
            ],
        }


# --------------------------------------------------------------------------

def build(cfg: Config, facts: RouterFacts) -> Plan:
    p = Plan()
    tag = cfg.tag
    mark = f"[{tag}]"

    # ---------- kiểm tra tiền đề ----------
    p.errors.extend(cfg.validate())

    verdict = ver.assess(facts.version, facts.free_hdd_kib / 1024)
    p.dialect = verdict["dialect"]
    if verdict["level"] == "blocked":
        p.errors.append(verdict["title"] + " " + " ".join(verdict["hints"]))

    lan_if = cfg.lan_interface or facts.lan_interface
    lan_gw = cfg.lan_gateway or facts.lan_gateway
    lan_net = cfg.lan_subnet or facts.lan_subnet
    if not (lan_if and lan_gw and lan_net):
        p.errors.append(
            "Không xác định được LAN của router. Hãy khai báo tay trong mục 'lan' "
            "của file cấu hình (interface / subnet / gateway)."
        )
        return p
    cfg.lan_interface, cfg.lan_gateway, cfg.lan_subnet = lan_if, lan_gw, lan_net

    for w in cfg.wans:
        if facts.interfaces and w.interface not in facts.interfaces:
            p.errors.append(f"Router không có interface {w.interface!r} (khai trong 'wans').")

    if p.errors:
        return p

    # ---------- cấp phát tài nguyên ----------
    _allocate(cfg, facts, p)
    if p.errors:
        return p

    # ---------- sinh lệnh ----------
    bypass_list = f"{tag}-BYPASS"
    c = p.commands

    # Đánh số mục tự động: có mục bị bỏ qua (vd không cần thu hẹp DHCP pool)
    # thì số thứ tự vẫn liền mạch, người đọc diff không bị hụt số.
    _sec_no = [0]

    def sec(text: str) -> str:
        _sec_no[0] += 1
        return f"# ---------- {_sec_no[0]}. {text} ----------"

    c.append(sec(f"Dọn các đối tượng cũ mang nhãn {mark} (để chạy lại được nhiều lần)"))
    c.append(f'/ip firewall mangle remove [find comment~{q(tag)}]')
    c.append(f'/ip firewall address-list remove [find comment~{q(tag)}]')
    c.append(f'/ip address remove [find comment~{q(tag)}]')
    c.append(f'/interface vrrp remove [find comment~{q(tag)}]')
    c.append(f'/ip route remove [find comment~{q(tag)}]')
    if p.dialect == "v7":
        c.append(f'/routing table remove [find comment~{q(tag)}]')

    c.append(sec("Dải địa chỉ nội bộ: KHÔNG áp định tuyến chọn WAN"))
    for netw in cfg.bypass_networks:
        c.append(f'/ip firewall address-list add list={bypass_list} address={netw} '
                 f'comment={q(f"{mark} dich noi bo")}')

    if p.pool_changes:
        c.append(sec("Thu hẹp DHCP pool để IP gateway không bị cấp cho máy khác"))
        for name, _old, new in p.pool_changes:
            c.append(f'/ip pool set [find name={q(name)}] ranges={new}')

    if p.dialect == "v7":
        c.append(sec("Bảng định tuyến riêng cho từng lựa chọn"))
        for a in p.allocations:
            if a.table:
                c.append(f'/routing table add fib name={a.table} '
                         f'comment={q(f"{mark} {a.profile.name}")}')
    else:
        c.append(sec("RouterOS v6: không có menu /routing table, bảng sinh ra từ routing-mark"))

    c.append(sec("Route mặc định của từng bảng"))
    chk = " check-gateway=ping" if cfg.check_gateway else ""
    rt = "routing-table" if p.dialect == "v7" else "routing-mark"
    for a in p.allocations:
        if not a.table:
            continue
        gws = ",".join(a.profile.wans)
        c.append(f'/ip route add dst-address=0.0.0.0/0 gateway={gws} {rt}={a.table} '
                 f'distance=1{chk} comment={q(f"{mark} {a.profile.name}")}')

    # route dự phòng: dùng các WAN KHÔNG thuộc profile, distance cao hơn
    others_exist = any(len(a.profile.wans) < len(cfg.wans) for a in p.allocations if a.table)
    if others_exist:
        state = "" if cfg.failover else " disabled=yes"
        label = "dang BAT" if cfg.failover else "dang TAT"
        c.append(sec(f"Route dự phòng khi WAN đã chọn bị đứt ({label})"))
        for a in p.allocations:
            if not a.table:
                continue
            rest = [w.interface for w in cfg.wans if w.interface not in a.profile.wans]
            if not rest:
                continue
            c.append(f'/ip route add dst-address=0.0.0.0/0 gateway={",".join(rest)} '
                     f'{rt}={a.table} distance=10{state} '
                     f'comment={q(f"{mark} du phong cho {a.profile.name}")}')

    c.append(sec("Gateway ảo: mỗi lựa chọn một địa chỉ MAC riêng (VRRP)"))
    for a in p.allocations:
        if not a.vrrp_name:
            continue
        c.append(f'/interface vrrp add name={a.vrrp_name} interface={lan_if} vrid={a.vrid} '
                 f'priority=254 comment={q(f"{mark} gateway {a.gateway} = {a.profile.name}")}')

    c.append(sec("Gán IP cho gateway ảo — BẮT BUỘC /32"))
    c.append(f"#   Dùng /24 sẽ sinh connected route ECMP trùng subnet LAN, làm phình bảng ARP.")
    for a in p.allocations:
        if not a.vrrp_name:
            continue
        c.append(f'/ip address add address={a.gateway}/32 network={a.gateway} '
                 f'interface={a.vrrp_name} comment={q(f"{mark} gateway {a.profile.name}")}')

    idx = _mangle_index(facts, tag)
    c.append(sec(f"Mangle: chèn tại vị trí {idx}, trước mọi rule đánh dấu sẵn có"))
    c.append(f"#   in-interface trên rule mark-routing là BẮT BUỘC: thiếu nó thì gói TRẢ LỜI")
    c.append(f"#   từ Internet cũng bị gán routing-mark và bị đẩy ngược ra WAN thay vì về máy.")
    step = 0
    for a in p.allocations:
        if not a.vrrp_name:
            continue
        c.append(
            f'/ip firewall mangle add place-before={idx + step} chain=prerouting '
            f'in-interface={a.vrrp_name} connection-mark=no-mark dst-address-type=!local '
            f'dst-address-list=!{bypass_list} action=mark-connection '
            f'new-connection-mark={a.conn_mark} passthrough=yes '
            f'comment={q(f"{mark} {a.profile.name} 1/2 - danh dau connection")}'
        )
        step += 1
        via = "+".join(a.profile.wans)
        c.append(
            f'/ip firewall mangle add place-before={idx + step} chain=prerouting '
            f'in-interface={a.vrrp_name} connection-mark={a.conn_mark} '
            f'action=mark-routing new-routing-mark={a.table} passthrough=yes '
            f'comment={q(mark + " " + a.profile.name + " 2/2 - dinh tuyen ra " + via)}'
        )
        step += 1

    # ---------- ghim theo địa chỉ nguồn ----------
    # Dành cho máy KHÔNG tự chọn được: nằm sau router phụ có NAT, hoặc thiết bị
    # không cài được app (camera, máy in, điện thoại).
    #
    # Đặt SAU các rule theo gateway, và cùng đòi connection-mark=no-mark, nên máy
    # cắm thẳng đã tự chọn gateway thì lựa chọn của nó thắng.
    #
    # Rule mark-routing lọc theo src-address-list chứ không phải chỉ connection-mark:
    # gói trả lời từ Internet có địa chỉ nguồn là máy chủ ngoài nên không khớp —
    # đúng cái bẫy đã từng làm mất mạng toàn bộ.
    if cfg.pinned:
        by_name = {a.profile.name: a for a in p.allocations}
        c.append(sec("Ghim theo địa chỉ nguồn (máy không tự chọn được)"))
        for g in cfg.pinned:
            a = by_name.get(g.profile)
            if not a or not a.table:
                continue
            lst = f"{tag}-PIN-{g.slug()}"
            cm = f"{tag}-pin-{g.slug()}"
            for m in g.match:
                c.append(f'/ip firewall address-list add list={lst} address={m} '
                         f'comment={q(mark + " ghim: " + g.name)}')
            c.append(
                f'/ip firewall mangle add place-before={idx + step} chain=prerouting '
                f'src-address-list={lst} connection-mark=no-mark dst-address-type=!local '
                f'dst-address-list=!{bypass_list} action=mark-connection '
                f'new-connection-mark={cm} passthrough=yes '
                f'comment={q(mark + " ghim " + g.name + " 1/2")}'
            )
            step += 1
            c.append(
                f'/ip firewall mangle add place-before={idx + step} chain=prerouting '
                f'src-address-list={lst} connection-mark={cm} '
                f'action=mark-routing new-routing-mark={a.table} passthrough=yes '
                f'comment={q(mark + " ghim " + g.name + " 2/2 -> " + g.profile)}'
            )
            step += 1

    p.warnings.extend(doctor(cfg, facts, p))
    return p


# --------------------------------------------------------------------------

def _allocate(cfg: Config, facts: RouterFacts, p: Plan) -> None:
    used_ips = set(facts.used_ips)
    used_vrids = set(facts.used_vrids)
    # Interface và routing table là hai không gian tên KHÁC nhau trong RouterOS,
    # tách riêng để tên của cùng một lựa chọn khớp nhau, dễ đọc khi soi cấu hình.
    used_ifaces = set(facts.interfaces)
    used_tables = set(facts.routing_tables)

    candidates = _gateway_candidates(cfg, facts, used_ips)
    need = [pr for pr in cfg.profiles if not pr.is_default and not pr.gateway]
    if len(candidates) < len(need):
        p.errors.append(
            f"Không đủ địa chỉ IP trống cho {len(need)} lựa chọn "
            f"(chỉ tìm được {len(candidates)}). Hãy khai 'gateway_pool' rộng hơn "
            f"hoặc đặt 'gateway' thủ công cho từng profile."
        )
        return

    chosen: list[str] = []
    vrid = max(cfg.vrid_base, 1)

    for pr in cfg.profiles:
        a = Allocation(profile=pr)

        if pr.is_default:
            a.gateway = cfg.lan_gateway          # không tạo gì thêm trên router
            p.allocations.append(a)
            continue

        if pr.gateway:
            a.gateway = pr.gateway
            if pr.gateway in used_ips:
                p.errors.append(
                    f"Profile {pr.name!r}: IP {pr.gateway} đã có thiết bị/địa chỉ khác dùng."
                )
        else:
            a.gateway = candidates.pop(0)
        used_ips.add(a.gateway)
        chosen.append(a.gateway)

        while vrid in used_vrids and vrid <= MAX_VRID:
            vrid += 1
        if vrid > MAX_VRID:
            p.errors.append("Hết VRID trống (VRRP chỉ có 1–255). Không tạo thêm được gateway ảo.")
            return
        a.vrid = vrid
        used_vrids.add(vrid)
        vrid += 1

        base = f"{cfg.prefix}-{pr.slug()}"
        a.vrrp_name = _unique(base, used_ifaces)
        used_ifaces.add(a.vrrp_name)
        a.table = _unique(base, used_tables)
        used_tables.add(a.table)
        a.conn_mark = f"{cfg.tag}-{pr.slug()}"

        p.allocations.append(a)

    _plan_pool_shrink(cfg, facts, chosen, p)


def _gateway_candidates(cfg: Config, facts: RouterFacts, used: set[str]) -> list[str]:
    """
    Ưu tiên các IP nằm NGOÀI mọi DHCP pool. Chỉ khi không còn lựa chọn mới lấn
    vào pool (và khi đó sẽ kèm một lệnh thu hẹp pool).
    """
    net = ipaddress.ip_network(cfg.lan_subnet)          # type: ignore[arg-type]
    if cfg.gateway_pool:
        lo, _, hi = cfg.gateway_pool.partition("-")
        rng = _iter_range(lo.strip(), (hi or lo).strip())
    else:
        rng = [str(h) for h in net.hosts()]

    pools = [r for ranges in facts.dhcp_pools.values() for r in ranges]
    outside, inside = [], []
    for ip in rng:
        if ip in used or ip == cfg.lan_gateway:
            continue
        try:
            if ipaddress.ip_address(ip) not in net:
                continue
        except ValueError:
            continue
        (inside if _in_any_pool(ip, pools) else outside).append(ip)
    return outside + inside


def _plan_pool_shrink(cfg: Config, facts: RouterFacts, chosen: list[str], p: Plan) -> None:
    """Nếu IP gateway rơi vào DHCP pool thì đẩy điểm bắt đầu của pool lên trên."""
    if not chosen:
        return
    top = max(ipaddress.ip_address(ip) for ip in chosen)

    for name, ranges in facts.dhcp_pools.items():
        new_ranges, touched = [], False
        for r in ranges:
            lo_s, _, hi_s = r.partition("-")
            lo_s, hi_s = lo_s.strip(), (hi_s or lo_s).strip()
            try:
                lo, hi = ipaddress.ip_address(lo_s), ipaddress.ip_address(hi_s)
            except ValueError:
                new_ranges.append(r)
                continue

            hit = [ipaddress.ip_address(ip) for ip in chosen if lo <= ipaddress.ip_address(ip) <= hi]
            if not hit:
                new_ranges.append(r)
                continue

            # chỉ xử lý được khi IP đã chọn nằm ở rìa dưới của pool
            if min(hit) != lo and any(x > lo for x in hit):
                gaps = ", ".join(str(x) for x in sorted(hit))
                p.warnings.append(
                    f"DHCP pool {name!r} ({r}) chứa IP gateway {gaps} ở giữa dải. "
                    f"Tool không tự cắt đôi pool — hãy sửa tay hoặc chọn 'gateway_pool' khác."
                )
                new_ranges.append(r)
                continue

            new_lo = max(lo, top + 1)
            if new_lo >= hi:
                p.errors.append(f"Thu hẹp DHCP pool {name!r} sẽ làm pool rỗng. Hãy chọn IP gateway khác.")
                new_ranges.append(r)
                continue
            new_ranges.append(f"{new_lo}-{hi}")
            touched = True

        if touched:
            p.pool_changes.append((name, ",".join(ranges), ",".join(new_ranges)))


def _mangle_index(facts: RouterFacts, tag: str) -> int:
    """Vị trí chèn, tính trên danh sách ĐÃ bỏ các rule cũ của chính chúng ta."""
    ours = f"[{tag}]"
    pos = 0
    for r in facts.mangle:
        if ours in r.comment:
            continue
        if r.chain == "prerouting" and r.action in POLICY_ACTIONS:
            return pos
        pos += 1
    return pos


def doctor(cfg: Config, facts: RouterFacts, p: Plan) -> list[str]:
    """Cảnh báo các cạm bẫy đã biết. Không chặn, chỉ để admin biết trước."""
    w: list[str] = []

    if facts.free_hdd_kib and facts.free_hdd_kib < 1024:
        w.append(
            f"Router chỉ còn {facts.free_hdd_kib / 1024:.1f} MiB trống. Bản backup "
            f"nhị phân cỡ ~70 KiB vẫn đủ chỗ, nhưng nên dọn bớt /file trước khi nâng cấp RouterOS."
        )

    nat = [d for d in facts.downstream if getattr(d, "kind", "") == "nat"]
    pinned_ips = {m for g in cfg.pinned for m in g.match}
    for d in nat:
        if d.address in pinned_ips:
            continue          # đã xử lý bằng cách ghim, không cần cảnh báo nữa
        who = f"{d.address}" + (f" ({d.hostname})" if d.hostname else "")
        w.append(
            f"{who} nhiều khả năng là router phụ có NAT ({'; '.join(d.reasons)}). "
            f"Máy nằm SAU nó KHÔNG đổi được nhà mạng bằng gateway. Cách xử lý: chuyển "
            f"thiết bị đó sang chế độ Bridge/AP, hoặc ghim cả cụm vào một lựa chọn "
            f"cố định bằng mục 'pinned' trong file cấu hình."
        )

    existing = [r for r in facts.mangle
                if r.chain == "prerouting" and r.action in POLICY_ACTIONS
                and f"[{cfg.tag}]" not in r.comment]
    if existing:
        w.append(
            f"Router đã có sẵn {len(existing)} rule mangle đánh dấu định tuyến "
            f"(nhiều khả năng là cân bằng tải PCC). Rule mới được chèn TRƯỚC chúng "
            f"và dùng connection-mark riêng nên không xung đột — nhưng nên xem lại diff."
        )

    for pool, old, new in p.pool_changes:
        w.append(f"DHCP pool {pool!r} sẽ bị thu hẹp: {old} → {new}")

    running = {x.name for x in facts.pppoe if x.running}
    for wan in cfg.wans:
        if facts.pppoe and wan.interface not in running and \
                any(x.name == wan.interface for x in facts.pppoe):
            w.append(f"Đường {wan.name} ({wan.interface}) hiện KHÔNG kết nối. "
                     f"Cấu hình vẫn tạo được nhưng chưa test được.")

    n = sum(1 for a in p.allocations if a.vrrp_name)
    if n > 6:
        w.append(f"{n} gateway ảo là khá nhiều — mỗi cái gửi một gói VRRP mỗi giây lên LAN.")

    return w


# --------------------------------------------------------------------------

def _unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _iter_range(lo: str, hi: str) -> list[str]:
    a, b = ipaddress.ip_address(lo), ipaddress.ip_address(hi)
    return [str(ipaddress.ip_address(i)) for i in range(int(a), int(b) + 1)]


def _in_any_pool(ip: str, ranges: list[str]) -> bool:
    x = ipaddress.ip_address(ip)
    for r in ranges:
        lo_s, _, hi_s = r.partition("-")
        try:
            lo = ipaddress.ip_address(lo_s.strip())
            hi = ipaddress.ip_address((hi_s or lo_s).strip())
        except ValueError:
            continue
        if lo <= x <= hi:
            return True
    return False


def _default_color(pr: Profile, cfg: Config) -> str:
    """
    Profile một WAN thì lấy màu của WAN đó. Profile tổ hợp thì PHA màu các WAN
    thành phần lại — nhờ vậy nút "Viettel + VNPT" (đỏ + xanh) tự ra màu tím,
    khác hẳn hai nút kia, không phải chọn tay.
    """
    if pr.is_default:
        return "#546E7A"
    colors = [w.color for w in cfg.wans if w.interface in pr.wans]
    if not colors:
        return "#546E7A"
    if len(colors) == 1:
        return colors[0]

    rgb = [0, 0, 0]
    for hexcol in colors:
        h = hexcol.lstrip("#")
        if len(h) != 6:
            return colors[0]
        for i in range(3):
            rgb[i] += int(h[i * 2:i * 2 + 2], 16)
    return "#" + "".join(f"{v // len(colors):02X}" for v in rgb)


def _detail(pr: Profile, cfg: Config) -> str:
    if pr.is_default:
        return "Giữ nguyên hành vi mặc định của router"
    names = [w.name for w in cfg.wans if w.interface in pr.wans]
    if len(names) == 1:
        return f"Đi riêng qua đường {names[0]}"
    return "Chia tải giữa " + " + ".join(names)

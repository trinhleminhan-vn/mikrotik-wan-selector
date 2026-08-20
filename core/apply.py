"""
apply.py — Ghi cấu hình lên router một cách an toàn.

Quy trình cố định, không có đường tắt:
    backup  ->  dead-man switch  ->  apply  ->  verify  ->  gỡ dead-man

Dead-man switch là một scheduler trên chính router: nếu vì lý do gì đó ta mất
kết nối giữa chừng và không kịp gỡ nó, router tự hoàn tác toàn bộ sau vài phút.
Không có nó thì một lỗi cấu hình mạng đồng nghĩa với việc phải chạy tới tận nơi
cắm cáp console.
"""
from __future__ import annotations

import datetime
import pathlib
import time
from dataclasses import dataclass, field

from . import version as ver
from .model import Config, RouterFacts
from .plan import Plan
from .ros import Ros, q

DEADMAN_NAME = "{tag}-DEADMAN"
ROLLBACK_SCRIPT = "{tag}-ROLLBACK"


@dataclass
class VerifyResult:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(c[1] for c in self.checks)


# --------------------------------------------------------------------------

def backup(ros: Ros, out_dir: pathlib.Path, label: str = "pre") -> list[pathlib.Path]:
    """Tạo backup nhị phân + bản export text, tải về máy rồi xoá khỏi router."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{label}-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    ros.run(f"/system backup save name={name} dont-encrypt=yes")
    ros.run(f"/export file={name}")
    time.sleep(3)                      # RouterOS ghi file bất đồng bộ

    saved: list[pathlib.Path] = []
    for remote in (f"{name}.backup", f"{name}.rsc"):
        local = out_dir / remote
        try:
            size = ros.download(remote, local)
            ros.log(f"  -> tải về {local} ({size} bytes)")
            saved.append(local)
            ros.run(f'/file remove [find name={q(remote)}]', check=False)
        except Exception as e:                                   # noqa: BLE001
            ros.log(f"  !! không tải được {remote}: {e}")
    if not saved:
        raise RuntimeError("Không tạo/tải được backup — dừng lại, không ghi gì lên router.")
    return saved


def rollback_source(cfg: Config, plan: Plan | None = None) -> str:
    """Chuỗi lệnh hoàn tác, dùng cho cả script trên router lẫn lệnh rollback."""
    tag = cfg.tag
    steps = [
        f'/ip firewall mangle remove [find comment~\\"{tag}\\"]',
        f'/ip firewall address-list remove [find comment~\\"{tag}\\"]',
        f'/ip address remove [find comment~\\"{tag}\\"]',
        f'/interface vrrp remove [find comment~\\"{tag}\\"]',
        f'/ip route remove [find comment~\\"{tag}\\"]',
    ]
    if (plan.dialect if plan else "v7") == "v7":
        steps.append(f'/routing table remove [find comment~\\"{tag}\\"]')
    for name, old, _new in (plan.pool_changes if plan else []):
        steps.append(f'/ip pool set [find name=\\"{name}\\"] ranges={old}')
    steps.append(f'/system scheduler remove [find name~\\"{tag}\\"]')
    return "; ".join(steps)


def install_deadman(ros: Ros, cfg: Config, plan: Plan, minutes: int = 5) -> None:
    tag = cfg.tag
    sched = DEADMAN_NAME.format(tag=tag)
    script = ROLLBACK_SCRIPT.format(tag=tag)

    ros.run(f'/system scheduler remove [find name~{q(tag)}]', check=False)
    ros.run(f'/system script remove [find name~{q(tag)}]', check=False)
    ros.run(
        f'/system script add name={script} dont-require-permissions=no '
        f'policy=read,write,policy,test comment={q(f"[{tag}] hoan tac toan bo")} '
        f'source="{rollback_source(cfg, plan)}"'
    )
    ros.run(
        f'/system scheduler add name={sched} interval={minutes}m '
        f'policy=read,write,policy,test on-event={q(f"/system script run {script}")} '
        f'comment={q(f"[{tag}] dead-man: tu hoan tac neu mat ket noi")}'
    )
    ros.log(f"== Dead-man switch đã bật: tự hoàn tác sau {minutes} phút nếu không được gỡ.")


def remove_deadman(ros: Ros, cfg: Config) -> None:
    ros.run(f'/system scheduler remove [find name~{q(cfg.tag)}]', check=False)
    ros.log("== Đã gỡ dead-man switch (script hoàn tác vẫn giữ lại để dùng tay).")


def run_plan(ros: Ros, plan: Plan) -> None:
    """Chạy các lệnh trong plan. Dòng bắt đầu bằng '#' chỉ là chú thích."""
    for cmd in plan.commands:
        if cmd.lstrip().startswith("#"):
            ros.log(cmd)
            continue
        ros.run(cmd)


def rollback(ros: Ros, cfg: Config, plan: Plan | None = None) -> None:
    for step in rollback_source(cfg, plan).replace('\\"', '"').split("; "):
        ros.run(step, check=False)
    ros.run(f'/system script remove [find name~{q(cfg.tag)}]', check=False)


# --------------------------------------------------------------------------

def verify(ros: Ros, cfg: Config, plan: Plan) -> VerifyResult:
    """Kiểm tra sau khi áp: mỗi thứ đều phải ĐANG chạy, không chỉ là đã tạo."""
    v = VerifyResult()
    tag = cfg.tag

    vrrps = {r["name"]: r for r in ros.rows("/interface vrrp") if r.get("name")}
    for a in plan.allocations:
        if not a.vrrp_name:
            continue
        row = vrrps.get(a.vrrp_name)
        flags = (row or {}).get(".flags", "")
        v.add(f"VRRP {a.vrrp_name} là master",
              bool(row) and "M" in flags and "R" in flags,
              f"cờ={flags or 'không tìm thấy'}")

    addrs = ros.rows("/ip address")
    for a in plan.allocations:
        if not a.gateway or not a.vrrp_name:
            continue
        row = next((x for x in addrs if x.get("address", "").startswith(a.gateway + "/")), None)
        flags = (row or {}).get(".flags", "")
        v.add(f"IP {a.gateway}/32 hợp lệ",
              bool(row) and "I" not in flags,
              f"cờ={flags or 'không tìm thấy'}")
        if row and not row.get("address", "").endswith("/32"):
            v.add(f"IP {a.gateway} dùng /32", False,
                  f"đang là {row.get('address')} — sẽ sinh connected route trùng subnet")

    routes = ros.rows("/ip route")
    rt_field = "routing-table" if plan.dialect == "v7" else "routing-mark"
    for a in plan.allocations:
        if not a.table:
            continue
        act = [r for r in routes
               if r.get(rt_field) == a.table
               and r.get("dst-address") == "0.0.0.0/0"
               and "A" in (r.get(".flags") or "")]
        v.add(f"Bảng {a.table} có route đang hoạt động", bool(act),
              act[0].get("gateway", "") if act else "không có route active")

    mangle = ros.rows("/ip firewall mangle")
    ours = [m for m in mangle if tag in (m.get("comment") or "")]
    v.add("Rule mangle đã được tạo", len(ours) >= 2, f"{len(ours)} rule")
    bad = [m for m in ours if "I" in (m.get(".flags") or "")]
    v.add("Không có rule mangle nào invalid", not bad,
          "; ".join(m.get("comment", "") for m in bad))

    # Bẫy đã từng làm hỏng hệ thống: rule mark-routing không giới hạn chiều đi.
    # Phải có in-interface (rule theo gateway) HOẶC src-address-list (rule ghim),
    # nếu không gói TRẢ LỜI từ Internet cũng bị gán routing-mark và bị đẩy ngược ra WAN.
    leaky = [m for m in ours
             if m.get("action") == "mark-routing"
             and not m.get("in-interface") and not m.get("src-address-list")]
    v.add("mark-routing đều giới hạn chiều LAN đi ra", not leaky,
          "thiếu in-interface/src-address-list -> gói trả lời sẽ bị đẩy ngược ra WAN")

    lan_net = cfg.lan_subnet or ""
    dup = [r for r in routes
           if r.get("dst-address") == lan_net and "c" in (r.get(".flags") or "")]
    v.add("Không có connected route trùng subnet LAN", len(dup) <= 1,
          f"{len(dup)} route cho {lan_net}")

    return v


def summarize_facts(facts: RouterFacts) -> str:
    free_mb = facts.free_hdd_kib / 1024
    verdict = ver.assess(facts.version, free_mb)
    icon = {"ok": "✔", "warn": "!", "legacy": "!", "blocked": "✗"}[verdict["level"]]
    lines = [
        f"Router      : {facts.identity or '?'}  ({facts.board or '?'}, RouterOS {facts.version or '?'})",
        f"LAN         : {facts.lan_interface or '?'}  {facts.lan_subnet or '?'}  "
        f"(gateway {facts.lan_gateway or '?'})",
        f"Ổ đĩa trống : {facts.free_hdd_kib / 1024:.1f} MiB",
        "Đường WAN   :",
    ]
    if not facts.pppoe:
        lines.append("   (không tìm thấy PPPoE client nào)")
    for p in facts.pppoe:
        state = "đang chạy" if p.running else "KHÔNG kết nối"
        ip = p.local_address or "-"
        lines.append(f"   {p.name:<14} {state:<14} IP {ip:<16} {p.isp or 'chưa rõ nhà mạng'}")
    if facts.used_vrids:
        lines.append(f"VRID đã dùng: {sorted(facts.used_vrids)}")

    lines.append(f"Phiên bản   : {icon} {verdict['title']}")
    for h in verdict["hints"]:
        lines.append(f"              • {h}")
    disk = ver.disk_warning(free_mb, facts.board)
    if disk:
        lines.append(f"              • {disk}")
    return "\n".join(lines)

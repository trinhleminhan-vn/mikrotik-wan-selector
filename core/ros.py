"""
ros.py — Kết nối RouterOS qua SSH và đọc kết quả dạng có cấu trúc.

Điểm quan trọng: mọi lệnh gửi đi và kết quả trả về đều được ghi log ra file.
Khi tool tự động ghi vào router production thì nhật ký đầy đủ là thứ bắt buộc,
không phải tuỳ chọn.
"""
from __future__ import annotations

import datetime
import pathlib
import re
import shlex
from typing import Iterable

try:
    import paramiko
except ImportError as e:  # pragma: no cover
    raise SystemExit("Thiếu thư viện paramiko. Chạy: pip install paramiko") from e

from . import keys as keys_mod
from .model import RouterAuth


def probe_auth_methods(auth: RouterAuth) -> list[str] | None:
    """
    Hỏi router xem nó chấp nhận những cách đăng nhập nào.

    Dùng thủ thuật auth_none: chắc chắn thất bại, nhưng trong lỗi trả về có kèm
    danh sách phương thức hợp lệ. Nhờ vậy báo lỗi được đúng nguyên nhân thay vì
    chỉ nói chung chung "đăng nhập thất bại".
    """
    t = None
    try:
        t = paramiko.Transport((auth.host, auth.port))
        t.connect()
        t.auth_none(auth.user)
        return None                                     # router cho vào tự do (rất hiếm)
    except paramiko.BadAuthenticationType as e:
        return list(e.allowed_types)
    except Exception:                                   # noqa: BLE001
        return None
    finally:
        if t is not None:
            try:
                t.close()
            except Exception:                           # noqa: BLE001
                pass

# RouterOS in lỗi ra stdout chứ không dùng exit code, nên phải dò theo chuỗi.
ERROR_MARKERS = (
    "syntax error",
    "bad parameter",
    "expected end of command",
    "expected command name",
    "failure:",
    "no such item",
    "input does not match any value",
    "invalid value",
    "ambiguous value",
    "cannot ",
    "not enough permissions",
)


class RosError(RuntimeError):
    def __init__(self, command: str, output: str):
        super().__init__(f"Lệnh RouterOS thất bại:\n  {command}\n  -> {output.strip()}")
        self.command = command
        self.output = output


class ConnectError(RuntimeError):
    """
    Lỗi kết nối/đăng nhập đã được diễn giải sang tiếng Việt.

    `message` là câu giải thích cho người dùng bình thường, `hints` là các việc
    nên làm, `detail` giữ nguyên lỗi gốc để người rành kỹ thuật đối chiếu.
    """

    def __init__(self, message: str, hints: list[str] | None = None, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.hints = hints or []
        self.detail = detail

    def as_text(self) -> str:
        out = [self.message]
        out += [f"  • {h}" for h in self.hints]
        if self.detail:
            out.append(f"  (chi tiết kỹ thuật: {self.detail})")
        return "\n".join(out)


def explain_auth_failure(auth: RouterAuth, errors: list[str],
                         allowed: list[str] | None) -> ConnectError:
    """Biến lỗi paramiko khó hiểu thành lời giải thích dùng được."""
    joined = " | ".join(errors)
    low = joined.lower()
    allowed = allowed or []
    tried_key = bool(auth.key or auth.key_data)

    # Router chỉ nhận key, mà ta lại đưa mật khẩu.
    # Đây là hành vi RouterOS: user nào đã được import SSH key thì đăng nhập
    # bằng mật khẩu qua SSH sẽ bị tắt cho chính user đó.
    #
    # Chỉ báo lỗi này khi người dùng KHÔNG hề đưa key. Nếu đã đưa key mà vẫn
    # hỏng thì vấn đề nằm ở cái key, nói "router chỉ nhận key" là sai hướng.
    if allowed and "password" not in allowed and "publickey" in allowed and not tried_key:
        return ConnectError(
            f"Router chỉ cho đăng nhập bằng SSH key, không nhận mật khẩu "
            f"(tài khoản {auth.user}).",
            [
                "Đây KHÔNG phải do SSH bị tắt — SSH vẫn đang chạy bình thường.",
                "Nguyên nhân thường gặp: tài khoản này đã được nạp SSH key trước đó. "
                "RouterOS tự tắt đăng nhập bằng mật khẩu cho tài khoản đã có key.",
                "Cách xử lý: chọn đúng file SSH key ở ô 'Khoá SSH' phía trên.",
                "Nếu không còn key: vào Winbox → System → Users → tab SSH Keys, "
                "xoá key cũ của tài khoản này, hoặc nạp key mới rồi chọn ở đây.",
            ],
            joined,
        )

    if "authentication failed" in low or "authentication" in low:
        if tried_key:
            hints = [
                "Khoá này chưa được nạp vào router, hoặc nạp cho tài khoản khác.",
                "Nạp khoá: Winbox → Files (kéo thả file .pub vào) → New Terminal → "
                f"/user ssh-keys import public-key-file=<tên file>.pub user={auth.user}",
                "Nạp lên router là file .pub (khoá công khai); còn chọn ở đây là file "
                "KHÔNG có đuôi .pub (khoá riêng).",
                "Kiểm tra đã chọn đúng cặp khoá chưa — khoá riêng ở đây phải khớp với "
                "khoá .pub đã nạp lên router.",
            ]
            if allowed and "password" not in allowed:
                hints.append(
                    "Tài khoản này KHÔNG dùng mật khẩu được (router chỉ nhận khoá), "
                    "nên bắt buộc phải có đúng khoá."
                )
            return ConnectError(
                f"Router từ chối khoá SSH của bạn (tài khoản {auth.user}).",
                hints, joined,
            )
        return ConnectError(
            f"Sai mật khẩu hoặc sai tên tài khoản (đang thử {auth.user}).",
            [
                "Kiểm tra lại tên tài khoản và mật khẩu.",
                "Nếu tài khoản bị giới hạn nguồn truy cập (Winbox → System → Users → "
                "ô Allowed Address), máy bạn phải nằm trong dải đó.",
            ],
            joined,
        )

    if "timed out" in low or "timeout" in low:
        return ConnectError(
            f"Không kết nối được tới {auth.host}:{auth.port} — hết thời gian chờ.",
            [
                "Kiểm tra máy bạn có cùng mạng với router không (thử ping IP đó).",
                "Nếu dịch vụ SSH bị giới hạn nguồn (IP → Services → ô Address), "
                "máy bạn phải nằm trong dải được phép.",
                "Cũng có thể có luật tường lửa chặn cổng SSH từ mạng của bạn.",
            ],
            joined,
        )

    if "refused" in low or "unable to connect" in low or "no route to host" in low:
        return ConnectError(
            f"Router từ chối kết nối ở {auth.host}:{auth.port}.",
            [
                "Dịch vụ SSH đang tắt: Winbox → IP → Services → bật dòng 'ssh'.",
                "Hoặc SSH đã đổi sang cổng khác — sửa lại ô 'Cổng SSH'.",
                "Kiểm tra IP router có đúng không.",
            ],
            joined,
        )

    if "not found" in low and ("key" in low or "ssh" in low):
        return ConnectError("Không tìm thấy file SSH key đã chọn.",
                            ["Chọn lại file key, hoặc dùng nút duyệt file."], joined)

    return ConnectError(
        f"Không đăng nhập được vào {auth.user}@{auth.host}:{auth.port}.",
        ["Kiểm tra IP, cổng, tài khoản và cách xác thực (key hay mật khẩu)."],
        joined,
    )


class Ros:
    """Phiên làm việc với một router. Dùng theo kiểu context manager."""

    def __init__(self, auth: RouterAuth, log_dir: pathlib.Path | str | None = None,
                 echo: bool = False):
        self.auth = auth
        self.echo = echo
        self._client: paramiko.SSHClient | None = None

        self.log_path: pathlib.Path | None = None
        if log_dir:
            d = pathlib.Path(log_dir)
            d.mkdir(parents=True, exist_ok=True)
            self.log_path = d / f"ros-{datetime.datetime.now():%Y%m%d}.log"

    # ---------------- kết nối ----------------

    def __enter__(self) -> "Ros":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def log(self, msg: str) -> None:
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
        if self.echo:
            print(msg)

    def connect(self) -> None:
        a = self.auth
        errors: list[str] = []
        allowed: list[str] | None = None

        def _try(**kw) -> bool:
            nonlocal allowed
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                c.connect(a.host, a.port, a.user, timeout=15,
                          allow_agent=False, look_for_keys=False, **kw)
                self._client = c
                return True
            except paramiko.BadAuthenticationType as e:
                allowed = list(e.allowed_types)
                errors.append(f"{type(e).__name__}: {e}")
            except Exception as e:                       # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
            try:
                c.close()
            except Exception:                            # noqa: BLE001
                pass
            return False

        if not (a.key or a.key_data or a.password):
            raise ConnectError(
                "Chưa có cách đăng nhập nào.",
                ["Chọn một file SSH key, hoặc nhập mật khẩu của tài khoản router."],
            )

        if a.key or a.key_data:
            try:
                pkey = keys_mod.load(path=a.key, data=a.key_data)
            except ValueError as e:
                raise ConnectError(str(e),
                                   ["Chọn lại file key (file KHÔNG có đuôi .pub)."]) from e
            if _try(pkey=pkey):
                self.log(f"== Kết nối {a.user}@{a.host}:{a.port} bằng SSH key — OK")
                return

        if a.password and _try(password=a.password):
            self.log(f"== Kết nối {a.user}@{a.host}:{a.port} bằng mật khẩu — OK")
            return

        # Chưa biết router chấp nhận cách nào thì hỏi thẳng nó, để báo lỗi cho đúng.
        if allowed is None:
            allowed = probe_auth_methods(a)

        err = explain_auth_failure(a, errors, allowed)
        self.log(f"!! {err.as_text()}")
        raise err

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    # ---------------- chạy lệnh ----------------

    def run(self, command: str, check: bool = True) -> str:
        if not self._client:
            raise RuntimeError("Chưa kết nối tới router.")
        self.log(f"--- CMD: {command}")
        _, stdout, stderr = self._client.exec_command(command, timeout=90)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        body = out + (("\n[stderr] " + err) if err.strip() else "")
        self.log(body.rstrip())

        if check:
            low = body.lower()
            for mark in ERROR_MARKERS:
                if mark in low:
                    raise RosError(command, body)
        return body

    def run_many(self, commands: Iterable[str], check: bool = True) -> list[str]:
        return [self.run(c, check=check) for c in commands]

    # ---------------- đọc dữ liệu có cấu trúc ----------------

    def rows(self, path: str, where: str = "") -> list[dict[str, str]]:
        """
        Chạy `<path> print terse [where ...]` và trả về danh sách dict.

        `print terse` cho mỗi bản ghi đúng một dòng, không xuống dòng giữa
        chừng như `print detail`, nên parse được đáng tin cậy.
        """
        cmd = f"{path} print terse"
        if where:
            cmd += f" where {where}"
        return parse_terse(self.run(cmd, check=False))

    def scalar(self, command: str) -> str:
        """Chạy lệnh dạng `:put (...)` và trả về đúng giá trị đã strip."""
        return self.run(command, check=False).strip()

    def download(self, remote: str, local: pathlib.Path) -> int:
        sftp = self._client.open_sftp()          # type: ignore[union-attr]
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(remote, str(local))
            return local.stat().st_size
        finally:
            sftp.close()


# --------------------------------------------------------------------------
# Parser cho `print terse`
# --------------------------------------------------------------------------

_FLAG_LINE = re.compile(r"^\s*(?:Flags|Columns)\s*:", re.IGNORECASE)
_KV = re.compile(r'([A-Za-z0-9_.\-]+)=("(?:[^"\\]|\\.)*"|\S*)')


def parse_terse(text: str) -> list[dict[str, str]]:
    """
    Ví dụ một dòng terse:
        0 X name="ftp" port=21 address=""

    -> {'.id': '0', '.flags': 'X', 'name': 'ftp', 'port': '21', 'address': ''}
    """
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip() or _FLAG_LINE.match(line):
            continue

        rec: dict[str, str] = {}
        for m in _KV.finditer(line):
            key, val = m.group(1), m.group(2)
            if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                val = val[1:-1]
            rec[key] = val.replace('\\"', '"')
        if not rec:
            continue

        # phần đứng trước cặp key=value đầu tiên là số thứ tự + các cờ
        head = line[: _KV.search(line).start()].split()      # type: ignore[union-attr]
        if head:
            rec[".id"] = head[0]
            if len(head) > 1:
                rec[".flags"] = "".join(head[1:])
        rows.append(rec)
    return rows


def q(value: str) -> str:
    """Bọc giá trị vào dấu nháy kiểu RouterOS (escape dấu nháy bên trong)."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def shell_safe(value: str) -> str:
    return shlex.quote(str(value))

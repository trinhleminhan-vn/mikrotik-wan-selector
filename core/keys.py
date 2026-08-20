"""
keys.py — Tìm và nạp SSH private key.

Người dùng không nên phải gõ tay đường dẫn key. Module này quét các chỗ đặt key
thông dụng để app quản trị hiện sẵn danh sách bấm chọn, và nạp được key từ cả
đường dẫn lẫn nội dung dán trực tiếp.
"""
from __future__ import annotations

import io
import pathlib

try:
    import paramiko
except ImportError as e:                                        # pragma: no cover
    raise SystemExit("Thiếu thư viện paramiko. Chạy: pip install paramiko") from e

KEY_CLASSES = (
    ("ED25519", paramiko.Ed25519Key),
    ("RSA", paramiko.RSAKey),
    ("ECDSA", paramiko.ECDSAKey),
)

# Bỏ qua các file rõ ràng không phải private key
SKIP_SUFFIXES = {".pub", ".ppk", ".txt", ".md", ".json", ".yaml", ".yml", ".log"}
SKIP_NAMES = {"known_hosts", "authorized_keys", "config", "known_hosts.old"}


def generate(dest_dir: pathlib.Path, name: str = "wanselector",
             kind: str = "rsa", bits: int = 2048,
             comment: str = "wanselector") -> dict:
    """
    Tạo một cặp khoá SSH mới, ghi ra `dest_dir`.

    Mặc định là RSA 2048 vì RouterOS đời nào cũng nhận. Ed25519 ngắn gọn và
    hiện đại hơn nhưng chỉ dùng được từ RouterOS v7.

    Trả về đường dẫn khoá riêng, khoá công khai, nội dung khoá công khai và
    câu lệnh nạp vào router — để giao diện hiển thị luôn cho người dùng.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

    dest_dir = pathlib.Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "wanselector"
    priv_path = dest_dir / safe
    pub_path = dest_dir / f"{safe}.pub"
    if priv_path.exists() or pub_path.exists():
        raise ValueError(
            f"Đã có khoá tên {safe!r} trong {dest_dir}. Đặt tên khác, hoặc xoá khoá cũ "
            f"nếu chắc chắn không còn dùng."
        )

    if kind == "ed25519":
        key = ed25519.Ed25519PrivateKey.generate()
    else:
        kind = "rsa"
        key = rsa.generate_private_key(public_exponent=65537, key_size=int(bits))

    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    pub_text = pub.decode("ascii") + " " + comment + "\n"

    priv_path.write_bytes(priv)
    pub_path.write_text(pub_text, encoding="ascii")
    try:
        priv_path.chmod(0o600)          # có tác dụng trên macOS/Linux
    except OSError:
        pass

    # Đọc lại bằng chính thư viện sẽ dùng để đăng nhập — tạo xong mà không nạp
    # được thì phải biết ngay, đừng để tới lúc kết nối mới lộ.
    if describe(priv_path) is None:
        raise ValueError("Tạo khoá xong nhưng không đọc lại được. Hãy thử lại.")

    return {
        "kind": "Ed25519" if kind == "ed25519" else f"RSA {bits}",
        "private": str(priv_path),
        "public": str(pub_path),
        "public_text": pub_text.strip(),
        "filename": pub_path.name,
    }


def load(path: str | None = None, data: str | None = None):
    """
    Trả về đối tượng PKey của paramiko.

    `data` là nội dung key dán/tải trực tiếp từ trình duyệt — dùng khi người
    dùng chọn file bằng hộp thoại (trình duyệt không cho biết đường dẫn thật).
    """
    if data:
        for _name, cls in KEY_CLASSES:
            try:
                return cls.from_private_key(io.StringIO(data))
            except Exception:                                   # noqa: BLE001
                continue
        raise ValueError(
            "Không đọc được nội dung SSH key. File có thể không phải private key, "
            "hoặc đang có mật khẩu bảo vệ (passphrase) — tool chưa hỗ trợ key có passphrase."
        )

    if not path:
        raise ValueError("Chưa chỉ định SSH key.")

    p = pathlib.Path(path).expanduser()
    if not p.exists():
        raise ValueError(f"Không tìm thấy file SSH key: {p}")
    if p.is_dir():
        raise ValueError(f"{p} là thư mục, không phải file key.")

    for _name, cls in KEY_CLASSES:
        try:
            return cls.from_private_key_file(str(p))
        except Exception:                                       # noqa: BLE001
            continue
    raise ValueError(
        f"Không đọc được SSH key {p.name}. Kiểm tra xem có phải bạn chọn nhầm file "
        f".pub (khoá công khai) không — cần chọn file KHÔNG có đuôi .pub. "
        f"Key có passphrase cũng chưa được hỗ trợ."
    )


def describe(path: pathlib.Path) -> str | None:
    """Trả về loại key nếu đọc được, None nếu không phải private key hợp lệ."""
    for name, cls in KEY_CLASSES:
        try:
            cls.from_private_key_file(str(path))
            return name
        except paramiko.PasswordRequiredException:
            return "có passphrase"
        except Exception:                                       # noqa: BLE001
            continue
    return None


def discover(extra_dirs: list[pathlib.Path] | None = None) -> list[dict]:
    """
    Quét các thư mục hay chứa key và trả về danh sách key đọc được:
        [{"path": "...", "name": "id_rsa", "type": "RSA", "where": "~/.ssh"}]
    """
    home = pathlib.Path.home()
    dirs: list[pathlib.Path] = [home / ".ssh"]
    dirs += list(extra_dirs or [])

    found: list[dict] = []
    seen: set[str] = set()

    for d in dirs:
        try:
            if not d.is_dir():
                continue
            entries = sorted(d.iterdir())
        except OSError:
            continue

        for f in entries:
            if not f.is_file() or f.suffix in SKIP_SUFFIXES or f.name in SKIP_NAMES:
                continue
            try:
                if f.stat().st_size > 64 * 1024:                # key thật rất nhỏ
                    continue
                head = f.open("rb").read(40)
            except OSError:
                continue
            if b"PRIVATE KEY" not in head and b"BEGIN " not in head:
                continue

            kind = describe(f)
            if not kind:
                continue
            real = str(f.resolve())
            if real in seen:
                continue
            seen.add(real)

            where = str(d)
            try:
                where = "~/" + str(d.relative_to(home)).replace("\\", "/")
            except ValueError:
                pass
            found.append({"path": str(f), "name": f.name, "type": kind, "where": where})

    return found

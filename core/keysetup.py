"""
keysetup.py — Nạp khoá SSH lên router và kiểm chứng ngay.

Tách khỏi core/keys.py để tránh vòng lặp import: keys.py được ros.py dùng, còn
module này lại cần ros.py.

Điểm quan trọng về an toàn: **RouterOS tự tắt đăng nhập bằng mật khẩu cho tài
khoản nào đã có khoá SSH.** Nghĩa là nạp khoá xong mà khoá lại sai, thì mất luôn
đường SSH vào máy đó (Winbox vẫn vào được, nhưng vẫn rất phiền).

Nên quy trình ở đây là: ghi nhớ danh sách khoá cũ → nạp → mở MỘT KẾT NỐI MỚI
bằng khoá để kiểm chứng → hỏng thì gỡ đúng cái khoá vừa nạp, trả router về
nguyên trạng.
"""
from __future__ import annotations

import pathlib

from .model import RouterAuth
from .ros import Ros, q


def install(ros: Ros, auth: RouterAuth, pub_path: str | pathlib.Path,
            priv_path: str | pathlib.Path) -> dict:
    """
    Nạp khoá công khai vào tài khoản đang đăng nhập, rồi kiểm chứng.

    `ros` là phiên đang mở (thường đăng nhập bằng mật khẩu).
    Trả về dict có `ok`, `message`, `steps`.
    """
    pub_path = pathlib.Path(pub_path)
    if not pub_path.exists():
        return {"ok": False, "message": f"Không tìm thấy file khoá công khai: {pub_path}"}

    user = auth.user
    remote = pub_path.name
    steps: list[str] = []

    def note(s: str) -> None:
        steps.append(s)
        ros.log("   " + s)

    # 1. Ghi nhớ các khoá đang có, để biết cái nào là của lần nạp này.
    #    Dùng ID nội bộ (*1, *2...) chứ không dùng số thứ tự: số thứ tự xê dịch
    #    mỗi khi thêm/bớt, gỡ nhầm khoá của người khác là hỏng việc.
    before = set(_key_ids(ros))

    # 2. Đẩy file .pub lên router
    sftp = ros._client.open_sftp()          # type: ignore[union-attr]
    try:
        sftp.put(str(pub_path), remote)
    finally:
        sftp.close()
    note(f"Đã tải {remote} lên router")

    # 3. Nạp khoá
    try:
        ros.run(f'/user ssh-keys import public-key-file={q(remote)} user={q(user)}')
        note(f"Đã nạp khoá vào tài khoản {user}")
    except Exception as e:                                       # noqa: BLE001
        ros.run(f'/file remove [find name={q(remote)}]', check=False)
        return {"ok": False, "steps": steps,
                "message": f"Router từ chối nạp khoá: {e}"}

    new_ids = [k for k in _key_ids(ros) if k not in before]

    # 4. Dọn file .pub trên router — đã nạp rồi thì không cần giữ
    ros.run(f'/file remove [find name={q(remote)}]', check=False)

    # 5. Kiểm chứng bằng MỘT KẾT NỐI MỚI dùng khoá vừa nạp
    probe = RouterAuth(host=auth.host, port=auth.port, user=user, key=str(priv_path))
    test = Ros(probe, log_dir=ros.log_path.parent if ros.log_path else None)
    try:
        test.connect()
        test.run(":put [/system identity get name]", check=False)
        test.close()
        note("Đăng nhập thử bằng khoá mới — thành công")
        return {
            "ok": True, "steps": steps,
            "message": (f"Đã nạp khoá cho tài khoản {user} và đăng nhập thử thành công. "
                        f"Từ giờ tài khoản này CHỈ đăng nhập SSH bằng khoá — RouterOS tự "
                        f"tắt đăng nhập bằng mật khẩu cho tài khoản đã có khoá."),
        }
    except Exception as e:                                       # noqa: BLE001
        # Không vào được bằng khoá -> gỡ đúng khoá vừa nạp để trả lại nguyên trạng
        for kid in new_ids:
            ros.run(f"/user ssh-keys remove {kid}", check=False)
        note("Đăng nhập thử THẤT BẠI — đã gỡ khoá vừa nạp, router trở lại như cũ")
        return {"ok": False, "steps": steps,
                "message": (f"Nạp được khoá nhưng đăng nhập thử không thành công ({e}). "
                            f"Đã gỡ khoá đó ra, tài khoản {user} vẫn đăng nhập bằng mật khẩu "
                            f"như trước.")}


def _key_ids(ros: Ros) -> list[str]:
    """ID nội bộ của các khoá SSH (*1, *2...). RouterOS in ra cách nhau bằng ';'."""
    raw = ros.scalar(":put [/user ssh-keys find]")
    return [x for x in raw.replace(";", " ").split() if x.startswith("*")]


def list_keys(ros: Ros) -> list[dict]:
    """Các khoá SSH đang có trên router, để admin biết đường dọn."""
    out = []
    for r in ros.rows("/user ssh-keys"):
        out.append({"id": r.get(".id", ""), "user": r.get("user", ""),
                    "bits": r.get("bits", ""), "owner": r.get("key-owner", "")})
    return out

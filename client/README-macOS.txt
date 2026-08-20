========================================================================
  CHỌN NHÀ MẠNG RA INTERNET  —  Bản macOS (dùng được cả Linux)
========================================================================

CÁCH DÙNG TRÊN macOS
------------------------------------------------------------------------
  1. Chép nguyên cả thư mục này sang máy cần dùng. Phải giữ đủ các file
     cạnh nhau, đặc biệt là profiles.sh — đó là nơi chứa cấu hình riêng
     của hệ thống bạn.

  2. Bấm đúp vào file:   ChonNhaMang.command

     Lần đầu macOS có thể chặn với thông báo "không thể mở vì từ nhà phát
     triển không xác định". Khi đó bấm chuột phải vào file -> chọn "Open"
     -> bấm "Open" lần nữa để xác nhận.

     Nếu vẫn không mở được, mở Terminal và chạy (sửa đường dẫn cho đúng):

         cd "/duong/dan/toi/thu-muc-nay"
         chmod +x ChonNhaMang.command wanswitch.sh

  3. Terminal hiện menu, gõ số rồi Enter. Gõ "k" để kiểm tra IP public.

  4. Máy sẽ hỏi mật khẩu đăng nhập (quyền sudo) khi đổi gateway.


CÁCH DÙNG TRÊN LINUX
------------------------------------------------------------------------
      chmod +x wanswitch.sh
      ./wanswitch.sh                    # menu tương tác
      ./wanswitch.sh "<tên lựa chọn>"   # chọn thẳng
      ./wanswitch.sh status             # không cần sudo


LƯU Ý
------------------------------------------------------------------------
  * Máy phải được nối trực tiếp vào router MikroTik và nhận IP trong lớp
    mạng ghi ở cuối file này. Nếu máy nằm sau một router Wi-Fi khác thì
    tool sẽ cảnh báo và không đổi được.

  * Các kết nối ĐANG MỞ vẫn giữ đường cũ. Đóng/mở lại trình duyệt sau khi
    đổi rồi hãy kiểm tra.

  * KHÁC BIỆT SO VỚI BẢN WINDOWS: trên macOS lựa chọn KHÔNG được ghi nhớ
    lâu dài. Rút/cắm lại dây mạng, đổi Wi-Fi hoặc khởi động lại là quay về
    mặc định — chạy lại tool là được. Đây là giới hạn cách macOS quản lý
    default route, không phải lỗi.

  * Nếu đường mạng đang chọn bị đứt, máy sẽ mất Internet — đúng theo thiết
    kế. Chọn chế độ mặc định để chạy lại.


KHẮC PHỤC SỰ CỐ
------------------------------------------------------------------------
  "CHƯA ăn — hệ thống vẫn đi hướng khác"
      -> Máy đang có nhiều đường ra Internet cùng lúc. Tool tự xử lý:
              Linux : tính metric thấp nhất đang có rồi thêm route của
                      mình thấp hơn 1 bậc; nếu card LAN khác đang giữ
                      default route thì dời route sang card đó.
              macOS : 'route change' không ăn thì xoá hẳn default route
                      cũ rồi thêm lại (có phục hồi nếu thêm hỏng).
         Không thắng được thì tool CHỈ ĐÍCH DANH card đang chiếm và loại
         của nó (VPN / máy ảo / Wi-Fi / di động) kèm việc cần làm.
         Hay gặp nhất là VPN toàn tuyến (utun*): phải ngắt VPN.
         Xem bảng đầy đủ:   ./wanswitch.sh routes
         Gỡ sạch route tool đã thêm:   ./wanswitch.sh reset

  "Không tìm thấy card mạng nào có IP trong lớp ..."
      -> Máy chưa nằm trên LAN của MikroTik.

  "Không tìm thấy profiles.sh cạnh file này"
      -> Chép thiếu file. Chép lại nguyên cả thư mục.

  Bấm đúp không có gì xảy ra
      -> File mất quyền chạy (hay gặp khi chép qua USB định dạng FAT32):
             chmod +x ChonNhaMang.command wanswitch.sh


GIỚI THIỆU
------------------------------------------------------------------------
  Khám phá thêm nhiều phần mềm tại:
      https://trinhleminhan.com/tools

  Muốn dựng công cụ / phần mềm riêng? Gửi liên hệ tại:
      https://trinhleminhan.com/lien-he/

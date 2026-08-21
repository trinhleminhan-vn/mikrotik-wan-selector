========================================================================
  CHỌN NHÀ MẠNG RA INTERNET  —  Bản dành cho Windows
========================================================================

CÁCH DÙNG
------------------------------------------------------------------------
  1. Chép nguyên cả thư mục này sang máy cần dùng (Desktop, USB, thư mục
     chung... đâu cũng được). Phải giữ đủ cả 3 file cạnh nhau, đặc biệt là
     profiles.json — đó là nơi chứa cấu hình riêng của hệ thống bạn.

  2. Bấm đúp vào file:   ChonNhaMang.cmd

  3. Windows hỏi quyền quản trị -> chọn "Yes".

  4. Cửa sổ hiện ra, mỗi nhà mạng một nút. Bấm nút để chuyển.

  5. Bấm "Kiểm tra IP public đang dùng" để xác nhận đã đổi đúng.


LƯU Ý
------------------------------------------------------------------------
  * Máy phải được nối trực tiếp vào router MikroTik và nhận IP trong lớp
    mạng ghi ở cuối file này. Nếu máy nằm sau một router Wi-Fi khác thì
    tool sẽ cảnh báo và không đổi được — đó là giới hạn kỹ thuật, không
    phải lỗi.

  * Các kết nối ĐANG MỞ vẫn giữ đường cũ. Sau khi đổi, đóng và mở lại
    trình duyệt (hoặc chờ 1-2 phút) rồi kiểm tra lại.

  * Lựa chọn được ghi nhớ qua cả khởi động lại máy. Muốn trở về bình
    thường thì bấm nút chế độ mặc định.

  * Nếu đường mạng đang chọn bị đứt, máy sẽ mất Internet — đúng theo thiết
    kế, vì bạn đã cố định lớp IP public. Bấm chế độ mặc định để chạy lại.


DÙNG BẰNG DÒNG LỆNH
------------------------------------------------------------------------
  Mở PowerShell với quyền Administrator, chuyển vào thư mục này:

      .\WanSwitch.ps1 -Mode "<tên lựa chọn>"
      .\WanSwitch.ps1 -Mode status      (xem trạng thái, không cần admin)

  Tên lựa chọn xem ở cuối file này, gõ đúng cả dấu.


KHẮC PHỤC SỰ CỐ
------------------------------------------------------------------------
  "CHƯA ăn — hệ thống vẫn đi hướng khác"
      -> Máy đang có NHIỀU đường ra Internet cùng lúc và Windows chọn
         đường khác. Tool tự xử lý theo 3 bước, ghi rõ từng bước trong
         ô nhật ký:
              1) Nếu card đang thắng cũng nằm trong lớp mạng của router
                 -> tự dời route sang đúng card đó.
              2) Cắm cặp route 0.0.0.0/1 + 128.0.0.0/1. Hai nửa này phủ
                 đúng bằng đường mặc định nhưng "hẹp" hơn một bit, mà
                 Windows luôn ưu tiên đường hẹp hơn TRƯỚC khi so điểm
                 metric -> thắng cả route do router cấp trên chính card
                 mình. Không hề đụng tới mạng nội bộ.
              3) Nếu vẫn thua (thường là do có VPN cũng dùng chiêu này)
                 -> tự hạ interface metric của card LAN xuống đủ thấp.
         Không thắng được thì tool sẽ CHỈ ĐÍCH DANH card đang chiếm và
         loại của nó (VPN / máy ảo / Wi-Fi / di động) kèm việc cần làm.
         Hay gặp nhất:
              - VPN toàn tuyến -> tool vượt được, NHƯNG vượt xong là
                traffic không còn đi trong tunnel nữa. Đang cần VPN cho
                công việc thì bấm MẶC ĐỊNH để trả đường lại cho VPN.
              - Card ảo Hyper-V / VMware / WSL -> tắt trong Network
                Connections.
              - Wi-Fi đang nối sang mạng khác -> tắt Wi-Fi.
         KHÔNG cần rút dây mạng đang dùng. Nếu tool báo kẻ chiếm nằm
         ngay trên chính card của bạn thì đó là route do router cấp,
         không phải "mạng phụ" nào cả.
         Xem bảng đầy đủ:   .\WanSwitch.ps1 -Mode status
         Dấu ">" là đường máy đang thực sự đi; dòng nào có [0.0.0.0/1]
         là route do tool cắm.

  Muốn gỡ sạch mọi thứ tool đã đặt
      -> .\WanSwitch.ps1 -Reset
         Gỡ route đã thêm VÀ trả interface metric về đúng như trước.
         (Bấm nút MẶC ĐỊNH trong giao diện cũng làm đúng việc này.)

  "Không tìm thấy card mạng nào có IP trong lớp ..."
      -> Máy chưa nằm trên LAN của MikroTik. Kiểm tra dây mạng, hoặc máy
         đang nối qua một router khác.

  "Không tìm thấy profiles.json cạnh file này"
      -> Bạn chép thiếu file. Chép lại nguyên cả thư mục.

  Bấm nút báo thành công nhưng vẫn không ra mạng
      -> Đóng/mở lại trình duyệt. Nếu vẫn không được, chọn chế độ mặc định
         rồi báo người quản trị.

  Cửa sổ hiện chữ bị lỗi font
      -> WanSwitch.ps1 phải được lưu dạng UTF-8 CÓ BOM. Đừng mở ra sửa rồi
         lưu lại bằng Notepad ở chế độ ANSI.


GIỚI THIỆU
------------------------------------------------------------------------
  Khám phá thêm nhiều phần mềm tại:
      https://trinhleminhan.com/tools

  Muốn dựng công cụ / phần mềm riêng? Gửi liên hệ tại:
      https://trinhleminhan.com/lien-he/

#!/usr/bin/env bash
# =============================================================================
#  ChonNhaMang.command — bấm đúp để mở tool chọn nhà mạng trên macOS
#  (macOS mở file .command bằng Terminal, không cần cài thêm gì)
# =============================================================================

cd "$(dirname "$0")" || exit 1

# Lần đầu chép từ máy khác sang, file có thể mất quyền chạy — tự cấp lại
[ -x ./wanswitch.sh ] || chmod +x ./wanswitch.sh 2>/dev/null

if [ ! -f ./wanswitch.sh ]; then
    echo "Không tìm thấy file wanswitch.sh cạnh file này."
    echo "Hãy giữ nguyên cả thư mục khi chép đi."
    read -r -p "Nhấn Enter để đóng..." _
    exit 1
fi

./wanswitch.sh

echo
read -r -p "Nhấn Enter để đóng cửa sổ..." _

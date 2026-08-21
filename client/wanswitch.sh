#!/usr/bin/env bash
# =============================================================================
#  wanswitch.sh — Chọn nhà mạng ra Internet  (macOS / Linux, bản dùng chung)
# -----------------------------------------------------------------------------
#  File này KHÔNG chứa cấu hình. Danh sách nhà mạng và IP gateway nằm trong
#  profiles.sh đặt cạnh nó, do app admin sinh ra.
#
#  Cách dùng:
#     ./wanswitch.sh                # menu tương tác (tự xin sudo khi cần)
#     ./wanswitch.sh "Viettel"      # chọn thẳng theo tên
#     ./wanswitch.sh status         # xem đang đi hướng nào (không cần sudo)
#     ./wanswitch.sh routes         # xem bảng tranh giành default route
#     ./wanswitch.sh reset          # gỡ sạch route do tool này thêm
# =============================================================================
set -u

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
HERE="$(dirname "$SELF")"

if [ ! -f "$HERE/profiles.sh" ]; then
    echo "Không tìm thấy profiles.sh cạnh file này. Hãy giữ nguyên cả thư mục khi chép đi." >&2
    exit 1
fi
# shellcheck source=/dev/null
. "$HERE/profiles.sh"

OS="$(uname -s)"
METRIC=50                 # Linux: metric mặc định khi không phải tranh giành

ABOUT_TOOLS="https://trinhleminhan.com/tools"
ABOUT_CONTACT="https://trinhleminhan.com/lien-he/"

if [ -t 1 ]; then
    RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'
    BLD=$'\033[1m';  DIM=$'\033[2m';  RST=$'\033[0m'
else
    RED=''; GRN=''; YEL=''; BLD=''; DIM=''; RST=''
fi

die()  { printf '%s\n' "${RED}LỖI:${RST} $*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }
ok()   { printf '%s\n' "${GRN}$*${RST}"; }
warn() { printf '%s\n' "${YEL}$*${RST}"; }

# Đọc trường của profile thứ N:  field 2 NAME  ->  giá trị của P2_NAME
field() { eval "printf '%s' \"\${P${1}_${2}:-}\""; }

# Nơi nhớ card mạng đã chọn
CFGDIR="${XDG_CONFIG_HOME:-$HOME/.config}/wanswitch"
CFGFILE="$CFGDIR/adapter"

# In ra MỌI card đang có IP trong lớp LAN của router, mỗi dòng "iface ip".
#
# Máy nhiều card là chuyện thường: laptop vừa cắm dây vừa bật Wi-Fi, máy bàn hai
# cổng LAN, máy ảo có card ảo. Chọn bừa cái đầu tiên là sai — thêm route vào card
# không phải card đang đi mạng thì đổi xong vẫn không có tác dụng gì.
list_lan_ifaces() {
    case "$OS" in
        Linux)
            ip -4 -o addr show scope global 2>/dev/null \
                | awk -v p="$LAN_PREFIX" '$4 ~ "^"p {split($4,a,"/"); print $2, a[1]}'
            ;;
        Darwin)
            for i in $(ifconfig -l); do
                a=$(ipconfig getifaddr "$i" 2>/dev/null || true)
                case "$a" in "$LAN_PREFIX"*) printf '%s %s\n' "$i" "$a" ;; esac
            done
            ;;
        *) die "Hệ điều hành không hỗ trợ: $OS" ;;
    esac
}

count_lan_ifaces() { list_lan_ifaces | grep -c . ; }

# Card đang dùng: ưu tiên card người dùng đã chọn, không có thì lấy cái đầu.
find_lan_iface() {
    all="$(list_lan_ifaces)"
    [ -n "$all" ] || return 0
    if [ -f "$CFGFILE" ]; then
        saved="$(cat "$CFGFILE" 2>/dev/null)"
        line="$(printf '%s\n' "$all" | awk -v w="$saved" '$1==w {print; exit}')"
        [ -n "$line" ] && { printf '%s\n' "$line"; return 0; }
    fi
    printf '%s\n' "$all" | head -n 1
}

save_lan_iface() {
    mkdir -p "$CFGDIR" 2>/dev/null && printf '%s\n' "$1" > "$CFGFILE" 2>/dev/null || true
}

choose_iface() {
    all="$(list_lan_ifaces)"
    n="$(printf '%s\n' "$all" | grep -c .)"
    if [ "$n" -le 1 ]; then
        warn "Máy chỉ có một card trong lớp ${LAN_PREFIX}0/24, không cần chọn."
        return
    fi
    printf '\nCác card mạng trong lớp %s0/24:\n' "$LAN_PREFIX"
    printf '%s\n' "$all" | nl -w4 -s') '
    printf 'Chọn card (Enter để bỏ qua): '
    read -r c || return
    case "$c" in ''|*[!0-9]*) return ;; esac
    line="$(printf '%s\n' "$all" | sed -n "${c}p")"
    [ -n "$line" ] || { warn "Lựa chọn không hợp lệ."; return; }
    save_lan_iface "${line%% *}"
    ok "Đã ghi nhớ card mạng: ${line%% *}"
}

show_about() {
    printf '\n%s%s%s\n' "$BLD" "$SYSTEM_NAME" "$RST"
    printf '  Công cụ đổi hướng ra Internet cho máy trong mạng nội bộ.\n\n'
    printf '  Khám phá thêm nhiều phần mềm tại:\n    %s\n\n' "$ABOUT_TOOLS"
    printf '  Muốn dựng công cụ / phần mềm riêng? Gửi liên hệ tại:\n    %s\n' "$ABOUT_CONTACT"
}

# --------------------- Nhận dạng loại card mạng ------------------------------
# Để chỉ được đích danh THỦ PHẠM khi một card khác đang giữ default route, chứ
# không chỉ đoán mò "có VPN nào không".
mac_port_of() {
    networksetup -listallhardwareports 2>/dev/null \
        | awk -v d="$1" '/^Hardware Port:/ {p=substr($0,16)} /^Device:/ {if ($2==d) {print p; exit}}'
}

iface_kind() {
    local i="$1" port
    case "$i" in
        utun*|tun*|tap*|ppp*|ipsec*|gif*|wg*|nordlynx*|proton*|tailscale*|zt*)
            printf 'VPN'; return ;;
        docker*|br-*|virbr*|vmnet*|vboxnet*|veth*|bridge*|lxcbr*|awdl*|llw*|ap[0-9]*)
            printf 'máy ảo'; return ;;
        ww*|wwan*|rmnet*|usb[0-9]*)
            printf 'di động'; return ;;
    esac
    if [ "$OS" = "Linux" ]; then
        [ -d "/sys/class/net/$i/wireless" ] && { printf 'Wi-Fi'; return; }
        [ -e "/sys/class/net/$i/phy80211" ] && { printf 'Wi-Fi'; return; }
        case "$i" in wl*|wlan*|wlp*|ath*|ra[0-9]*) printf 'Wi-Fi'; return ;; esac
    else
        port="$(mac_port_of "$i")"
        case "$port" in
            *Wi-Fi*|*AirPort*)    printf 'Wi-Fi';   return ;;
            *iPhone*|*Bluetooth*) printf 'di động'; return ;;
        esac
    fi
    printf 'vật lý'
}

# Hai nửa của toàn bộ không gian địa chỉ. Cộng lại phủ đúng bằng default route
# nhưng prefix dài hơn một bit -> nhân hệ điều hành chọn chúng trước, bất kể metric.
SPLIT_HALVES='0.0.0.0/1 128.0.0.0/1'

# Danh sách gateway do tool này tạo ra (không tính gateway mặc định của DHCP).
managed_gateways() {
    local i=1
    while [ "$i" -le "$PROFILE_COUNT" ]; do
        [ "$(field "$i" DEFAULT)" = "0" ] && field "$i" GW && printf '\n'
        i=$((i + 1))
    done
}

# ------------------ Bảng tranh giành đường ra Internet -----------------------
# Mỗi dòng: "METRIC|IFACE|GATEWAY". Linux sắp sẵn theo metric.
default_routes() {
    case "$OS" in
        Linux)
            ip -4 route show 2>/dev/null | awk '
                $1=="default" || $1=="0.0.0.0/1" || $1=="128.0.0.0/1" {
                    pfx  = ($1=="default") ? "0.0.0.0/0" : $1
                    plen = ($1=="default") ? 0 : 1
                    gw=""; dev=""; m=0
                    for (i=2; i<=NF; i++) {
                        if      ($i=="via")    gw=$(i+1)
                        else if ($i=="dev")    dev=$(i+1)
                        else if ($i=="metric") m=$(i+1)
                    }
                    if (dev != "") printf "%s|%s|%s|%s|%s\n", plen, m, dev, gw, pfx
                }' | sort -t'|' -k1,1nr -k2,2n
            ;;
        Darwin)
            # netstat đổi số cột giữa các bản macOS -> dò cột nào là tên card.
            # macOS viết tắt prefix: "0/1" và "128.0/1" chứ không ghi đủ.
            netstat -rn -f inet 2>/dev/null | awk '
                $1=="default" || $1=="0/1" || $1=="0.0.0.0/1" ||
                $1=="128.0/1" || $1=="128.0.0.0/1" {
                    plen = ($1=="default") ? 0 : 1
                    pfx  = ($1=="default") ? "0.0.0.0/0" : \
                           (substr($1,1,1)=="0" ? "0.0.0.0/1" : "128.0.0.0/1")
                    dev=""
                    for (i=2; i<=NF; i++)
                        if ($i ~ /^(en|utun|ppp|ipsec|bridge|awdl|llw|gif|stf|anpi|ap|feth)[0-9]+$/) dev=$i
                    if (dev != "") printf "%s|0|%s|%s|%s\n", plen, dev, $2, pfx
                }' | sort -t'|' -k1,1nr
            ;;
    esac
}

# 'route get' cho biết nhân hệ điều hành THỰC SỰ chọn đường nào — chính xác hơn
# là đọc dòng đầu của bảng route.
current_gateway() {
    case "$OS" in
        Linux)  ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' ;;
        Darwin) route -n get 1.1.1.1 2>/dev/null | awk '/gateway:/ {print $2; exit}' ;;
    esac
}

current_iface() {
    case "$OS" in
        Linux)  ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' ;;
        Darwin) route -n get 1.1.1.1 2>/dev/null | awk '/interface:/ {print $2; exit}' ;;
    esac
}

route_table_lines() {
    local rows plen m dev gw pfx tag mark a_if a_gw
    rows="$(default_routes)"
    if [ -z "$rows" ]; then
        info "Không có default route nào."
        return
    fi
    # Dấu '>' bám theo đường nhân hệ điều hành thực sự chọn, không phải dòng đầu
    # bảng — macOS không xếp bảng theo độ ưu tiên.
    a_if="$(current_iface)"; a_gw="$(current_gateway)"
    if [ "$OS" = "Darwin" ]; then
        info "Bảng đường ra (prefix dài hơn thắng trước — '>' là đường đang đi):"
    else
        info "Bảng đường ra (prefix dài hơn thắng trước, cùng độ dài mới xét metric):"
    fi
    while IFS='|' read -r plen m dev gw pfx; do
        [ -n "$dev" ] || continue
        if [ "$dev" = "$a_if" ] && { [ -z "$gw" ] || [ "$gw" = "$a_gw" ]; }; then
            mark='>'
        else
            mark=' '
        fi
        # /0 là default route thường; /1 thì ghi rõ, vì chính nó giải thích vì
        # sao một dòng metric cao lại thắng dòng metric thấp.
        tag=''
        [ "$plen" = "0" ] || tag="  [$pfx]"
        if [ "$OS" = "Darwin" ]; then
            printf '%s %-14s %-15s (%s)%s\n' "$mark" "$dev" "${gw:--}" "$(iface_kind "$dev")" "$tag"
        else
            printf '%s %-14s %-15s metric %4s  (%s)%s\n' \
                   "$mark" "$dev" "${gw:--}" "$m" "$(iface_kind "$dev")" "$tag"
        fi
    done <<EOF
$rows
EOF
}

# Metric thấp nhất trong các default route KHÔNG phải của tool này (Linux)
lowest_foreign_metric() {
    local plen m dev gw pfx best='' i is_mine
    while IFS='|' read -r plen m dev gw pfx; do
        [ -n "$dev" ] || continue
        [ "$plen" = "0" ] || continue          # chỉ so metric giữa các default route
        is_mine=0; i=1
        while [ "$i" -le "$PROFILE_COUNT" ]; do
            if [ "$(field "$i" DEFAULT)" = "0" ] && [ "$(field "$i" GW)" = "$gw" ]; then
                is_mine=1; break
            fi
            i=$((i + 1))
        done
        [ "$is_mine" = "1" ] && continue
        if [ -z "$best" ] || [ "$m" -lt "$best" ]; then best="$m"; fi
    done <<EOF
$(default_routes)
EOF
    printf '%s' "${best:-}"
}

# Ai đang chiếm default route, và người dùng phải làm gì
explain_winner() {
    local dev kind gw mine="${1:-}"
    dev="$(current_iface)"; gw="$(current_gateway)"
    if [ -z "$dev" ]; then
        warn "  Không đọc được bảng định tuyến."
        return
    fi
    kind="$(iface_kind "$dev")"
    warn "  Card '$dev' [$kind] đang chiếm đường ra qua ${gw:-?}."
    # Cùng một card thì không có "mạng phụ" nào để rút. Bảo người ta rút dây ở
    # đây là bảo họ tự cắt mạng của chính mình.
    if [ -n "$mine" ] && [ "$dev" = "$mine" ]; then
        warn "  Route này nằm NGAY TRÊN card bạn đang dùng, do DHCP của router cấp —"
        warn "  không phải mạng phụ, đừng rút dây."
        warn "  Lệnh tự kiểm tra:  ./$(basename "$SELF") routes"
        return
    fi
    case "$kind" in
        VPN)
            warn "  VPN toàn tuyến kéo hết traffic vào tunnel — chọn nhà mạng không còn tác"
            warn "  dụng. Hãy ngắt VPN rồi bấm lại, hoặc bật split-tunnel cho VPN đó." ;;
        "máy ảo")
            warn "  Đây là card ảo (Docker / VM / bridge). Tắt nó hoặc bỏ default route của nó." ;;
        "di động")
            warn "  Đang có kết nối 4G/USB. Ngắt nó rồi bấm lại." ;;
        Wi-Fi)
            warn "  Wi-Fi đang nối sang mạng khác. Tắt Wi-Fi (hoặc nối Wi-Fi vào chính router"
            warn "  này) rồi bấm lại." ;;
        *)
            warn "  Ngắt kết nối mạng phụ này rồi bấm lại." ;;
    esac
    if [ "$OS" = "Darwin" ]; then
        warn "  Thứ tự ưu tiên mạng của macOS:  networksetup -listnetworkserviceorder"
    fi
    warn "  Lệnh tự kiểm tra:  ./$(basename "$SELF") routes"
}

# Tên của profile ứng với một IP gateway
name_of_gateway() {
    local i=1
    while [ "$i" -le "$PROFILE_COUNT" ]; do
        if [ "$(field "$i" GW)" = "$1" ]; then field "$i" NAME; return 0; fi
        i=$((i + 1))
    done
    printf 'không thuộc cấu hình này'
}

# Số thứ tự profile theo tên (không phân biệt hoa thường)
index_of_name() {
    local i=1 want
    want="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    while [ "$i" -le "$PROFILE_COUNT" ]; do
        if [ "$(field "$i" NAME | tr '[:upper:]' '[:lower:]')" = "$want" ]; then
            printf '%s' "$i"; return 0
        fi
        i=$((i + 1))
    done
    return 1
}

# Vũ khí thật sự: cặp route /1.
#
# Cả Linux lẫn macOS đều chọn đường theo LONGEST PREFIX MATCH — so độ dài prefix
# trước, cùng độ dài mới xét tới metric. Hai nửa /1 phủ đúng bằng 0.0.0.0/0
# nhưng dài hơn một bit nên thắng MỌI default route, kể cả route do DHCP của
# router cấp ngay trên chính card mình (ca mà chỉnh metric bó tay: Linux không
# hạ được dưới 0, macOS thì chỉ cho đúng một default route).
#
# Đây cũng là cách phần mềm VPN kéo toàn bộ traffic vào tunnel. Máy trong LAN
# không bị ảnh hưởng: route on-link của lớp mạng là /24, dài hơn /1 nhiều.
add_split_routes() {
    local gw="$1" iface="$2" half
    for half in $SPLIT_HALVES; do
        case "$OS" in
            Linux)
                ip route replace "$half" via "$gw" dev "$iface" 2>/dev/null || return 1 ;;
            Darwin)
                route -n delete -net "$half" >/dev/null 2>&1
                route -n add -net "$half" "$gw" >/dev/null 2>&1 || return 1 ;;
        esac
    done
    return 0
}

remove_split_routes() {
    local gw half
    for gw in $(managed_gateways); do
        for half in $SPLIT_HALVES; do
            case "$OS" in
                Linux)
                    while ip -4 route show "$half" 2>/dev/null | grep -q "via $gw "; do
                        ip route del "$half" via "$gw" 2>/dev/null || break
                    done ;;
                Darwin)
                    route -n delete -net "$half" "$gw" >/dev/null 2>&1 || true ;;
            esac
        done
    done
}

remove_managed_routes() {
    local iface="${1:-}" gw
    if [ "$OS" = "Linux" ]; then            # macOS chỉ có 1 default route
        for gw in $(managed_gateways); do
            while ip -4 route show default | grep -q "via $gw "; do
                ip route del default via "$gw" ${iface:+dev "$iface"} 2>/dev/null \
                    || ip route del default via "$gw" 2>/dev/null || break
            done
        done
    fi
    # Route /1 thì cả Linux lẫn macOS đều tự thêm được, nên cả hai đều phải dọn.
    remove_split_routes
}

apply_profile() {
    local idx="$1" gw name is_def line iface ip applied want_m foreign old_gw win_if
    gw="$(field "$idx" GW)"; name="$(field "$idx" NAME)"; is_def="$(field "$idx" DEFAULT)"

    line="$(find_lan_iface || true)"
    [ -n "$line" ] || die "Không tìm thấy card mạng nào có IP trong lớp ${LAN_PREFIX}0/24.
Máy này không nằm trực tiếp trên LAN của router nên không đổi gateway theo cách này được."
    iface="${line%% *}"; ip="${line##* }"

    case "$OS" in
        Linux)
            remove_managed_routes "$iface"
            if [ "$is_def" = "0" ]; then
                # Metric phải THẤP HƠN mọi default route đang có, nếu không thêm
                # xong vẫn thua. Tính theo bảng route thật chứ không đoán.
                foreign="$(lowest_foreign_metric)"
                want_m="$METRIC"
                if [ -n "$foreign" ]; then
                    if [ "$foreign" -le 0 ]; then
                        want_m=0
                    elif [ "$((foreign - 1))" -lt "$METRIC" ]; then
                        want_m="$((foreign - 1))"
                    fi
                fi
                info "${DIM}  · Route thấp nhất đang có: metric ${foreign:-không có} → dùng metric $want_m${RST}"
                ip route add default via "$gw" dev "$iface" metric "$want_m" \
                    || die "Không thêm được route qua $gw trên $iface"
            fi
            ip -4 neigh flush all 2>/dev/null || true
            ;;
        Darwin)
            old_gw="$(current_gateway)"
            route -n change default "$gw" >/dev/null 2>&1 \
                || route -n add default "$gw" >/dev/null 2>&1 \
                || die "Không đổi được default gateway sang $gw"
            arp -a -d >/dev/null 2>&1 || true
            dscacheutil -flushcache 2>/dev/null || true
            killall -HUP mDNSResponder 2>/dev/null || true
            ;;
    esac

    sleep 1
    applied="$(current_gateway)"

    # --- Bậc 1 (Linux): card LAN khác đang giữ default route -> dời route sang ---
    if [ "$applied" != "$gw" ] && [ "$OS" = "Linux" ] && [ "$is_def" = "0" ]; then
        win_if="$(current_iface)"
        if [ -n "$win_if" ] && [ "$win_if" != "$iface" ] && list_lan_ifaces | grep -q "^$win_if "; then
            info "${DIM}  · Card '$win_if' cũng thuộc lớp LAN và đang giữ default route — dời route sang card đó.${RST}"
            remove_managed_routes ""
            foreign="$(lowest_foreign_metric)"
            want_m="$METRIC"
            if [ -n "$foreign" ] && [ "$foreign" -gt 0 ] && [ "$((foreign - 1))" -lt "$METRIC" ]; then
                want_m="$((foreign - 1))"
            fi
            ip route add default via "$gw" dev "$win_if" metric "$want_m" 2>/dev/null || true
            iface="$win_if"
            ip="$(list_lan_ifaces | awk -v d="$win_if" '$1==d {print $2; exit}')"
            save_lan_iface "$win_if"
            sleep 1
            applied="$(current_gateway)"
        fi
    fi

    # --- Bậc 2: cặp route /1 — thắng bằng prefix dài hơn, khỏi đụng metric ---
    # Ca phổ biến nhất và cũng là ca metric bó tay: route mặc định do DHCP của
    # router cấp nằm ngay trên CÙNG card. Linux không hạ metric xuống dưới 0
    # được; macOS thì chỉ cho đúng một default route.
    if [ "$applied" != "$gw" ] && [ "$is_def" = "0" ]; then
        info "${DIM}  · Đường ra vẫn đi qua ${applied:-?} — chuyển sang cặp route /1.${RST}"
        if add_split_routes "$gw" "$iface"; then
            sleep 1
            applied="$(current_gateway)"
            [ "$applied" = "$gw" ] && info "${DIM}  · Giành được đường ra bằng cặp route /1.${RST}"
        else
            warn "  Không thêm được route /1."
        fi
    fi

    # --- Bậc 3 (macOS): xoá hẳn default route cũ rồi thêm lại của mình ---
    # Có phục hồi: thêm không được thì trả lại gateway cũ ngay, không để máy rơi
    # vào tình trạng không có default route.
    if [ "$applied" != "$gw" ] && [ "$OS" = "Darwin" ] && [ "$is_def" = "0" ]; then
        info "${DIM}  · 'route change' chưa ăn — xoá default route cũ rồi thêm lại.${RST}"
        route -n delete default >/dev/null 2>&1 || true
        if ! route -n add default "$gw" >/dev/null 2>&1; then
            [ -n "${old_gw:-}" ] && route -n add default "$old_gw" >/dev/null 2>&1 || true
            warn "  Không thêm được route mới — đã trả lại gateway cũ ${old_gw:-?}."
        fi
        sleep 1
        applied="$(current_gateway)"
    fi

    if [ "$applied" = "$gw" ]; then
        ok "✔ Đã áp dụng thành công: $name"
        info "  Card mạng : $iface ($ip)"
        info "  Gateway   : $gw"
    else
        warn "⚠ CHƯA ăn — hệ thống vẫn đi hướng khác (${applied:-không có})."
        explain_winner "$iface"
        info "  Card mạng : $iface ($ip)"
        info "  Gateway   : $gw"
        echo
        route_table_lines
    fi
    info "${DIM}  Các kết nối đang mở vẫn giữ đường cũ — đóng/mở lại trình duyệt để áp dụng.${RST}"
}

reset_all() {
    remove_managed_routes ""
    if [ "$OS" = "Darwin" ]; then
        local i
        i=1
        while [ "$i" -le "$PROFILE_COUNT" ]; do
            if [ "$(field "$i" DEFAULT)" = "1" ]; then
                route -n change default "$(field "$i" GW)" >/dev/null 2>&1 || true
                break
            fi
            i=$((i + 1))
        done
    fi
    ok "Đã gỡ mọi route do tool này thêm."
    echo
    route_table_lines
}

run_profile() {
    if [ "$(id -u)" -eq 0 ]; then
        apply_profile "$1"
    else
        info "${DIM}Cần quyền quản trị — nhập mật khẩu máy của bạn:${RST}"
        sudo "$SELF" "$(field "$1" NAME)"
    fi
}

show_public_ip() {
    local json
    if command -v curl >/dev/null 2>&1; then
        json="$(curl -s --max-time 12 'http://ip-api.com/json/?fields=status,query,isp,as' || true)"
    elif command -v wget >/dev/null 2>&1; then
        json="$(wget -qO- --timeout=12 'http://ip-api.com/json/?fields=status,query,isp,as' || true)"
    else
        warn "Không có curl/wget để tra cứu IP public."; return
    fi
    [ -n "$json" ] || { warn "Không tra cứu được IP public."; return; }
    printf '  IP public : %s\n' "$(printf '%s' "$json" | sed -n 's/.*"query":"\([^"]*\)".*/\1/p')"
    printf '  Nhà mạng  : %s\n' "$(printf '%s' "$json" | sed -n 's/.*"isp":"\([^"]*\)".*/\1/p')"
    printf '  ASN       : %s\n' "$(printf '%s' "$json" | sed -n 's/.*"as":"\([^"]*\)".*/\1/p')"
}

show_status() {
    local line iface ip gw n dev
    line="$(find_lan_iface || true)"
    if [ -n "$line" ]; then
        iface="${line%% *}"; ip="${line##* }"
        n="$(count_lan_ifaces)"
        if [ "$n" -gt 1 ]; then
            info "  Card mạng : $iface ($ip)   ${DIM}(máy có $n card trong lớp mạng này)${RST}"
        else
            info "  Card mạng : $iface ($ip)"
        fi
    else
        warn "  CẢNH BÁO  : máy không có IP trong lớp ${LAN_PREFIX}0/24"
        warn "              → không thể đổi gateway từ máy này."
    fi
    gw="$(current_gateway)"
    info "  Gateway   : ${gw:-không có}  →  $(name_of_gateway "${gw:-}")"
    dev="$(current_iface)"
    [ -n "$dev" ] && info "  Đang đi qua: $dev [$(iface_kind "$dev")]"
}

menu() {
    local i
    while :; do
        printf '\n%s%s%s\n' "$BLD" "$SYSTEM_NAME" "$RST"
        printf '%s\n' "──────────────────────────────────────────────"
        show_status
        printf '\n'
        i=1
        while [ "$i" -le "$PROFILE_COUNT" ]; do
            printf '  %s%s%s) %-22s %s%s%s\n' "$BLD" "$i" "$RST" \
                   "$(field "$i" NAME)" "$DIM" "$(field "$i" DETAIL)" "$RST"
            i=$((i + 1))
        done
        printf '  %sk%s) Kiểm tra IP public đang dùng\n' "$BLD" "$RST"
        printf '  %sr%s) Xem bảng tranh giành default route\n' "$BLD" "$RST"
        if [ "$(count_lan_ifaces)" -gt 1 ]; then
            printf '  %sc%s) Chọn card mạng dùng để đổi\n' "$BLD" "$RST"
        fi
        printf '  %sg%s) Giới thiệu\n' "$BLD" "$RST"
        printf '  %s0%s) Thoát\n' "$BLD" "$RST"
        # Dòng chân: luôn hiện, không giấu sau mục menu
        printf '\n%s  Thêm phần mềm khác: %s   •   Đặt làm công cụ riêng: %s%s\n' \
               "$DIM" "$ABOUT_TOOLS" "$ABOUT_CONTACT" "$RST"
        printf '\nChọn: '
        read -r c || exit 0
        case "$c" in
            0) exit 0 ;;
            k|K) printf '\nĐang tra cứu ...\n'; show_public_ip ;;
            r|R) printf '\n'; route_table_lines ;;
            c|C) choose_iface ;;
            g|G) show_about ;;
            ''|*[!0-9]*) warn "Lựa chọn không hợp lệ." ;;
            *)  if [ "$c" -ge 1 ] && [ "$c" -le "$PROFILE_COUNT" ]; then run_profile "$c"
                else warn "Lựa chọn không hợp lệ."; fi ;;
        esac
    done
}

case "${1:-}" in
    "")       menu ;;
    routes)   route_table_lines ;;
    reset)    if [ "$(id -u)" -ne 0 ]; then exec sudo "$SELF" reset; fi
              reset_all ;;
    status)   show_status; echo; route_table_lines; echo; show_public_ip
              if [ "$(count_lan_ifaces)" -gt 1 ]; then
                  printf '
Các card trong lớp %s0/24:
' "$LAN_PREFIX"
                  list_lan_ifaces | sed 's/^/  /'
                  printf 'Đổi card:  ./wanswitch.sh iface
'
              fi ;;
    about)    show_about ;;
    iface)    choose_iface ;;
    -h|--help|help) sed -n '2,17p' "$0" ;;
    *)
        idx="$(index_of_name "$1" || true)"
        [ -n "$idx" ] || die "Không có lựa chọn tên '$1'. Chạy không tham số để xem menu."
        if [ "$(id -u)" -ne 0 ]; then exec sudo "$SELF" "$1"; fi
        apply_profile "$idx"; echo; show_public_ip
        ;;
esac

# mikrotik-wan-selector

Cho phép máy trong LAN **tự chọn nhà mạng ra Internet** bằng cách đổi default
gateway, trên router MikroTik có nhiều đường PPPoE.

Người dùng cuối bấm một nút. Không cần biết gì về router, không cần thông tin
đăng nhập router, không cài driver hay dịch vụ nền.

---

## Nó giải quyết vấn đề gì

Nhà có 2–3 đường Internet (Viettel, VNPT, FPT). Bình thường router tự cân bằng
tải nên không biết trước mình đang ra bằng IP public của nhà mạng nào. Có những
việc bắt buộc phải cố định — chạy quảng cáo, quản lý nhiều tài khoản, test
định tuyến, tránh bị chặn theo dải IP.

Công cụ này dựng thêm các **IP gateway phụ** trên router. Máy nào đổi default
gateway sang IP tương ứng thì đi ra đúng nhà mạng đó. Máy không đổi gì thì chạy
y như cũ — **cấu hình sẵn có không bị đụng tới**.

```
Máy đặt gateway 192.168.88.2  ->  ra Internet bằng IP Viettel
Máy đặt gateway 192.168.88.3  ->  ra Internet bằng IP VNPT
Máy đặt gateway 192.168.88.4  ->  chia tải cả hai
Máy để nguyên  192.168.88.1   ->  giữ nguyên hành vi mặc định của router
```

---

## Nguyên lý

Nếu chỉ thêm nhiều IP lên cùng một interface thì cả ba địa chỉ dùng **chung một
MAC**. Khi máy gửi gói ra Internet, IP đích là `8.8.8.8` chứ không phải IP
gateway — router không có cách nào biết máy đã chọn gateway nào.

Cách giải: mỗi IP gateway đặt trên một **interface VRRP riêng**, nên có **MAC ảo
riêng biệt**. Router phân biệt qua MAC đích của khung Ethernet:

```
Máy đặt gateway .2  →  ARP hỏi "ai là 192.168.88.2"
                    →  router trả lời bằng MAC 00:00:5E:00:01:C9
                    →  máy gửi khung tới MAC đó
                    →  RouterOS quy gói này về in-interface = sel-viettel
                    →  mangle đánh dấu connection
                    →  mangle gán routing-mark
                    →  bảng định tuyến riêng chỉ có 1 route: 0.0.0.0/0 → pppoe-out1
                    →  NAT masquerade ra IP public Viettel
```

Đánh dấu ở mức **connection** chứ không phải từng gói, nên một phiên TCP/UDP đã
bắt đầu sẽ đi trọn vẹn một hướng — không vỡ session giữa chừng.

---

## Cài đặt

```bash
pip install -r requirements.txt
```

Cần Python 3.10+ và RouterOS **v7** trở lên. Router phải bật SSH; nên dùng
SSH key thay vì mật khẩu.

---

## Cách dùng nhanh — app quản trị có giao diện

```bash
python admin.py
```

Trình duyệt tự mở `http://127.0.0.1:8777`. Bốn bước trên một trang:

1. **Kết nối tới router** — điền IP, tài khoản, SSH key (hoặc mật khẩu).
2. **Đặt tên nhà mạng và lựa chọn** — tên đã được tra tự động từ IP public,
   sửa lại nếu muốn. Tick chọn những nút sẽ hiện trên máy con.
3. **Xem trước rồi áp dụng** — hiện đúng từng dòng lệnh sẽ chạy, bảng cấp phát
   IP gateway, và các cảnh báo. Bấm áp dụng thì tự chạy backup → dead-man
   switch → apply → verify → gỡ dead-man.
4. **Bộ cài cho máy con** — tự sinh ngay sau khi áp dụng thành công, bấm tải về.

Máy chủ chỉ lắng nghe trên `127.0.0.1`, không mở ra mạng. Thông tin đăng nhập
router chỉ nằm trên máy bạn và **không bao giờ được đưa vào bộ cài của máy con**.

### Chưa có khoá SSH? Tạo ngay trong app

Bấm **"Chưa có khoá? Tạo khoá mới"** ở bước 1. Không cần đi tìm `ssh-keygen`,
không cần gõ lệnh. Tạo xong app hiện luôn hai đường nạp vào router:

**Cách 1 — để tool tự nạp.** Cần đăng nhập được bằng mật khẩu một lần. Tool sẽ:

1. Đẩy file `.pub` lên router qua SFTP
2. Chạy `/user ssh-keys import ...`
3. **Mở một kết nối MỚI bằng khoá để kiểm chứng**
4. Vào được → xong, tự chuyển sang dùng khoá. Không vào được → **gỡ đúng khoá
   vừa nạp**, router trở lại y như trước

Bước 3 và 4 là phần quan trọng nhất. Nạp khoá xong mà khoá lại hỏng thì mất luôn
đường SSH, vì RouterOS tự tắt đăng nhập bằng mật khẩu cho tài khoản đã có khoá.
Tool gỡ theo **ID nội bộ** (`*1`, `*2`) chứ không theo số thứ tự — số thứ tự xê
dịch mỗi khi thêm bớt, gỡ nhầm khoá của người khác là hỏng việc.

**Cách 2 — làm tay bằng Winbox.** App hiện sẵn nút tải file `.pub`, câu lệnh
import kèm nút chép, và các bước:

1. Winbox → **Files** → kéo thả file `.pub` vào
2. Winbox → **New Terminal** → dán `/user ssh-keys import public-key-file=<tên>.pub user=admin`
3. Quay lại app bấm **Kết nối & khảo sát**

Bằng dòng lệnh:

```bash
python wanctl.py genkey -n vanphong                       # chỉ tạo, in hướng dẫn
python wanctl.py genkey -n vanphong --kind ed25519        # khoá ngắn gọn hơn, cần RouterOS v7
python wanctl.py genkey -n vanphong --install -r 192.168.88.1 -u admin -P '<mật khẩu>'
```

Mặc định là **RSA 2048** vì RouterOS đời nào cũng nhận. Ed25519 hiện đại và ngắn
hơn nhưng chỉ dùng được từ v7.

> Khoá riêng nằm trong thư mục `keys/` và **không bao giờ được phục vụ qua HTTP** —
> máy chủ chỉ cho tải file `.pub`. Đừng gửi file khoá riêng cho ai, kể cả người
> quản trị router.

### Chọn khoá SSH

Ô "Khoá SSH" tự quét `~/.ssh` và thư mục `keys/` của dự án, liệt kê sẵn từng khoá
kèm loại (RSA / ED25519) và nơi tìm thấy — bấm chọn là xong. Không thấy khoá cần
dùng thì có thêm hai lựa chọn: **chọn file từ máy** bằng hộp thoại, hoặc **nhập
đường dẫn thủ công**.

Chọn nhầm file `.pub` là lỗi phổ biến nhất, nên tool chặn ngay tại chỗ và nói rõ
phải chọn file *không* có đuôi `.pub`.

### Báo lỗi đăng nhập

Lỗi kết nối được diễn giải sang tiếng Việt kèm việc cần làm, thay vì quăng ra
nguyên câu của thư viện. Ví dụ thật:

> `BadAuthenticationType: Bad authentication type; allowed types: ['publickey']`

trở thành:

> **Router chỉ cho đăng nhập bằng SSH key, không nhận mật khẩu (tài khoản admin).**
> - Đây KHÔNG phải do SSH bị tắt — SSH vẫn đang chạy bình thường.
> - Nguyên nhân thường gặp: tài khoản này đã được nạp SSH key trước đó. RouterOS
>   tự tắt đăng nhập bằng mật khẩu cho tài khoản đã có key.
> - Cách xử lý: chọn đúng file SSH key ở ô 'Khoá SSH' phía trên.
> - Nếu không còn key: vào Winbox → System → Users → tab SSH Keys, xoá key cũ của
>   tài khoản này, hoặc nạp key mới rồi chọn ở đây.

Câu lỗi gốc vẫn giữ lại trong phần "Chi tiết kỹ thuật" gấp lại bên dưới, ai cần
mới mở. Các trường hợp được nhận diện riêng: sai mật khẩu, khoá bị router từ chối,
chọn nhầm file `.pub`, khoá có passphrase, sai IP, SSH tắt, sai cổng, và bị chặn
bởi giới hạn nguồn truy cập.

Phần còn lại của tài liệu này mô tả bản dòng lệnh `wanctl.py` — cùng một bộ lõi,
tiện khi cần tự động hoá hoặc chạy hàng loạt.

---

## Dùng bằng dòng lệnh

### 1. Khảo sát (chỉ đọc, không đụng gì vào router)

```bash
python wanctl.py survey -r 192.168.88.1 -u admin -k ~/.ssh/id_rsa
```

```
Router      : MikroTik  (hEX, RouterOS 7.x (stable))
LAN         : bridgeLAN  192.168.88.0/24  (gateway 192.168.88.1)
Ổ đĩa trống : 3.5 MiB
Đường WAN   :
   pppoe-out1     đang chạy      IP 115.74.x.x      Viettel
   pppoe-out2     đang chạy      IP 123.22.x.x      VNPT
VRID đã dùng: [201, 202]
```

Tên nhà mạng được tra tự động từ IP public qua RDAP — comment trong cấu hình
router thường sai hoặc cũ, IP public thì không nói dối.

### 2. Sinh file cấu hình mẫu

```bash
python wanctl.py init -r 192.168.88.1 -u admin -k ~/.ssh/id_rsa -o cauhinh.yaml
```

Mở ra sửa tên nhà mạng, thêm/bớt lựa chọn. Xem `examples/router-demo.yaml`.

### 3. Xem trước — quan trọng nhất

```bash
python wanctl.py plan -c cauhinh.yaml
```

In ra **đúng từng dòng lệnh** sẽ chạy, bảng cấp phát tài nguyên (IP gateway nào,
VRID nào, bảng định tuyến nào) và các cảnh báo. Chưa ghi gì lên router.

### 4. Áp dụng

```bash
python wanctl.py apply -c cauhinh.yaml --yes
```

Quy trình cố định, không có đường tắt:

```
backup  ->  dead-man switch  ->  apply  ->  verify  ->  gỡ dead-man
```

- **backup**: tạo bản nhị phân + bản export text, tải về máy rồi xoá khỏi router.
  Không tải về được thì dừng, không ghi gì.
- **dead-man switch**: một scheduler trên chính router. Nếu ta mất kết nối giữa
  chừng và không kịp gỡ, router **tự hoàn tác toàn bộ** sau vài phút. Không có
  nó thì một lỗi cấu hình mạng đồng nghĩa với việc phải chạy tới tận nơi cắm
  cáp console.
- **verify**: kiểm tra từng thứ đang *chạy*, không chỉ là đã tạo — VRRP có lên
  master không, IP có hợp lệ không, route có active không, rule mangle có bị
  invalid không.

### 5. Đóng gói app cho người dùng cuối

```bash
python wanctl.py package -c cauhinh.yaml -o dist/
```

Ra hai bộ độc lập, chép đi đâu cũng chạy:

| Gói | Bấm đúp |
|---|---|
| `dist/ChonNhaMang-Windows/` (+ `.zip`) | `ChonNhaMang.cmd` |
| `dist/ChonNhaMang-macOS/` (+ `.zip`) | `ChonNhaMang.command` |

### 6. Hoàn tác

```bash
python wanctl.py rollback -c cauhinh.yaml --yes
```

Gỡ mọi đối tượng mang nhãn của tool, trả DHCP pool về nguyên trạng. Cấu hình
sẵn có của bạn không bị đụng tới.

---

## Không sinh app riêng cho từng khách — sinh **cấu hình**

Mã nguồn client là **một bản duy nhất**. Thứ khác nhau giữa các khách chỉ là
`profiles.json` (và `profiles.sh` cho macOS/Linux) nhỏ xíu đi kèm:

```json
{
  "system_name": "Chọn nhà mạng ra Internet",
  "lan_subnet": "192.168.88.0/24",
  "profiles": [
    { "name": "Viettel",        "gateway": "192.168.88.4", "color": "#EE0033", "is_default": false },
    { "name": "VNPT",           "gateway": "192.168.88.5", "color": "#0068B3", "is_default": false },
    { "name": "Viettel + VNPT", "gateway": "192.168.88.6", "color": "#773473", "is_default": false },
    { "name": "Mặc định",       "gateway": "192.168.88.1", "color": "#546E7A", "is_default": true  }
  ]
}
```

Client tự dựng giao diện từ danh sách này — có 3 lựa chọn thì hiện 3 nút, có 5
thì hiện 5, cửa sổ tự co giãn. Sửa một lỗi là mọi khách đều được vá, không phải
build lại N bản.

Màu của lựa chọn tổ hợp được **pha tự động** từ màu các WAN thành phần: đỏ
Viettel + xanh VNPT ra tím, khác hẳn hai nút kia mà không phải chọn tay.

**Client không chứa thông tin SSH nào cả.** Nó chỉ đổi route trên máy nội bộ.
Kể cả bộ cài bị lộ ra ngoài cũng không lộ router.

---

## Ba cạm bẫy đã trả giá để biết

Cả ba đều đã được mã hoá thành kiểm tra tự động trong `verify`, không phải ghi
chú trong tài liệu.

### 1. `mark-routing` bắt buộc phải có `in-interface`

`connection-mark` gắn cho **cả hai chiều** của một connection. Nếu rule
mark-routing chỉ lọc theo `connection-mark`, thì gói **trả lời từ Internet** cũng
bị gán routing-mark. Router khi đó tra địa chỉ máy trong bảng định tuyến riêng —
bảng này chỉ có đúng một route `0.0.0.0/0`, vì trong RouterOS 7 các connected
route chỉ nằm ở bảng `main`. Kết quả: gói trả lời bị đẩy ngược ra WAN.

Triệu chứng cực dễ chẩn đoán nhầm: tool báo đổi gateway thành công, counter
mangle nhảy bình thường, connection tracking vẫn thấy gói trả lời — nhưng máy
hoàn toàn không ra được Internet, còn về mặc định thì chạy.

### 2. IP gateway phải là `/32`

Dùng `/24` sẽ sinh **connected route ECMP trùng subnet LAN** (một route cho
bridge, một cho mỗi VRRP), làm bảng ARP phình lên gấp N lần.

### 3. VRID chỉ có 1–255 và phải không đụng cái sẵn có

Tool quét `/interface vrrp` rồi mới cấp phát. IP gateway cũng vậy: ưu tiên các
địa chỉ **ngoài** mọi DHCP pool, chỉ khi hết mới lấn vào pool và khi đó kèm luôn
lệnh thu hẹp pool.

---

## Máy có nhiều card mạng

Rất thường gặp: laptop vừa cắm dây vừa bật Wi-Fi, máy bàn hai cổng LAN, máy có
card ảo của VMware/Hyper-V/WSL, máy đang chạy VPN.

Nếu tool chọn bừa một card, nó sẽ thêm route vào **card không phải card đang đi
mạng** — bấm xong báo thành công nhưng thực tế không đổi gì.

### Tự dò

Tool liệt kê **mọi** card đang có IP trong lớp mạng của router, rồi tự chọn theo
thứ tự ưu tiên:

1. Card đang **thực sự gánh default route** — đây là tín hiệu đáng tin nhất
2. Card có **interface metric thấp hơn** (Windows ưu tiên nó)
3. Xếp theo tên, cho ổn định giữa các lần chạy

### Chọn thủ công

Chỉ hiện ô chọn **khi máy thật sự có nhiều hơn một card** — máy bình thường không
bị rối thêm.

- **Windows:** ô "Card mạng dùng để đổi" ngay dưới khung trạng thái. Hoặc dòng lệnh:
  ```powershell
  .\WanSwitch.ps1 -Mode status                  # xem máy có mấy card
  .\WanSwitch.ps1 -Adapter "Ethernet 2"         # chọn card
  ```
- **macOS / Linux:** mục `c) Chọn card mạng dùng để đổi` trong menu, hoặc
  `./wanswitch.sh iface`.

Lựa chọn được **ghi nhớ** cho các lần sau:

| | Nơi lưu |
|---|---|
| Windows | `%LOCALAPPDATA%\WanSwitch\adapter.txt` |
| macOS / Linux | `~/.config/wanswitch/adapter` |

Không ghi ở cạnh script, vì thư mục có thể chỉ đọc (chép từ USB, thư mục dùng chung).

### Khi card khác vẫn thắng

Đây là ca hỏng phổ biến nhất, và nó **không phải lỗi router**: lệnh thêm route
chạy xong sạch sẽ, nhưng hệ điều hành vẫn chọn một default route khác để đi.

Windows chọn đường theo **metric hiệu dụng = RouteMetric + InterfaceMetric**,
thấp hơn thì thắng. Route tool thêm vào (`metric 1`) chỉ chắc chắn thắng route
DHCP **trên cùng một card**; nó không tự động thắng một card khác. Máy nào cũng
có thể có nhiều default route cùng lúc: VPN, Hyper-V / VMware / WSL, Wi-Fi nối
sang mạng khác, USB 4G.

Client xử lý theo thang leo, ghi rõ từng bước ra nhật ký:

| Bậc | Tình huống | Xử lý |
|-----|-----------|-------|
| 1 | Card đang thắng **cũng nằm trong lớp mạng của router** | Dời route sang đúng card đó, và ghi nhớ card này cho lần sau |
| 2 | Card đang thắng ở mạng khác | Hạ `InterfaceMetric` của card LAN xuống `metric_kẻ_thắng − 1 − RouteMetric` |
| 3 | Kẻ thắng ở metric quá thấp (VPN thường là 1) | Không vượt được — **chỉ đích danh** card, loại card, và việc cần làm |

Bậc 2 là thay đổi thường trú của hệ điều hành, nên giá trị gốc được lưu vào
`%ProgramData%\WanSwitch\ifmetric.json` và trả lại nguyên vẹn khi người dùng
chọn profile mặc định hoặc chạy `-Reset`. Trả không được thì **giữ nguyên state
file**, không xoá — xoá đi là mất luôn giá trị gốc.

Khi vẫn thua, thông báo chỉ thẳng thủ phạm chứ không bắt người dùng tự mò:

```
⚠ CHƯA ăn — hệ thống vẫn đi hướng khác.
Card 'Cloudflare WARP' [VPN] đang chiếm default route qua 172.16.0.1 với metric 5.
  Tên đầy đủ: Cloudflare WARP Tunnel Adapter
  VPN toàn tuyến kéo hết traffic vào tunnel — chọn nhà mạng không còn tác dụng.
  Hãy ngắt VPN rồi bấm lại, hoặc bật split-tunnel cho VPN đó.
  Lệnh tự kiểm tra:  .\WanSwitch.ps1 -Mode status
Bảng default route (metric thấp hơn thì thắng):
> WARP                 172.16.0.1      metric    5  (VPN)
  Ethernet             192.168.88.4    metric   16  (vật lý)
  Ethernet             192.168.88.1    metric   30  (vật lý)
```

Loại card được nhận qua tên + mô tả driver: `VPN`, `máy ảo`, `Wi-Fi`, `di động`,
`vật lý` — mỗi loại có một câu hướng dẫn riêng, vì cách xử lý khác nhau.

**VPN toàn tuyến là ca không cứu được bằng metric**, và đúng ra là không nên
cứu: nó kéo hết traffic vào tunnel nên việc chọn nhà mạng cũng không còn ý
nghĩa. Tool nói đúng như vậy thay vì cố giành route rồi báo thành công giả.

Bản macOS/Linux tương đương:

* **Linux** — đọc metric thấp nhất đang có (bỏ qua chính các gateway của tool)
  rồi thêm route của mình thấp hơn 1 bậc, thay vì dùng cứng `metric 50`. Card
  LAN khác đang giữ default route thì dời route sang card đó.
* **macOS** — không có metric; `route change` không ăn thì xoá hẳn default route
  cũ rồi thêm lại, **có phục hồi** gateway cũ nếu bước thêm hỏng (không để máy
  rơi vào trạng thái không có default route).
* Trạng thái đọc bằng `ip route get 1.1.1.1` / `route -n get default` — hỏi nhân
  hệ điều hành thực sự chọn đường nào, chính xác hơn là đọc dòng đầu bảng route.
* Lệnh: `./wanswitch.sh routes` (xem bảng) · `./wanswitch.sh reset` (gỡ sạch)

---

## Khi có router phụ nằm dưới LAN

Đây là giới hạn lớn nhất của cách "chọn bằng gateway", nên nói thẳng từ đầu.

### Vấn đề

```
MikroTik 192.168.88.1
    │
    ├── PC cắm thẳng 192.168.88.50   → đổi gateway được ✅
    │
    └── Router phụ 192.168.88.30  (NAT)
            │
            ├── PC 192.168.86.10  ┐
            ├── PC 192.168.86.11  ├── MikroTik chỉ thấy DUY NHẤT 192.168.88.30 ❌
            └── Điện thoại        ┘
```

Router phụ NAT toàn bộ máy phía sau thành **một địa chỉ nguồn duy nhất**. Đổi
gateway trên các máy đó chỉ trỏ tới router phụ, không tới được MikroTik. Đây là
giới hạn vật lý, không phải lỗi có thể vá.

### Tool phát hiện thế nào

Ba tín hiệu, độ tin cậy giảm dần:

| Tín hiệu | Ý nghĩa |
|---|---|
| Có route tĩnh trỏ vào một IP trong LAN | **Chắc chắn** là router |
| Một IP giữ trên 150 kết nối cùng lúc | **Rất có thể** đang NAT hộ nhiều máy — PC đơn lẻ hiếm khi vượt con số này |
| Tên máy / class-id DHCP giống thiết bị mạng | Chỉ là **nghi ngờ** (`googlewifi`, `tp-link`, `archer`, `deco`, `openwrt`…) |

Rồi phân làm hai loại — phân biệt này quan trọng, tránh cảnh báo thừa:

- **`nat`** — đang NAT hộ máy khác. Máy phía sau **không** chọn được. Cần xử lý.
- **`route`** — chỉ là cổng ra cho một mạng khác (NAS làm site-to-site VPN chẳng
  hạn). Về mặt kỹ thuật cũng là "router phụ", nhưng không có máy con nào nằm sau
  cần chọn nhà mạng → **không cảnh báo**.

Ví dụ kết quả dò (số liệu minh hoạ):

```
192.168.88.20    route   chắc chắn   nas           → cổng VPN, bỏ qua
192.168.88.30    nat     chắc chắn   (router Wi-Fi) → cảnh báo, cần xử lý
   • router có route tĩnh trỏ qua nó (tới 192.168.86.0/24)
   • đang giữ 315 kết nối cùng lúc — nhiều hơn một máy đơn lẻ rất nhiều
   • tên/định danh DHCP trùng danh sách hãng router phổ biến
```

### Bốn cách xử lý

**Cách 1 — chuyển router phụ sang Bridge / Access Point. Triệt để nhất.**
Tắt DHCP và NAT trên nó, nối dây LAN-sang-LAN (không dùng cổng WAN). Máy con
nhận IP thẳng từ MikroTik và dùng được y hệt máy cắm trực tiếp.
*Đánh đổi:* mất các tính năng riêng của hãng. Với Google Nest Wifi, chế độ bridge
còn tắt luôn nhiều chức năng trong app Google Home và một số đời chỉ cho bridge
khi chỉ có một điểm phát.

**Cách 2 — ghim cả cụm vào một nhà mạng cố định. Tool hỗ trợ sẵn.**
Không chọn được theo từng máy, nhưng kiểm soát được cả cụm. Trong app quản trị,
mục "Máy nằm sau router phụ" có sẵn ô chọn. Hoặc khai trong file cấu hình:

```yaml
pinned:
  - name: "Google Wifi"
    match: ["192.168.88.30"]
    profile: "VNPT"
  - name: "Camera"                 # dùng được cho cả thiết bị không cài được app
    match: ["192.168.88.80/29"]
    profile: "Viettel"
```

Cơ chế: định tuyến theo **địa chỉ nguồn** thay vì theo gateway. Rule ghim đặt
**sau** rule theo gateway và cùng đòi `connection-mark=no-mark`, nên máy cắm thẳng
đã tự chọn thì lựa chọn của nó vẫn thắng.

> Rule `mark-routing` của nhóm ghim lọc theo `src-address-list` chứ không chỉ
> `connection-mark` — nếu chỉ lọc theo connection-mark thì gói **trả lời** từ
> Internet cũng dính, và bị đẩy ngược ra WAN. `verify` có kiểm tra riêng điều này.

**Cách 3 — tắt NAT trên router phụ, để nó chỉ định tuyến.**
Một số router cho phép (OpenWrt, DD-WRT, MikroTik, vài dòng doanh nghiệp). Khi đó
MikroTik thấy đúng địa chỉ thật `192.168.86.x`, và có thể ghim theo từng dải nhỏ
hoặc từng IP. Cần thêm route tĩnh trên MikroTik trỏ về dải đó (thường đã có sẵn).
Vẫn chưa phải tự chọn được, nhưng ghim được chi tiết tới từng máy.

**Cách 4 — mỗi nhà mạng một SSID, nếu thiết bị phát Wi-Fi hỗ trợ VLAN.**
Tạo VLAN cho từng nhà mạng trên MikroTik, AP phát mỗi VLAN thành một SSID
(`Cty-Viettel`, `Cty-VNPT`). Người dùng chọn nhà mạng bằng cách **chọn mạng Wi-Fi** —
không cần cài gì, **dùng được cả trên điện thoại và máy in**.
*Đánh đổi:* cần AP hỗ trợ VLAN (Google Wifi thì không).

### Chọn cách nào

| Tình huống | Nên dùng |
|---|---|
| Có quyền cấu hình router phụ, cần chọn theo từng máy | Cách 1 |
| Không đụng được vào router phụ, chỉ cần cả cụm cố định | Cách 2 |
| Router phụ tắt được NAT | Cách 3 |
| Nhiều điện thoại/máy tính bảng, AP có VLAN | Cách 4 |

---

## Phiên bản RouterOS

Tool tự đọc phiên bản rồi **chọn cú pháp lệnh phù hợp**, vì cách tạo bảng định
tuyến khác hẳn nhau giữa hai đời:

```
v7:  /routing table add fib name=sel-viettel
     /ip route add dst-address=0.0.0.0/0 gateway=pppoe-out1 routing-table=sel-viettel

v6:  (không có menu /routing table)
     /ip route add dst-address=0.0.0.0/0 gateway=pppoe-out1 routing-mark=sel-viettel
```

Chạy nhầm cú pháp v7 trên router v6 thì lệnh đứt giữa chừng — nguy hiểm vì lúc đó
cấu hình đã ghi được một nửa. Nên việc chọn cú pháp phải xong **trước** khi sinh lệnh.

| Phiên bản | Kết luận | Ghi chú |
|---|---|---|
| **7.10 trở lên** | ✅ Dùng tốt | Đã kiểm chứng trên 7.23.2 |
| 7.0 – 7.9 | ⚠️ Chạy được | v7 đời đầu còn lỗi ở định tuyến và VRRP, nên nâng lên 7.10+ |
| 6.40 – 6.49.x | ⚠️ Chạy được, cú pháp v6 | **Chưa kiểm chứng trên thiết bị thật** — xem kỹ phần xem trước |
| Dưới 6.40 | ❌ Chặn | Nâng cấp trước đã |
| 8.x trở lên | ⚠️ Cho chạy kèm cảnh báo | Mới hơn bản tool đã kiểm chứng |

Tool **không tự nâng cấp router**. Nâng cấp bắt buộc khởi động lại và có rủi ro,
nên đó phải là quyết định của bạn.

### Nâng cấp RouterOS

**Bước 0 — kiểm tra dung lượng trống. Đây là chỗ hỏng thường gặp nhất.**

Gói cài RouterOS khoảng **12–18 MB**. Nhiều thiết bị phổ biến (hEX RB750Gr3,
hAP ac lite…) chỉ có **16 MB flash**. Còn vài MB trống là nâng cấp thất bại giữa
chừng. Tool cảnh báo sẵn khi thấy dưới 20 MB trống.

```
/system resource print                  # xem free-hdd-space
/file print                             # xem file đang chiếm chỗ
/file remove [find name~"backup"]       # xoá backup cũ trên router
/file remove [find name~".npk"]         # xoá gói cài cũ
/log print follow-only                  # nếu log ghi ra disk thì chuyển sang memory
```

**Bước 1 — backup trước, và kéo file về máy.**

```bash
python wanctl.py survey -c cauhinh.yaml     # xác nhận vào được router
```

Cách chắc chắn nhất là dùng chính tool: mỗi lần `apply` đều tự backup và tải về.
Muốn backup thủ công thì trong Winbox: **Files** → `Backup` → rồi **kéo file
`.backup` từ cửa sổ Files ra Desktop**.

**Bước 2 — nâng cấp.**

Cách dễ nhất, Winbox → **System → Packages → Check For Updates** → chọn kênh
`stable` → **Download&Install**. Router tự tải và khởi động lại (~2 phút).

Không ra Internet được thì tải tay: vào `mikrotik.com/download`, chọn đúng
**kiến trúc** của thiết bị (`/system resource print` → dòng `architecture-name`,
ví dụ hEX là `mmips`), tải gói `routeros-<phiên bản>-<kiến trúc>.npk`, rồi kéo
thả vào **Files** trong Winbox và khởi động lại router.

**Bước 3 — nâng cả firmware.**

```
/system routerboard upgrade
/system reboot
```

Bước này hay bị quên. Firmware cũ hơn RouterOS có thể gây lỗi lạ.

**Đi từ v6 lên v7 thì nâng theo bậc:** `6.x cũ` → `6.49.x` (bản long-term cuối
cùng của v6) → rồi mới lên `7.x`. Đừng nhảy thẳng. Đọc kỹ ghi chú phát hành, vì
v7 đổi khá nhiều thứ về định tuyến và tường lửa.

---

## Backup nằm ở đâu

Mỗi lần `apply`, tool tự tạo backup **trước khi ghi bất cứ thứ gì**, tải về máy
bạn, rồi xoá bản trên router cho đỡ tốn chỗ. **Tải về không thành công thì tool
dừng, không ghi gì lên router.**

```
mikrotik-wan-selector/
└─ backup/
   ├─ pre-WANSEL-20260819-085235.backup     ← ảnh nhị phân toàn bộ cấu hình
   └─ pre-WANSEL-20260819-085235.rsc        ← bản export text, để đọc và diff
```

App quản trị hiện đường dẫn đầy đủ ngay sau khi áp dụng xong.

**Khôi phục nguyên trạng** (khi muốn quay lại 100%, kể cả những thứ tool không
đụng tới): Winbox → **Files** → kéo file `.backup` vào → chọn file → **Restore**
→ router khởi động lại.

**Chỉ gỡ phần tool tạo ra** (giữ nguyên cấu hình sẵn có): bấm **Hoàn tác** trong
app quản trị, hoặc `python wanctl.py rollback -c cauhinh.yaml --yes`.

Hai file trên là **ảnh chụp tại thời điểm áp dụng**. Nên giữ lại, đừng xoá — cách
duy nhất quay về trạng thái trước đó là chúng.

---

## Giới hạn cần biết trước

- **Máy nằm sau router phụ NAT thì không dùng được.** Đổi gateway trên máy đó
  chỉ trỏ tới router phụ, không tới MikroTik. Tool tự phát hiện và cảnh báo ngay
  ở bước `plan`. Muốn dùng được phải chuyển router phụ sang chế độ bridge/AP.
- **macOS không nhớ lựa chọn lâu dài.** Rút/cắm dây mạng, đổi Wi-Fi hoặc khởi
  động lại là quay về mặc định. Trên Windows thì lựa chọn giữ nguyên qua reboot.
- **Không failover mặc định.** Chọn Viettel mà Viettel đứt thì mất mạng — đúng
  theo thiết kế, vì mục đích là cố định lớp IP public. Đặt `failover: true` nếu
  muốn tự rớt sang đường còn lại.
- **Điện thoại, máy in, thiết bị không cài được app** thì cách này không dùng
  được. Xem phần dưới.

---

## Hướng phát triển

- **Chế độ web chọn nhà mạng cho máy con**: người dùng mở trình duyệt, bấm chọn,
  backend gọi API router thêm IP máy đó vào address-list, mangle định tuyến theo
  `src-address`. Không cần cài gì, không cần quyền admin trên máy, **dùng được cả
  trên điện thoại và máy in**. Đổi lại phải có một dịch vụ chạy nền.
- **Hỗ trợ RouterOS v6.**
- **Lưu hồ sơ nhiều router** trong app quản trị, để quản lý nhiều điểm cùng lúc.

---

## Cấu trúc

```
mikrotik-wan-selector/
├─ admin.py             App quản trị có giao diện (web chạy tại chỗ)
├─ admin/index.html     Giao diện một trang, không phụ thuộc thư viện ngoài
├─ wanctl.py            CLI: survey / init / plan / apply / verify / rollback / package
├─ core/
│  ├─ model.py          Kiểu dữ liệu: Config (mong muốn) + RouterFacts (hiện trạng)
│  ├─ ros.py            Kết nối SSH RouterOS, parse `print terse`, ghi log mọi lệnh
│  ├─ survey.py         Đọc hiện trạng router (CHỈ ĐỌC)
│  ├─ plan.py           Sinh lệnh + cấp phát tài nguyên + cảnh báo xung đột
│  ├─ apply.py          Backup, dead-man switch, apply, verify, rollback
│  └─ isp.py            Đoán tên nhà mạng từ IP public qua RDAP
├─ client/              MỘT client dùng chung cho mọi khách
├─ packager/build.py    Ghép client + profiles thành bộ cài
├─ examples/            File cấu hình mẫu
├─ backup/              Backup tự động trước mỗi lần apply
└─ logs/                Nhật ký đầy đủ mọi lệnh đã chạy trên router
```

---

## Giới thiệu

Khám phá thêm nhiều phần mềm tại **<https://trinhleminhan.com/tools>**

Muốn dựng công cụ / phần mềm riêng? Gửi liên hệ tại
**<https://trinhleminhan.com/lien-he/>**

Hai đường dẫn này cũng nằm sẵn trong nút **Giới thiệu** của app máy con
(Windows) và mục `g) Giới thiệu` trong menu (macOS / Linux), cùng phần cuối
trang quản trị.

---

## Giấy phép

MIT.

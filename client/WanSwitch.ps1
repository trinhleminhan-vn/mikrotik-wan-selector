<#
    WanSwitch.ps1 — Chọn nhà mạng ra Internet  (bản dùng chung, đọc profiles.json)
    ---------------------------------------------------------------------------
    File này KHÔNG chứa cấu hình nào cả. Toàn bộ danh sách nhà mạng, IP gateway
    và lớp mạng nằm trong profiles.json đặt cạnh nó — do app admin sinh ra.
    Nhờ vậy mọi khách hàng dùng chung đúng một bản mã nguồn.

    Chạy không tham số  -> mở giao diện.
    Chạy có tham số     -> .\WanSwitch.ps1 -Mode "Viettel" | -Mode status

    LƯU Ý KHI SỬA FILE: phải lưu ở dạng UTF-8 CÓ BOM, nếu không Windows
    PowerShell 5.1 sẽ hiển thị tiếng Việt bị lỗi font.
#>
[CmdletBinding()]
param(
    [string]$Mode,
    # Tên card mạng dùng để đổi hướng — chỉ cần khi máy có nhiều card cùng nằm
    # trong lớp mạng của router. Đặt một lần là được nhớ cho các lần sau.
    [string]$Adapter,
    [switch]$Preview,
    [string]$Shot,

    # Gỡ sạch can thiệp của tool: route đã thêm + interface metric đã hạ.
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'

# ------------------------------ NẠP CẤU HÌNH ------------------------------
function Get-ScriptDir {
    if ($PSScriptRoot) { return $PSScriptRoot }
    return (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$ConfigPath = Join-Path (Get-ScriptDir) 'profiles.json'
if (-not (Test-Path $ConfigPath)) {
    throw "Không tìm thấy profiles.json cạnh file này. Hãy giữ nguyên cả thư mục khi chép đi."
}
$Cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json

$SystemName = if ($Cfg.system_name) { $Cfg.system_name } else { 'Chọn nhà mạng ra Internet' }

# "192.168.88.0/24" -> "192.168.88."
$LanPrefix = ''
if ($Cfg.lan_subnet) {
    $octets = ($Cfg.lan_subnet -split '/')[0] -split '\.'
    if ($octets.Count -ge 3) { $LanPrefix = ($octets[0..2] -join '.') + '.' }
}

$Profiles        = @($Cfg.profiles)
$DefaultProfile  = $Profiles | Where-Object { $_.is_default } | Select-Object -First 1
# Chỉ những gateway do tool tạo mới bị gỡ; gateway mặc định là của DHCP, không đụng.
$ManagedGateways = @($Profiles | Where-Object { -not $_.is_default } | ForEach-Object { $_.gateway })

# Hai nửa của toàn bộ không gian địa chỉ. Cộng lại phủ đúng bằng 0.0.0.0/0
# nhưng prefix dài hơn một bit -> luôn được chọn trước mọi default route.
$SplitHalves     = @(
    @{ Net = '0.0.0.0';   Mask = '128.0.0.0' },
    @{ Net = '128.0.0.0'; Mask = '128.0.0.0' }
)
# Mọi prefix có thể gánh đường ra Internet, dùng khi dựng bảng so sánh.
$DefaultPrefixes = @('0.0.0.0/0', '0.0.0.0/1', '128.0.0.0/1')

if (-not $Profiles -or $Profiles.Count -eq 0) { throw "profiles.json không có lựa chọn nào." }
if (-not $LanPrefix) { throw "profiles.json thiếu 'lan_subnet'." }

# Nơi ghi nhớ interface metric gốc trước khi tool hạ xuống để giành default
# route. Đặt ở ProgramData: script tự nâng quyền nên LOCALAPPDATA có thể là của
# tài khoản admin khác, ghi rồi đọc lại không thấy.
$StateFile = Join-Path $env:ProgramData 'WanSwitch\ifmetric.json'

# Nhận dạng loại card mạng qua tên/mô tả, để chỉ được đích danh thủ phạm khi
# một card khác đang giữ default route.
$AdapterKinds = [ordered]@{
    'VPN'     = 'vpn|tap-win|tap9|tunnel|wireguard|wintun|openvpn|anyconnect|forticlient|globalprotect|zscaler|tailscale|zerotier|softether|pulse secure|sonicwall|check ?point|nordlynx|proton|surfshark|expressvpn|hamachi|radmin'
    'máy ảo'  = 'hyper-v|vethernet|vmware|virtualbox|vmnet|wsl|docker|sandbox|parallels|loopback'
    'Wi-Fi'   = 'wi-?fi|wireless|802\.11|wlan'
    'di động' = 'mobile broadband|cellular|\blte\b|\b4g\b|\b5g\b|modem|rndis|huawei|dongle'
}

# ------------------------------- HÀM DÙNG CHUNG -------------------------------
function Invoke-Native {
    <#
        Gọi chương trình ngoài an toàn. Windows PowerShell 5.1 biến mỗi dòng
        stderr của native exe thành ErrorRecord; kèm $ErrorActionPreference='Stop'
        thì một cảnh báo vô hại như "Element not found" cũng làm dừng cả script.
    #>
    param([Parameter(Mandatory)][string]$Exe, [Parameter(Mandatory)][string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Arguments 2>&1 | Out-String
        return @{ Code = $LASTEXITCODE; Output = $out.Trim() }
    } catch {
        return @{ Code = -1; Output = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $old
    }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Nơi nhớ card mạng đã chọn. Không ghi cạnh script vì thư mục có thể chỉ đọc
# (chép từ USB, thư mục dùng chung...).
$SettingsFile = Join-Path $env:LOCALAPPDATA 'WanSwitch\adapter.txt'

function Get-LanAdapters {
    <#
        Trả về MỌI card mạng đang có IP trong lớp LAN của router, đã sắp xếp
        theo thứ tự ưu tiên.

        Máy có nhiều card là chuyện thường: laptop vừa cắm dây vừa bật Wi-Fi,
        máy bàn có 2 cổng LAN, máy ảo có card ảo. Chọn bừa cái đầu tiên là sai —
        route thêm vào card không phải card đang đi mạng thì đổi xong vẫn không
        có tác dụng gì.
    #>
    $addrs = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
               Where-Object { $_.IPAddress -like "$LanPrefix*" })
    if ($addrs.Count -le 1) { return $addrs }

    # Card nào đang thực sự gánh default route thì ưu tiên nhất,
    # sau đó tới card có interface metric thấp hơn (Windows ưu tiên nó).
    $defIf = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
               Sort-Object { Get-EffectiveMetric $_ } | ForEach-Object { $_.ifIndex })

    $addrs | Sort-Object `
        @{ Expression = { if ($defIf.Count -and $defIf[0] -eq $_.InterfaceIndex) { 0 } else { 1 } } },
        @{ Expression = {
              try { (Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4).InterfaceMetric }
              catch { 9999 } } },
        @{ Expression = { $_.InterfaceAlias } }
}

function Get-LanAdapter {
    <# Card đang được dùng: ưu tiên card người dùng đã chọn, nếu không thì tự chọn. #>
    $all = @(Get-LanAdapters)
    if ($all.Count -eq 0) { return $null }

    if (Test-Path $SettingsFile) {
        $saved = (Get-Content -LiteralPath $SettingsFile -Raw -ErrorAction SilentlyContinue).Trim()
        $hit = $all | Where-Object { $_.InterfaceAlias -eq $saved } | Select-Object -First 1
        if ($hit) { return $hit }        # card đã lưu vẫn còn thì dùng lại
    }
    return $all[0]
}

function Set-ChosenAdapter {
    param([string]$Alias)
    try {
        $dir = Split-Path -Parent $SettingsFile
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Set-Content -LiteralPath $SettingsFile -Value $Alias -Encoding UTF8
    } catch { }      # không lưu được thì thôi, lần sau tự chọn lại
}

function Get-EffectiveMetric {
    <#
        Con số quyết định Windows đi đường nào khi có nhiều default route:
        RouteMetric của route + InterfaceMetric của card. Thấp hơn là thắng.
    #>
    param([Parameter(Mandatory)]$Route)
    try {
        $ifm = (Get-NetIPInterface -InterfaceIndex $Route.ifIndex -AddressFamily IPv4 -ErrorAction Stop).InterfaceMetric
    } catch { $ifm = 0 }
    [int]$Route.RouteMetric + [int]$ifm
}

function Get-AdapterKind {
    param([string]$Text)
    foreach ($k in $AdapterKinds.Keys) {
        if ($Text -match $AdapterKinds[$k]) { return $k }
    }
    return 'vật lý'
}

function New-RouteInfo {
    <# Một dòng trong bảng tranh giành đường ra, đã tra sẵn tên card và metric. #>
    param([Parameter(Mandatory)][int]$IfIndex,
          [string]$NextHop     = '',
          [string]$Prefix      = '0.0.0.0/0',
          [int]   $RouteMetric = 0,
          [string]$Alias       = '')

    $desc = ''
    try {
        $na    = Get-NetAdapter -InterfaceIndex $IfIndex -ErrorAction Stop
        $Alias = $na.Name
        $desc  = $na.InterfaceDescription
    } catch { }

    $ifm = 0
    try {
        $ifm = [int](Get-NetIPInterface -InterfaceIndex $IfIndex -AddressFamily IPv4 -ErrorAction Stop).InterfaceMetric
    } catch { }

    $plen = 0
    if ($Prefix -match '/(\d+)$') { $plen = [int]$Matches[1] }

    return [pscustomobject]@{
        IfIndex     = $IfIndex
        Alias       = $Alias
        Desc        = $desc
        Kind        = (Get-AdapterKind "$Alias $desc")
        NextHop     = $NextHop
        Prefix      = $Prefix
        PrefixLen   = $plen
        RouteMetric = $RouteMetric
        IfMetric    = $ifm
        Effective   = ($RouteMetric + $ifm)
        Managed     = ($ManagedGateways -contains $NextHop)
    }
}

function Get-DefaultRoutes {
    <#
        Toàn cảnh cuộc tranh giành đường ra Internet, sắp theo đúng luật Windows
        dùng để chọn: PREFIX DÀI HƠN THẮNG TRƯỚC, cùng độ dài mới xét tới metric.
        Phải gom cả /1 chứ không riêng /0, vì tool này (và mọi phần mềm VPN)
        giành đường bằng cặp route /1.
    #>
    $out = @()
    foreach ($p in $DefaultPrefixes) {
        foreach ($r in @(Get-NetRoute -DestinationPrefix $p -ErrorAction SilentlyContinue)) {
            $out += (New-RouteInfo -IfIndex     ([int]$r.ifIndex) `
                                   -NextHop     ([string]$r.NextHop) `
                                   -Prefix      ([string]$r.DestinationPrefix) `
                                   -RouteMetric ([int]$r.RouteMetric) `
                                   -Alias       ([string]$r.InterfaceAlias))
        }
    }
    return @($out | Sort-Object @{ Expression = 'PrefixLen'; Descending = $true }, Effective, IfIndex)
}

function Get-ActiveRoute {
    <#
        Hỏi thẳng bộ định tuyến của Windows xem gói ra Internet chui qua đâu,
        thay vì tự suy từ bảng metric. Bắt buộc phải hỏi thật: route /1 thắng
        route /0 nhờ prefix dài hơn dù metric CAO hơn, nhìn metric là đoán sai.
    #>
    try {
        $found = Find-NetRoute -RemoteIPAddress '1.1.1.1' -ErrorAction Stop
    } catch { return $null }

    $best = $null
    foreach ($o in @($found)) {
        if (-not $o.PSObject.Properties['NextHop']) { continue }
        $hop = [string]$o.NextHop
        if (-not $hop) { continue }
        if ($null -eq $best) { $best = $o }
        # NextHop 0.0.0.0 là route on-link (đích nằm ngay trong LAN), không phải đường ra
        if ($hop -ne '0.0.0.0') { $best = $o; break }
    }
    if ($null -eq $best) { return $null }

    return New-RouteInfo -IfIndex     ([int]$best.ifIndex) `
                         -NextHop     ([string]$best.NextHop) `
                         -Prefix      ([string]$best.DestinationPrefix) `
                         -RouteMetric ([int]$best.RouteMetric)
}

function Get-WinnerRoute {
    <# Đường đang thắng: ưu tiên câu trả lời thật của Windows, hết cách mới suy từ metric. #>
    $act = Get-ActiveRoute
    if ($act -and $act.NextHop -ne '0.0.0.0') { return $act }
    $routes = @(Get-DefaultRoutes)
    if ($routes.Count -gt 0) { return $routes[0] }
    return $null
}

function Format-RouteLine {
    param([Parameter(Mandatory)]$R, [switch]$Winner)
    $mark = if ($Winner) { '>' } else { ' ' }
    # /0 là default route thường; /1 thì ghi rõ, vì chính nó giải thích vì sao
    # một dòng metric cao lại thắng dòng metric thấp.
    $tag  = if ($R.Prefix -and $R.Prefix -ne '0.0.0.0/0') { "  [$($R.Prefix)]" } else { '' }
    return ('{0} {1,-20} {2,-15} metric {3,4}  ({4}){5}' -f
            $mark, $R.Alias, $R.NextHop, $R.Effective, $R.Kind, $tag)
}

function Get-RouteTableLines {
    $routes = @(Get-DefaultRoutes)
    if ($routes.Count -eq 0) { return @('Không có default route nào.') }
    $act = Get-ActiveRoute
    $out = @('Bảng đường ra (prefix dài hơn thắng trước, cùng độ dài mới xét metric thấp hơn):')
    for ($i = 0; $i -lt $routes.Count; $i++) {
        $r   = $routes[$i]
        $win = if ($act) { ($r.IfIndex -eq $act.IfIndex -and $r.NextHop -eq $act.NextHop -and
                            $r.Prefix  -eq $act.Prefix) }
               else       { ($i -eq 0) }
        $out += (Format-RouteLine -R $r -Winner:$win)
    }
    return $out
}

# ------------------- Ghi nhớ / trả lại interface metric -------------------
# Hạ interface metric là cách duy nhất để thắng một card khác đang giữ default
# route, nhưng đó là thay đổi thường trú của Windows — phải lưu giá trị gốc và
# trả về đúng như cũ khi người dùng chọn lựa chọn mặc định hoặc chạy -Reset.

function Read-IfMetricState {
    if (-not (Test-Path $StateFile)) { return @() }
    try {
        $j = Get-Content -LiteralPath $StateFile -Raw -ErrorAction Stop
        if (-not $j -or -not $j.Trim()) { return @() }
        $data = ConvertFrom-Json -InputObject $j
    } catch { return @() }
    if ($null -eq $data) { return @() }

    # BẪY của Windows PowerShell 5.1: ConvertFrom-Json đẩy CẢ MẢNG ra pipeline
    # như MỘT phần tử duy nhất, nên @(...) bọc thành mảng-lồng-mảng và mọi
    # trường đọc ra đều rỗng. foreach thì duyệt đúng — dùng nó để trải phẳng.
    $out = New-Object System.Collections.ArrayList
    foreach ($e in $data) { [void]$out.Add($e) }
    return $out.ToArray()
}

function Write-IfMetricState {
    param([Parameter(Mandatory)][AllowEmptyCollection()][array]$List)
    try {
        if ($List.Count -eq 0) {
            if (Test-Path $StateFile) { Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue }
            return
        }
        $dir = Split-Path -Parent $StateFile
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        # KHÔNG dùng ,$List ở đây: toán tử phẩy bọc thêm một lớp mảng.
        $List | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $StateFile -Encoding UTF8
    } catch { }
}

function Save-IfMetricState {
    param([Parameter(Mandatory)][int]$Index, [Parameter(Mandatory)]$Iface)
    $list = @(Read-IfMetricState)
    # Chỉ lưu LẦN ĐẦU: những lần sau giá trị hiện tại đã là do tool đặt.
    if (@($list | Where-Object { [int]$_.Index -eq $Index }).Count -gt 0) { return }
    $list += [pscustomobject]@{
        Index  = $Index
        Alias  = [string]$Iface.InterfaceAlias
        Auto   = [string]$Iface.AutomaticMetric
        Metric = [int]$Iface.InterfaceMetric
    }
    Write-IfMetricState $list
}

function Restore-IfMetrics {
    <#
        Trả interface metric về trạng thái trước khi tool can thiệp. Cái nào trả
        không được thì GIỮ LẠI trong state — xoá đi là mất luôn giá trị gốc.
    #>
    $restored = @()
    $left     = @()
    foreach ($e in @(Read-IfMetricState)) {
        try {
            if ("$($e.Auto)" -eq 'Enabled') {
                Set-NetIPInterface -InterfaceIndex ([int]$e.Index) -AddressFamily IPv4 `
                                   -AutomaticMetric Enabled -ErrorAction Stop
            } else {
                Set-NetIPInterface -InterfaceIndex ([int]$e.Index) -AddressFamily IPv4 `
                                   -InterfaceMetric ([int]$e.Metric) -ErrorAction Stop
            }
            $restored += [string]$e.Alias
        } catch {
            $left += $e
        }
    }
    Write-IfMetricState $left
    return @($restored)
}

function Set-IfMetric {
    param([Parameter(Mandatory)][int]$Index, [Parameter(Mandatory)][int]$Value)
    try {
        $ifc = Get-NetIPInterface -InterfaceIndex $Index -AddressFamily IPv4 -ErrorAction Stop
    } catch { return $false }
    Save-IfMetricState -Index $Index -Iface $ifc
    try {
        Set-NetIPInterface -InterfaceIndex $Index -AddressFamily IPv4 `
                           -InterfaceMetric $Value -ErrorAction Stop
        return $true
    } catch { return $false }
}

# ------------------------------- Route -----------------------------------
function Get-CurrentProfile {
    $win = Get-WinnerRoute
    if (-not $win) { return $null }
    $Profiles | Where-Object { $_.gateway -eq $win.NextHop } | Select-Object -First 1
}

function Remove-ManagedRoutes {
    foreach ($gw in $ManagedGateways) {
        [void](Invoke-Native -Exe 'route' -Arguments @('-p','delete','0.0.0.0','mask','0.0.0.0',$gw))
        [void](Invoke-Native -Exe 'route' -Arguments @(     'delete','0.0.0.0','mask','0.0.0.0',$gw))
        foreach ($h in $SplitHalves) {
            [void](Invoke-Native -Exe 'route' -Arguments @('-p','delete',$h.Net,'mask',$h.Mask,$gw))
            [void](Invoke-Native -Exe 'route' -Arguments @(     'delete',$h.Net,'mask',$h.Mask,$gw))
        }
    }
}

function Add-ManagedRoute {
    param([Parameter(Mandatory)][string]$Gateway,
          [Parameter(Mandatory)][int]$IfIndex,
          [int]$Metric = 1)
    # metric 1 để chắc chắn thắng default route do DHCP cấp trên cùng card đó
    $r = Invoke-Native -Exe 'route' -Arguments @(
        '-p','add','0.0.0.0','mask','0.0.0.0',$Gateway,
        'metric',[string]$Metric,'if',[string]$IfIndex)
    if ($r.Code -ne 0) { throw "Không thêm được route qua ${Gateway}: $($r.Output)" }
}

function Add-SplitRoute {
    <#
        Vũ khí thật sự của tool: hai nửa 0.0.0.0/1 và 128.0.0.0/1.

        Windows chọn đường theo LONGEST PREFIX MATCH — so độ dài prefix trước,
        cùng độ dài mới xét metric. Cặp /1 này phủ đúng bằng 0.0.0.0/0 nhưng dài
        hơn một bit, nên thắng MỌI default route bất kể metric, kể cả route DHCP
        nằm ngay trên cùng card mình (ca mà hạ interface metric bó tay, vì cả
        hai cùng dịch chuyển như nhau). Phần mềm VPN kéo toàn bộ traffic vào
        tunnel cũng bằng đúng cách này.

        Máy trong LAN không bị ảnh hưởng: route on-link của lớp mạng là /24,
        dài hơn /1 nhiều nên vẫn được ưu tiên.
    #>
    param([Parameter(Mandatory)][string]$Gateway,
          [Parameter(Mandatory)][int]$IfIndex,
          [int]$Metric = 1)

    foreach ($h in $SplitHalves) {
        $r = Invoke-Native -Exe 'route' -Arguments @(
            '-p','add',$h.Net,'mask',$h.Mask,$Gateway,
            'metric',[string]$Metric,'if',[string]$IfIndex)
        if ($r.Code -ne 0) { throw "Không thêm được route $($h.Net)/1 qua ${Gateway}: $($r.Output)" }
    }
}

function Clear-NetCaches {
    [void](Invoke-Native -Exe 'arp'      -Arguments @('-d','*'))
    [void](Invoke-Native -Exe 'ipconfig' -Arguments @('/flushdns'))
    Start-Sleep -Milliseconds 400
}

function Reset-WanSwitch {
    <# Gỡ sạch dấu vết của tool: route đã thêm + interface metric đã hạ. #>
    Remove-ManagedRoutes
    $back = Restore-IfMetrics
    Clear-NetCaches
    return $back
}

function Set-WanProfile {
    param([Parameter(Mandatory)]$Profile)

    $steps = New-Object System.Collections.ArrayList
    $note  = { param([string]$m) [void]$steps.Add($m) }

    $adapter = Get-LanAdapter
    if (-not $adapter) {
        throw ("Không tìm thấy card mạng nào có IP trong lớp ${LanPrefix}0/24.`r`n" +
               "Máy này không nằm trực tiếp trên LAN của router nên không đổi gateway theo cách này được.")
    }

    Remove-ManagedRoutes
    $gw = $Profile.gateway

    # --------- Lựa chọn mặc định: trả lại nguyên trạng, không giành giật ---------
    if ($Profile.is_default) {
        foreach ($a in (Restore-IfMetrics)) {
            & $note "Đã trả interface metric của '$a' về như cũ."
        }
        Clear-NetCaches
        $applied = Get-CurrentProfile
        return @{
            Adapter  = $adapter.InterfaceAlias
            Ip       = $adapter.IPAddress
            Gateway  = $gw
            Name     = $Profile.name
            Verified = ($applied -and $applied.name -eq $Profile.name)
            Actual   = if ($applied) { $applied.name } else { 'không rõ' }
            Steps    = @($steps)
            Why      = ''
            Routes   = (Get-DefaultRoutes)
        }
    }

    Add-ManagedRoute -Gateway $gw -IfIndex $adapter.InterfaceIndex
    Clear-NetCaches
    $applied = Get-CurrentProfile
    $ok      = ($applied -and $applied.name -eq $Profile.name)

    # --------- Bậc 1: card LAN KHÁC đang giữ default route -> dời route sang ---------
    # Máy cắm 2 dây vào cùng switch, hoặc vừa cắm dây vừa bắt Wi-Fi cùng lớp mạng.
    # Không cần hạ metric, chỉ cần cắm route vào đúng card đang đi mạng.
    if (-not $ok) {
        $win    = @(Get-DefaultRoutes)[0]
        $lanIdx = @(Get-LanAdapters | ForEach-Object { [int]$_.InterfaceIndex })
        if ($win -and $win.IfIndex -ne [int]$adapter.InterfaceIndex -and $lanIdx -contains $win.IfIndex) {
            & $note ("Card '$($win.Alias)' cũng thuộc lớp LAN và đang giữ default route " +
                     "(metric $($win.Effective)) — dời route sang card đó.")
            Remove-ManagedRoutes
            Add-ManagedRoute -Gateway $gw -IfIndex $win.IfIndex
            Clear-NetCaches
            $moved = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $win.IfIndex -ErrorAction SilentlyContinue |
                     Where-Object { $_.IPAddress -like "$LanPrefix*" } | Select-Object -First 1
            if ($moved) { $adapter = $moved; Set-ChosenAdapter $moved.InterfaceAlias }
            $applied = Get-CurrentProfile
            $ok      = ($applied -and $applied.name -eq $Profile.name)
        }
    }

    # --------- Bậc 2: cặp route /1 — thắng bằng prefix dài hơn, khỏi đụng metric ---------
    # Ca PHỔ BIẾN NHẤT và cũng là ca metric bó tay: route mặc định do DHCP của
    # router cấp nằm NGAY TRÊN CÙNG card, RouteMetric của nó là 0 nên metric hiệu
    # dụng luôn thấp hơn route tool thêm vào (RouteMetric 1). Hạ interface metric
    # vô ích vì cả hai cùng tụt như nhau, mà máy chỉ có một card LAN thì cũng
    # chẳng có mạng phụ nào để ngắt.
    if (-not $ok) {
        $win = Get-WinnerRoute
        $qua = if ($win) { "qua $($win.NextHop)" } else { 'đường khác' }
        & $note "Đường ra vẫn đi $qua — chuyển sang cặp route /1, thắng bằng độ dài prefix."
        if ($win -and $win.Kind -eq 'VPN') {
            & $note ("Lưu ý: cách này kéo traffic ra khỏi VPN '$($win.Alias)'. " +
                     'Chọn lại chế độ mặc định để trả đường về cho VPN.')
        }
        try {
            Add-SplitRoute -Gateway $gw -IfIndex $adapter.InterfaceIndex
            Clear-NetCaches
            $applied = Get-CurrentProfile
            $ok      = ($applied -and $applied.name -eq $Profile.name)
            if ($ok) { & $note 'Giành được đường ra bằng cặp route /1.' }
        } catch {
            & $note "Không thêm được route /1: $($_.Exception.Message)"
        }
    }

    # --------- Bậc 3: hạ interface metric của card mình cho đủ thắng ---------
    # Chỉ còn cần tới khi có phần mềm khác (thường là VPN) cũng cắm route /1:
    # cùng độ dài prefix thì quay về đọ metric.
    if (-not $ok) {
        $routes = @(Get-DefaultRoutes)
        $win    = $routes[0]
        $mine   = @($routes | Where-Object { $_.NextHop -eq $gw }) | Select-Object -First 1

        if (-not $mine) {
            & $note "Route qua $gw không xuất hiện trong bảng định tuyến — card có thể vừa mất IP."
        } elseif ($win -and $win.IfIndex -eq $mine.IfIndex) {
            & $note ("'$($win.Alias)' thắng ngay trên chính card này (metric $($win.Effective)) — " +
                     "hạ interface metric không giải quyết được vì cả hai cùng dịch chuyển như nhau.")
        } elseif ($win) {
            $target = $win.Effective - 1 - $mine.RouteMetric
            if ($target -lt 1) {
                & $note ("'$($win.Alias)' đang ở metric $($win.Effective) — thấp tới mức không thể " +
                         "vượt bằng metric (cần interface metric $target, tối thiểu là 1).")
            } else {
                & $note ("Hạ interface metric của '$($mine.Alias)' từ $($mine.IfMetric) xuống $target " +
                         "để vượt '$($win.Alias)' (metric $($win.Effective)).")
                if (Set-IfMetric -Index $mine.IfIndex -Value $target) {
                    Clear-NetCaches
                    $applied = Get-CurrentProfile
                    $ok      = ($applied -and $applied.name -eq $Profile.name)
                    if ($ok) { & $note "Giành lại được default route." }
                } else {
                    & $note "Không đổi được interface metric (thiếu quyền quản trị?)."
                }
            }
        }
    }

    # --------- Vẫn thua: chỉ đích danh thủ phạm và việc cần làm ---------
    $why = ''
    if (-not $ok) {
        $win = Get-WinnerRoute
        if ($win) {
            $why = ("Card '$($win.Alias)' [$($win.Kind)] đang chiếm đường ra qua " +
                    "$($win.NextHop) (prefix $($win.Prefix), metric $($win.Effective)).")
            if ($win.Desc) { $why += "`r`n  Tên đầy đủ: $($win.Desc)" }
        }
        if ($win -and $win.IfIndex -eq [int]$adapter.InterfaceIndex) {
            # Cùng một card thì không có "mạng phụ" nào để rút. Bảo người ta rút
            # dây ở đây là bảo họ tự cắt mạng của chính mình.
            $why += ("`r`n  Route này nằm NGAY TRÊN card bạn đang dùng, do DHCP của router cấp — " +
                     "không phải mạng phụ, đừng rút dây.")
            $why += ("`r`n  Gỡ tay (CMD quyền Administrator):" +
                     "`r`n      route delete 0.0.0.0 mask 0.0.0.0 $($win.NextHop)")
        } elseif ($win) {
            switch ($win.Kind) {
                'VPN' {
                    $why += ("`r`n  VPN toàn tuyến kéo hết traffic vào tunnel — chọn nhà mạng không " +
                             "còn tác dụng. Hãy ngắt VPN rồi bấm lại, hoặc bật split-tunnel cho VPN đó.")
                }
                'máy ảo' {
                    $why += ("`r`n  Đây là card ảo (Hyper-V / VMware / VirtualBox / WSL). Tắt card này " +
                             "trong Network Connections, hoặc bỏ default route của nó.")
                }
                'di động' {
                    $why += "`r`n  Đang có kết nối 4G/USB dongle. Ngắt nó rồi bấm lại."
                }
                'Wi-Fi' {
                    $why += ("`r`n  Wi-Fi đang nối sang mạng khác. Tắt Wi-Fi (hoặc nối Wi-Fi vào chính " +
                             "router này) rồi bấm lại.")
                }
                default {
                    $why += "`r`n  Ngắt kết nối mạng phụ này rồi bấm lại."
                }
            }
            $why += "`r`n  Lệnh tự kiểm tra:  .\WanSwitch.ps1 -Mode status"
        } else {
            $why = 'Không đọc được bảng định tuyến.'
        }
    }

    @{
        Adapter  = $adapter.InterfaceAlias
        Ip       = $adapter.IPAddress
        Gateway  = $gw
        Name     = $Profile.name
        Verified = $ok
        Actual   = if ($applied) { $applied.name } else { 'không rõ' }
        Steps    = @($steps)
        Why      = $why
        Routes   = (Get-DefaultRoutes)
    }
}

function Get-PublicIpInfo {
    try {
        $r = Invoke-RestMethod -Uri 'http://ip-api.com/json/?fields=status,query,isp,as' -TimeoutSec 12
        if ($r.status -ne 'success') { return @('Không tra cứu được IP public.') }
        @("IP public : $($r.query)", "Nhà mạng  : $($r.isp)", "ASN       : $($r.as)")
    } catch {
        @("Không tra cứu được IP public: $($_.Exception.Message)")
    }
}

function Get-StatusLines {
    $adapter = Get-LanAdapter
    $all     = @(Get-LanAdapters)
    $cur     = Get-CurrentProfile
    $lines   = New-Object System.Collections.ArrayList

    if ($adapter) {
        $suffix = if ($all.Count -gt 1) { "   (máy có $($all.Count) card trong lớp mạng này)" } else { "" }
        [void]$lines.Add("Card mạng  :  $($adapter.InterfaceAlias)$suffix")
        [void]$lines.Add("IP của PC  :  $($adapter.IPAddress)")
    } else {
        [void]$lines.Add("CẢNH BÁO   :  PC không có IP trong lớp ${LanPrefix}0/24")
        [void]$lines.Add("              → không thể đổi gateway từ máy này.")
    }
    $routes = @(Get-DefaultRoutes)
    if ($cur) {
        [void]$lines.Add("Đang dùng  :  $($cur.name)   (gateway $($cur.gateway))")
    } elseif ($routes.Count -gt 0) {
        $w = $routes[0]
        [void]$lines.Add("Đang dùng  :  $($w.NextHop) qua '$($w.Alias)' [$($w.Kind)]  — không thuộc cấu hình này")
    } else {
        [void]$lines.Add("Đang dùng  :  không có default route")
    }
    if (@(Read-IfMetricState).Count -gt 0) {
        [void]$lines.Add("Lưu ý      :  tool đang giữ interface metric tuỳ chỉnh (chọn mặc định để trả lại)")
    }
    $lines
}

# -------------------------------- GIỚI THIỆU --------------------------------
$AboutTools   = 'https://trinhleminhan.com/tools'
$AboutContact = 'https://trinhleminhan.com/lien-he/'

function Show-About {
    param([string]$ShotPath)      # có đường dẫn thì chỉ lưu ảnh rồi thoát
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $f                 = New-Object System.Windows.Forms.Form
    $f.Text            = 'Giới thiệu'
    $f.ClientSize      = New-Object System.Drawing.Size(470, 250)
    $f.StartPosition   = 'CenterParent'
    $f.FormBorderStyle = 'FixedDialog'
    $f.MaximizeBox     = $false
    $f.MinimizeBox     = $false
    $f.BackColor       = [System.Drawing.Color]::White
    $f.Font            = New-Object System.Drawing.Font('Segoe UI', 10)

    $t           = New-Object System.Windows.Forms.Label
    $t.Text      = $SystemName
    $t.Font      = New-Object System.Drawing.Font('Segoe UI', 14, [System.Drawing.FontStyle]::Bold)
    $t.Location  = New-Object System.Drawing.Point(24, 22)
    $t.Size      = New-Object System.Drawing.Size(420, 30)
    $f.Controls.Add($t)

    $d           = New-Object System.Windows.Forms.Label
    $d.Text      = "Công cụ đổi hướng ra Internet cho máy trong mạng nội bộ."
    $d.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#4B5563')
    $d.Location  = New-Object System.Drawing.Point(24, 54)
    $d.Size      = New-Object System.Drawing.Size(420, 24)
    $f.Controls.Add($d)

    $mk = {
        param($text, $url, $y)
        $l               = New-Object System.Windows.Forms.LinkLabel
        $l.Text          = $text
        $l.Location      = New-Object System.Drawing.Point(24, $y)
        $l.Size          = New-Object System.Drawing.Size(420, 24)
        $l.LinkColor     = [System.Drawing.ColorTranslator]::FromHtml('#0068B3')
        $l.Add_LinkClicked({ Start-Process $url }.GetNewClosure())
        $f.Controls.Add($l)
    }

    $c1           = New-Object System.Windows.Forms.Label
    $c1.Text      = 'Khám phá thêm nhiều phần mềm tại:'
    $c1.Location  = New-Object System.Drawing.Point(24, 96)
    $c1.Size      = New-Object System.Drawing.Size(420, 22)
    $f.Controls.Add($c1)
    & $mk $AboutTools $AboutTools 118

    $c2           = New-Object System.Windows.Forms.Label
    $c2.Text      = 'Muốn dựng công cụ / phần mềm riêng? Gửi liên hệ tại:'
    $c2.Location  = New-Object System.Drawing.Point(24, 152)
    $c2.Size      = New-Object System.Drawing.Size(420, 22)
    $f.Controls.Add($c2)
    & $mk $AboutContact $AboutContact 174

    $ok              = New-Object System.Windows.Forms.Button
    $ok.Text         = 'Đóng'
    $ok.Location     = New-Object System.Drawing.Point(356, 206)
    $ok.Size         = New-Object System.Drawing.Size(88, 32)
    $ok.Add_Click({ $f.Close() }.GetNewClosure())
    $f.Controls.Add($ok)

    if ($ShotPath) {
        $f.Show(); $f.Refresh()
        [System.Windows.Forms.Application]::DoEvents()
        $bmp = New-Object System.Drawing.Bitmap($f.Width, $f.Height)
        $f.DrawToBitmap($bmp, (New-Object System.Drawing.Rectangle(0, 0, $f.Width, $f.Height)))
        $bmp.Save($ShotPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose(); $f.Close()
        Write-Host "Đã lưu ảnh cửa sổ Giới thiệu: $ShotPath"
        return
    }
    [void]$f.ShowDialog()
}

# -------------------------------- GIAO DIỆN --------------------------------
function Show-Gui {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $C = { param($hex) [System.Drawing.ColorTranslator]::FromHtml($hex) }

    # Máy nhiều card mạng thì phải cho chọn — chỉ hiện ô chọn khi thật sự có
    # nhiều hơn một card, để máy bình thường không bị rối thêm.
    $lanAdapters = @(Get-LanAdapters)
    $needPick    = $lanAdapters.Count -gt 1
    $pickH       = if ($needPick) { 46 } else { 0 }

    $btnH = 76
    $gap  = 12
    $top  = 200 + $pickH
    $listH = $Profiles.Count * ($btnH + $gap)
    $checkY = $top + $listH + 4
    $logLblY = $checkY + 58
    $logY = $logLblY + 26
    $footY = $logY + 150 + 12
    $formH = $footY + 34

    $form                 = New-Object System.Windows.Forms.Form
    $form.Text            = $SystemName
    $form.ClientSize      = New-Object System.Drawing.Size(660, $formH)
    $form.StartPosition   = 'CenterScreen'
    $form.FormBorderStyle = 'FixedSingle'
    $form.MaximizeBox     = $false
    $form.BackColor       = & $C '#F5F6F8'
    $form.Font            = New-Object System.Drawing.Font('Segoe UI', 11)

    $header           = New-Object System.Windows.Forms.Panel
    $header.Size      = New-Object System.Drawing.Size(660, 68)
    $header.BackColor = & $C '#1F2937'
    $form.Controls.Add($header)

    $title           = New-Object System.Windows.Forms.Label
    $title.Text      = $SystemName.ToUpper()
    $title.Font      = New-Object System.Drawing.Font('Segoe UI', 16, [System.Drawing.FontStyle]::Bold)
    $title.ForeColor = [System.Drawing.Color]::White
    $title.Location  = New-Object System.Drawing.Point(24, 18)
    $title.Size      = New-Object System.Drawing.Size(612, 34)
    $header.Controls.Add($title)

    $statusBox             = New-Object System.Windows.Forms.Panel
    $statusBox.Location    = New-Object System.Drawing.Point(24, 88)
    $statusBox.Size        = New-Object System.Drawing.Size(612, 96)
    $statusBox.BackColor   = [System.Drawing.Color]::White
    $statusBox.BorderStyle = 'FixedSingle'
    $form.Controls.Add($statusBox)

    $lblStatus          = New-Object System.Windows.Forms.Label
    $lblStatus.Location = New-Object System.Drawing.Point(16, 12)
    $lblStatus.Size     = New-Object System.Drawing.Size(580, 74)
    $lblStatus.Font     = New-Object System.Drawing.Font('Consolas', 11)
    $statusBox.Controls.Add($lblStatus)

    $lblLog           = New-Object System.Windows.Forms.Label
    $lblLog.Text      = 'Nhật ký'
    $lblLog.Font      = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $lblLog.ForeColor = & $C '#4B5563'
    $lblLog.Location  = New-Object System.Drawing.Point(24, $logLblY)
    $lblLog.Size      = New-Object System.Drawing.Size(200, 24)
    $form.Controls.Add($lblLog)

    $log            = New-Object System.Windows.Forms.TextBox
    $log.Location   = New-Object System.Drawing.Point(24, $logY)
    $log.Size       = New-Object System.Drawing.Size(612, 150)
    $log.Multiline  = $true
    $log.ScrollBars = 'Vertical'
    $log.ReadOnly   = $true
    $log.BackColor  = [System.Drawing.Color]::White
    $log.Font       = New-Object System.Drawing.Font('Consolas', 10)
    $form.Controls.Add($log)

    $WriteLog = {
        param([string]$m)
        $log.AppendText("[$(Get-Date -Format 'HH:mm:ss')]  $m`r`n")
        $log.SelectionStart = $log.TextLength; $log.ScrollToCaret()
    }.GetNewClosure()

    $RefreshStatus = { $lblStatus.Text = (Get-StatusLines) -join "`r`n" }.GetNewClosure()

    # ---------- ô chọn card mạng (chỉ khi máy có nhiều card) ----------
    if ($needPick) {
        $lblNic          = New-Object System.Windows.Forms.Label
        $lblNic.Text     = 'Card mạng dùng để đổi:'
        $lblNic.Location = New-Object System.Drawing.Point(24, 196)
        $lblNic.Size     = New-Object System.Drawing.Size(170, 26)
        $lblNic.Font     = New-Object System.Drawing.Font('Segoe UI', 10)
        $form.Controls.Add($lblNic)

        $cbo               = New-Object System.Windows.Forms.ComboBox
        $cbo.Location      = New-Object System.Drawing.Point(196, 192)
        $cbo.Size          = New-Object System.Drawing.Size(440, 28)
        $cbo.DropDownStyle = 'DropDownList'
        $cbo.Font          = New-Object System.Drawing.Font('Segoe UI', 10)
        foreach ($a in $lanAdapters) {
            [void]$cbo.Items.Add("$($a.InterfaceAlias)   —   $($a.IPAddress)")
        }
        $chosen = Get-LanAdapter
        $idx = 0
        for ($i = 0; $i -lt $lanAdapters.Count; $i++) {
            if ($lanAdapters[$i].InterfaceAlias -eq $chosen.InterfaceAlias) { $idx = $i }
        }
        $cbo.SelectedIndex = $idx
        $cbo.Add_SelectedIndexChanged({
            $alias = $lanAdapters[$this.SelectedIndex].InterfaceAlias
            Set-ChosenAdapter -Alias $alias
            & $WriteLog "Đã chọn card mạng: $alias"
            & $RefreshStatus
        }.GetNewClosure())
        $form.Controls.Add($cbo)
    }

    $y = $top
    foreach ($p in $Profiles) {
        $btn                                   = New-Object System.Windows.Forms.Button
        $btn.Text                              = "$($p.name)`n$($p.detail)"
        $btn.Location                          = New-Object System.Drawing.Point(24, $y)
        $btn.Size                              = New-Object System.Drawing.Size(612, $btnH)
        $btn.FlatStyle                         = 'Flat'
        $btn.FlatAppearance.BorderSize         = 0
        $btn.BackColor                         = & $C $p.color
        $btn.ForeColor                         = [System.Drawing.Color]::White
        $btn.Font                              = New-Object System.Drawing.Font('Segoe UI', 13, [System.Drawing.FontStyle]::Bold)
        $btn.Cursor                            = [System.Windows.Forms.Cursors]::Hand
        $btn.FlatAppearance.MouseOverBackColor = [System.Windows.Forms.ControlPaint]::Dark((& $C $p.color), 0.15)
        $btn.Tag                               = $p
        $btn.Add_Click({
            $prof = $this.Tag
            try {
                & $WriteLog "Đang chuyển sang: $($prof.name) ..."
                $r = Set-WanProfile -Profile $prof
                & $WriteLog "Card mạng $($r.Adapter) ($($r.Ip))  →  gateway $($r.Gateway)"
                foreach ($s in $r.Steps) { & $WriteLog "  · $s" }
                if ($r.Verified) {
                    & $WriteLog "✔ Đã áp dụng thành công: $($r.Name)"
                } else {
                    & $WriteLog "⚠ CHƯA ăn — hệ thống vẫn đi hướng khác."
                    foreach ($line in ($r.Why -split "`r`n")) { & $WriteLog $line }
                    foreach ($line in (Get-RouteTableLines)) { & $WriteLog $line }
                }
                & $WriteLog "Các kết nối đang mở vẫn giữ đường cũ — đóng/mở lại trình duyệt để áp dụng."
                & $RefreshStatus
            } catch {
                & $WriteLog "LỖI: $($_.Exception.Message)"
                [void][System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Lỗi',
                    [System.Windows.Forms.MessageBoxButtons]::OK,
                    [System.Windows.Forms.MessageBoxIcon]::Error)
            }
        }.GetNewClosure())
        $form.Controls.Add($btn)
        $y += $btnH + $gap
    }

    $btnCheck                           = New-Object System.Windows.Forms.Button
    $btnCheck.Text                      = 'Kiểm tra IP public đang dùng'
    $btnCheck.Location                  = New-Object System.Drawing.Point(24, $checkY)
    $btnCheck.Size                      = New-Object System.Drawing.Size(486, 42)
    $btnCheck.FlatStyle                 = 'Flat'
    $btnCheck.FlatAppearance.BorderSize = 1
    $btnCheck.BackColor                 = [System.Drawing.Color]::White
    $btnCheck.Cursor                    = [System.Windows.Forms.Cursors]::Hand
    $btnCheck.Add_Click({
        $this.Enabled = $false
        & $WriteLog 'Đang tra cứu IP public ...'
        $form.Refresh()
        foreach ($l in (Get-PublicIpInfo)) { & $WriteLog $l }
        $this.Enabled = $true
    }.GetNewClosure())
    $form.Controls.Add($btnCheck)

    # ---------- nút Giới thiệu ----------
    $btnAbout                           = New-Object System.Windows.Forms.Button
    $btnAbout.Text                      = 'Giới thiệu'
    $btnAbout.Location                  = New-Object System.Drawing.Point(516, $checkY)
    $btnAbout.Size                      = New-Object System.Drawing.Size(120, 42)
    $btnAbout.FlatStyle                 = 'Flat'
    $btnAbout.FlatAppearance.BorderSize = 1
    $btnAbout.BackColor                 = [System.Drawing.Color]::White
    $btnAbout.Cursor                    = [System.Windows.Forms.Cursors]::Hand
    $btnAbout.Add_Click({ Show-About }.GetNewClosure())
    $form.Controls.Add($btnAbout)

    # ---------- dòng chân: luôn hiển thị, không giấu sau nút ----------
    $t1 = 'trinhleminhan.com/tools'
    $t2 = 'trinhleminhan.com/lien-he'

    $foot              = New-Object System.Windows.Forms.LinkLabel
    $foot.Text         = "Thêm phần mềm: $t1   •   Đặt làm công cụ riêng: $t2"
    $foot.Font         = New-Object System.Drawing.Font('Segoe UI', 9)
    $foot.ForeColor    = & $C '#6B7280'
    $foot.LinkColor    = & $C '#0068B3'
    $foot.LinkBehavior = 'HoverUnderline'
    # AutoSize rồi tự canh giữa: khung cố định sẽ cắt cụt link khi tên miền dài
    $foot.AutoSize     = $true
    $foot.Links.Clear()
    [void]$foot.Links.Add($foot.Text.IndexOf($t1), $t1.Length, $AboutTools)
    [void]$foot.Links.Add($foot.Text.IndexOf($t2), $t2.Length, $AboutContact)
    $foot.Add_LinkClicked({
        param($sender, $e)
        try { Start-Process ([string]$e.Link.LinkData) } catch { }
    })
    $form.Controls.Add($foot)
    $foot.Location = New-Object System.Drawing.Point(
        [int](($form.ClientSize.Width - $foot.Width) / 2), $footY)

    & $RefreshStatus
    & $WriteLog 'Sẵn sàng. Bấm một nút để đổi hướng ra Internet.'

    if ($Shot) {
        $form.Show(); $form.Refresh()
        [System.Windows.Forms.Application]::DoEvents()
        $bmp = New-Object System.Drawing.Bitmap($form.Width, $form.Height)
        $form.DrawToBitmap($bmp, (New-Object System.Drawing.Rectangle(0, 0, $form.Width, $form.Height)))
        $bmp.Save($Shot, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose(); $form.Close()
        Write-Host "Đã lưu ảnh giao diện: $Shot"
        return
    }
    [void]$form.ShowDialog()
}

# ---------------------------------- CHẠY ----------------------------------
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

if ($Adapter) {
    Set-ChosenAdapter -Alias $Adapter
    Write-Host "Đã ghi nhớ card mạng: $Adapter"
}

if ($Mode -eq 'about' -and $Shot) { Show-About -ShotPath $Shot; exit }

if ($Mode -eq 'about') {
    Write-Host $SystemName
    Write-Host ''
    Write-Host "Khám phá thêm nhiều phần mềm tại:  $AboutTools"
    Write-Host "Muốn dựng công cụ riêng, liên hệ:  $AboutContact"
    exit
}

if ($Mode -eq 'status') {
    Write-Host ((Get-StatusLines) -join "`r`n")
    $all = @(Get-LanAdapters)
    if ($all.Count -gt 1) {
        Write-Host ''
        Write-Host "Máy có $($all.Count) card mạng trong lớp ${LanPrefix}0/24:"
        foreach ($a in $all) {
            $mark = if ($a.InterfaceAlias -eq (Get-LanAdapter).InterfaceAlias) { '  <-- đang dùng' } else { '' }
            Write-Host ("  {0,-24} {1}{2}" -f $a.InterfaceAlias, $a.IPAddress, $mark)
        }
        Write-Host 'Đổi card:  .\WanSwitch.ps1 -Adapter "<tên card>"'
    }
    Write-Host ''
    Write-Host ((Get-RouteTableLines) -join "`r`n")
    Write-Host ''
    Write-Host ((Get-PublicIpInfo) -join "`r`n")
    exit
}

if ($Preview -or $Shot) { Show-Gui; exit }

if (-not (Test-Admin)) {
    Write-Host 'Cần quyền Administrator. Đang mở lại với quyền quản trị...' -ForegroundColor Yellow
    $argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'))
    if ($Mode) { $argList += @('-Mode', $Mode) }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

if ($Reset) {
    $back = Reset-WanSwitch
    Write-Host 'Đã gỡ mọi route do tool này thêm.' -ForegroundColor Green
    if ($back.Count -gt 0) {
        Write-Host ("Đã trả interface metric về như cũ cho: " + ($back -join ', ')) -ForegroundColor Green
    } else {
        Write-Host 'Không có interface metric nào cần trả lại.'
    }
    Write-Host ''
    Write-Host ((Get-RouteTableLines) -join "`r`n")
    exit
}

if (-not $Mode) { Show-Gui; exit }

$target = $Profiles | Where-Object { $_.name -eq $Mode } | Select-Object -First 1
if (-not $target) {
    Write-Host "Không có lựa chọn tên '$Mode'. Các lựa chọn hợp lệ:" -ForegroundColor Red
    $Profiles | ForEach-Object { Write-Host "  - $($_.name)" }
    exit 1
}

$r = Set-WanProfile -Profile $target
foreach ($s in $r.Steps) { Write-Host "  · $s" -ForegroundColor DarkGray }
if ($r.Verified) {
    Write-Host "Đã chuyển sang: $($r.Name)" -ForegroundColor Green
} else {
    Write-Host "CHƯA ăn — hệ thống vẫn đi hướng khác." -ForegroundColor Yellow
    Write-Host $r.Why -ForegroundColor Yellow
}
Write-Host "  Card mạng : $($r.Adapter) ($($r.Ip))"
Write-Host "  Gateway   : $($r.Gateway)"
Write-Host ''
Write-Host ((Get-RouteTableLines) -join "`r`n")
Write-Host ''
Write-Host ((Get-PublicIpInfo) -join "`r`n")

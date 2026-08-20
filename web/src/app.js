/* =========================================================================
   app.js — toàn bộ logic của trang sinh cấu hình.
   Chạy 100% trong trình duyệt: không có fetch, không có server, không gửi
   dữ liệu router đi đâu hết. Đây là điều kiện tiên quyết, đừng phá.

   Ba phần:
     1. parseTerse + readSurvey : đọc output "print terse" của RouterOS
     2. buildPlan               : sinh danh sách lệnh (port từ core/plan.py)
     3. makeZip                 : đóng gói app người dùng ngay trong browser
   ========================================================================= */
'use strict';

/* ===================== 1. Đọc output của RouterOS ====================== */

const FLAG_LINE = /^\s*(?:Flags|Columns)\s*:/i;
const KV = /([A-Za-z0-9_.\-]+)=("(?:[^"\\]|\\.)*"|\S*)/g;

/** Một dòng terse -> object. Giữ nguyên hành vi của core/ros.py::parse_terse. */
function parseTerse(text) {
  const rows = [];
  for (const line of String(text || '').split(/\r?\n/)) {
    if (!line.trim() || FLAG_LINE.test(line)) continue;

    const rec = {};
    let first = -1;
    KV.lastIndex = 0;
    let m;
    while ((m = KV.exec(line)) !== null) {
      if (first < 0) first = m.index;
      let val = m[2];
      if (val.length >= 2 && val.startsWith('"') && val.endsWith('"')) {
        val = val.slice(1, -1);
      }
      rec[m[1]] = val.replace(/\\"/g, '"');
    }
    if (first < 0) continue;

    // phần đứng trước cặp key=value đầu tiên là số thứ tự + các cờ
    const head = line.slice(0, first).trim().split(/\s+/).filter(Boolean);
    if (head.length) {
      rec['.id'] = head[0];
      if (head.length > 1) rec['.flags'] = head.slice(1).join('');
    }
    rows.push(rec);
  }
  return rows;
}

/** Tách output thành từng khúc theo mốc "#TÊN" mà lệnh khảo sát in ra. */
function splitSections(text) {
  const out = {};
  let cur = null;
  for (const line of String(text || '').split(/\r?\n/)) {
    const m = line.match(/^\s*#([A-Z]+)\s*$/);
    if (m) { cur = m[1]; out[cur] = out[cur] || []; continue; }
    if (cur) out[cur].push(line);
  }
  const joined = {};
  for (const k of Object.keys(out)) joined[k] = out[k].join('\n');
  return joined;
}

const PRIVATE_NETS = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
                      '100.64.0.0/10', '169.254.0.0/16', '127.0.0.0/8'];

/** Output thô -> facts. Tương đương core/survey.py::survey nhưng không có SSH. */
function readSurvey(text) {
  const s = splitSections(text);
  const f = {
    ok: false, problems: [], identity: '', version: '', board: '', freeMiB: null,
    interfaces: [], wans: [], lanInterface: '', lanSubnet: '', lanGateway: '',
    usedIps: [], usedVrids: [], routingTables: [], pools: [], mangle: [],
  };

  if (!s.IF) {
    f.problems.push('Không thấy mốc "#IF" trong dữ liệu dán vào. Hãy dán TOÀN BỘ kết quả, kể cả mấy dòng bắt đầu bằng dấu #.');
    return f;
  }

  // ---- phiên bản ----
  const ver = (s.VER || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  f.version = ver[0] || '';
  f.board   = ver[1] || '';
  if (ver[2] && /^\d+$/.test(ver[2])) f.freeMiB = Math.round(Number(ver[2]) / 1024 / 1024 * 10) / 10;

  // ---- interface ----
  const ifRows = parseTerse(s.IF);
  f.interfaces = ifRows.map(r => r.name).filter(Boolean);
  const ifByName = {};
  for (const r of ifRows) if (r.name) ifByName[r.name] = r;

  // ---- địa chỉ ----
  const addrRows = parseTerse(s.ADDR || '');
  const addrByIface = {};
  for (const r of addrRows) {
    if (!r.address) continue;
    const ip = r.address.split('/')[0];
    f.usedIps.push(ip);
    if (r.interface) (addrByIface[r.interface] = addrByIface[r.interface] || []).push(r);
  }

  // ---- đường WAN ----
  // Ba nguồn, gộp lại: pppoe-client khai báo, interface kiểu pppoe-out,
  // và bất kỳ interface nào đang mang địa chỉ IP public.
  const wanNames = new Set();
  const pppoe = {};
  for (const r of parseTerse(s.PPPOE || '')) {
    if (!r.name) continue;
    wanNames.add(r.name);
    pppoe[r.name] = { parent: r.interface || '', user: r.user || '' };
  }
  for (const r of ifRows) if ((r.type || '').startsWith('pppoe-out') && r.name) wanNames.add(r.name);
  for (const r of addrRows) {
    const ip = (r.address || '').split('/')[0];
    if (ip && r.interface && !isPrivate(ip)) wanNames.add(r.interface);
  }
  for (const name of wanNames) {
    const row = ifByName[name] || {};
    const a = (addrByIface[name] || [])[0];
    const pp = pppoe[name] || {};
    // Tên nhà mạng hiếm khi nằm trên chính interface pppoe-out. Người ta hay
    // ghi comment ở cổng vật lý, hoặc nó lộ ra trong tên đăng nhập PPPoE.
    const parentComment = pp.parent && ifByName[pp.parent] ? (ifByName[pp.parent].comment || '') : '';
    f.wans.push({
      interface: name,
      running: String(row['.flags'] || '').includes('R'),
      comment: row.comment || '',
      parent: pp.parent || '',
      parentComment: parentComment,
      user: pp.user || '',
      ip: a ? (a.address || '').split('/')[0] : '',
    });
  }
  f.wans.sort((a, b) => a.interface.localeCompare(b.interface));

  // ---- LAN: lấy theo interface của DHCP server, đó là chỗ máy con thật sự nằm ----
  const dhcp = parseTerse(s.DHCP || '');
  const cand = [];
  for (const r of dhcp) if (r.interface) cand.push(r.interface);
  for (const name of cand) {
    const a = (addrByIface[name] || []).find(x => isPrivate((x.address || '').split('/')[0]));
    if (a) { f.lanInterface = name; f.lanGateway = a.address.split('/')[0]; f.lanSubnet = cidrOf(a.address); break; }
  }
  if (!f.lanInterface) {                       // không có DHCP server -> đoán theo bridge
    for (const r of ifRows) {
      if (r.type !== 'bridge' || !r.name) continue;
      const a = (addrByIface[r.name] || []).find(x => isPrivate((x.address || '').split('/')[0]));
      if (a) { f.lanInterface = r.name; f.lanGateway = a.address.split('/')[0]; f.lanSubnet = cidrOf(a.address); break; }
    }
  }

  // ---- VRID đang dùng, bảng định tuyến, DHCP pool, mangle ----
  for (const r of parseTerse(s.VRRP || '')) if (r.vrid) f.usedVrids.push(Number(r.vrid));
  for (const r of parseTerse(s.TABLE || '')) if (r.name) f.routingTables.push(r.name);
  for (const r of parseTerse(s.POOL || '')) if (r.name) f.pools.push({ name: r.name, ranges: r.ranges || '' });
  parseTerse(s.MANGLE || '').forEach((r, i) => {
    f.mangle.push({ index: i, chain: r.chain || '', action: r.action || '', comment: r.comment || '' });
  });

  // ---- kiểm tra đủ điều kiện chưa ----
  if (!f.lanInterface) f.problems.push('Không dò ra được LAN của router (không thấy DHCP server hay bridge nào mang IP nội bộ). Khai tay ở Bước 3.');
  if (f.wans.length < 2) f.problems.push('Chỉ thấy ' + f.wans.length + ' đường WAN. Tool này dành cho router có từ 2 đường trở lên.');
  f.ok = f.problems.length === 0;
  return f;
}

/* ============================ Tiện ích IP ============================== */

function ipToInt(ip) {
  const p = String(ip).split('.');
  if (p.length !== 4) return NaN;
  let n = 0;
  for (const x of p) {
    const v = Number(x);
    if (!Number.isInteger(v) || v < 0 || v > 255) return NaN;
    n = n * 256 + v;
  }
  return n;
}
function intToIp(n) {
  return [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join('.');
}
function cidrOf(addr) {                       // "192.168.88.1/24" -> "192.168.88.0/24"
  const [ip, lenRaw] = String(addr).split('/');
  const len = Number(lenRaw || 32);
  const n = ipToInt(ip);
  if (!Number.isFinite(n)) return '';
  const mask = len === 0 ? 0 : (0xFFFFFFFF << (32 - len)) >>> 0;
  return intToIp((n & mask) >>> 0) + '/' + len;
}
function netRange(cidr) {                     // -> {first, last} địa chỉ host
  const [ip, lenRaw] = String(cidr).split('/');
  const len = Number(lenRaw || 24);
  const n = ipToInt(ip);
  if (!Number.isFinite(n)) return null;
  const mask = len === 0 ? 0 : (0xFFFFFFFF << (32 - len)) >>> 0;
  const base = (n & mask) >>> 0;
  const bcast = (base | (~mask >>> 0)) >>> 0;
  return len >= 31 ? { first: base, last: bcast } : { first: base + 1, last: bcast - 1 };
}
function inNet(ip, cidr) {
  const r = netRange(cidr), n = ipToInt(ip);
  if (!r || !Number.isFinite(n)) return false;
  const [b, lenRaw] = String(cidr).split('/');
  const len = Number(lenRaw || 24);
  const mask = len === 0 ? 0 : (0xFFFFFFFF << (32 - len)) >>> 0;
  return ((n & mask) >>> 0) === ((ipToInt(b) & mask) >>> 0);
}
function isPrivate(ip) { return PRIVATE_NETS.some(n => inNet(ip, n)); }

/** "192.168.88.10-192.168.88.254,192.168.88.2" -> [[lo,hi], ...] dạng số */
function parseRanges(text) {
  const out = [];
  for (const part of String(text || '').split(',')) {
    const t = part.trim();
    if (!t) continue;
    const [lo, hi] = t.split('-');
    const a = ipToInt(lo.trim()), b = ipToInt((hi || lo).trim());
    if (Number.isFinite(a) && Number.isFinite(b)) out.push([a, b]);
  }
  return out;
}
function inAnyRange(n, ranges) { return ranges.some(([a, b]) => n >= a && n <= b); }

function slugify(name) {
  const s = String(name || '').trim().toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return s || 'profile';
}
function uniqueName(base, taken) {
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(base + '-' + i)) i++;
  return base + '-' + i;
}
/** Trích dẫn cho RouterOS: bọc nháy kép, thoát \ và " */
function q(v) { return '"' + String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"'; }

/* ================== 2. Sinh kế hoạch (port core/plan.py) ================ */

const POLICY_ACTIONS = new Set(['mark-connection', 'mark-routing', 'mark-packet', 'route']);
const MAX_VRID = 255;

function mangleIndex(facts, tag) {
  const ours = '[' + tag + ']';
  let pos = 0;
  for (const r of facts.mangle) {
    if (r.comment.includes(ours)) continue;
    if (r.chain === 'prerouting' && POLICY_ACTIONS.has(r.action)) return pos;
    pos++;
  }
  return pos;
}

function gatewayCandidates(cfg, facts, used) {
  const r = netRange(cfg.lanSubnet);
  if (!r) return [];
  const pools = facts.pools.flatMap(p => parseRanges(p.ranges));
  const outside = [], inside = [];
  const gwInt = ipToInt(cfg.lanGateway);
  for (let n = r.first; n <= r.last; n++) {
    const ip = intToIp(n);
    if (used.has(ip) || n === gwInt) continue;
    (inAnyRange(n, pools) ? inside : outside).push(ip);
    if (outside.length + inside.length > 512) break;   // đủ xài, khỏi quét cả /16
  }
  return outside.concat(inside);
}

function planPoolShrink(cfg, facts, chosen, plan) {
  // Nếu IP gateway lỡ nằm trong DHCP pool thì phải thu hẹp pool lại, không thì
  // router cấp đúng cái IP đó cho một máy khác và mọi thứ đứt ngang.
  for (const pool of facts.pools) {
    const ranges = parseRanges(pool.ranges);
    if (!ranges.length) continue;
    let changed = false;
    let out = ranges.map(x => x.slice());
    for (const ip of chosen) {
      const n = ipToInt(ip);
      const next = [];
      for (const [a, b] of out) {
        if (n < a || n > b) { next.push([a, b]); continue; }
        changed = true;
        if (n > a) next.push([a, n - 1]);
        if (n < b) next.push([n + 1, b]);
      }
      out = next;
    }
    if (changed) {
      const txt = out.map(([a, b]) => a === b ? intToIp(a) : intToIp(a) + '-' + intToIp(b)).join(',');
      plan.poolChanges.push([pool.name, pool.ranges, txt]);
    }
  }
}

/**
 * cfg = { tag, prefix, lanInterface, lanSubnet, lanGateway, vridBase,
 *         failover, checkGateway, systemName, bypassNetworks,
 *         wans:[{interface,name,color}], profiles:[{name,wans:[iface],isDefault,color,detail}] }
 */
function buildPlan(cfg, facts) {
  const plan = { commands: [], warnings: [], errors: [], allocations: [], poolChanges: [], dialect: 'v7' };
  const tag = cfg.tag, mark = '[' + tag + ']';

  if (!cfg.wans.length) plan.errors.push('Chưa chọn đường WAN nào.');
  if (!cfg.profiles.length) plan.errors.push('Chưa có nút nào.');
  if (!cfg.lanInterface || !cfg.lanSubnet || !cfg.lanGateway) {
    plan.errors.push('Thiếu thông tin LAN (interface / lớp mạng / gateway).');
  }
  const names = cfg.profiles.map(p => p.name.trim().toLowerCase());
  if (new Set(names).size !== names.length) plan.errors.push('Có hai nút trùng tên.');
  if (cfg.profiles.filter(p => p.isDefault).length > 1) plan.errors.push('Chỉ được một nút "mặc định".');
  if (plan.errors.length) return plan;

  // RouterOS v6 không có menu /routing table; câu lệnh khác hẳn.
  const major = Number(String(facts.version || '7').split('.')[0]) || 7;
  plan.dialect = major >= 7 ? 'v7' : 'v6';

  /* ---------------- cấp phát tài nguyên ---------------- */
  const usedIps = new Set(facts.usedIps);
  const usedVrids = new Set(facts.usedVrids);
  const usedIfaces = new Set(facts.interfaces);
  const usedTables = new Set(facts.routingTables);

  const candidates = gatewayCandidates(cfg, facts, usedIps);
  const need = cfg.profiles.filter(p => !p.isDefault && !p.gateway);
  if (candidates.length < need.length) {
    plan.errors.push('Không đủ IP trống trong ' + cfg.lanSubnet + ' cho ' + need.length + ' nút.');
    return plan;
  }

  const chosen = [];
  let vrid = Math.max(cfg.vridBase || 200, 1);
  let ci = 0;

  for (const pr of cfg.profiles) {
    const a = { profile: pr, gateway: '', vrid: 0, vrrpName: '', table: '', connMark: '' };
    if (pr.isDefault) { a.gateway = cfg.lanGateway; plan.allocations.push(a); continue; }

    if (pr.gateway) {
      a.gateway = pr.gateway;
      if (usedIps.has(pr.gateway)) plan.errors.push('Nút "' + pr.name + '": IP ' + pr.gateway + ' đã có thứ khác dùng.');
    } else {
      a.gateway = candidates[ci++];
    }
    usedIps.add(a.gateway);
    chosen.push(a.gateway);

    while (usedVrids.has(vrid) && vrid <= MAX_VRID) vrid++;
    if (vrid > MAX_VRID) { plan.errors.push('Hết VRID trống (VRRP chỉ có 1-255).'); return plan; }
    a.vrid = vrid; usedVrids.add(vrid); vrid++;

    const base = cfg.prefix + '-' + slugify(pr.name);
    a.vrrpName = uniqueName(base, usedIfaces); usedIfaces.add(a.vrrpName);
    a.table = uniqueName(base, usedTables); usedTables.add(a.table);
    a.connMark = tag + '-' + slugify(pr.name);
    plan.allocations.push(a);
  }
  if (plan.errors.length) return plan;
  planPoolShrink(cfg, facts, chosen, plan);

  /* ---------------- sinh lệnh ---------------- */
  const bypassList = tag + '-BYPASS';
  const c = plan.commands;
  let secNo = 0;
  const sec = t => { secNo++; return '# ---------- ' + secNo + '. ' + t + ' ----------'; };

  c.push('# Sinh bởi trang tao-cau-hinh, ' + new Date().toISOString().slice(0, 10));
  c.push('# Dan nguyen khoi nay vao Winbox > New Terminal.');
  c.push('# Chay lai nhieu lan duoc: buoc 1 don sach do cu mang nhan ' + mark);
  c.push('');

  c.push(sec('Dọn đối tượng cũ mang nhãn ' + mark));
  c.push('/ip firewall mangle remove [find comment~' + q(tag) + ']');
  c.push('/ip firewall address-list remove [find comment~' + q(tag) + ']');
  c.push('/ip address remove [find comment~' + q(tag) + ']');
  c.push('/interface vrrp remove [find comment~' + q(tag) + ']');
  c.push('/ip route remove [find comment~' + q(tag) + ']');
  if (plan.dialect === 'v7') c.push('/routing table remove [find comment~' + q(tag) + ']');

  c.push(sec('Dải địa chỉ nội bộ: KHÔNG áp định tuyến chọn WAN'));
  for (const n of cfg.bypassNetworks) {
    c.push('/ip firewall address-list add list=' + bypassList + ' address=' + n +
           ' comment=' + q(mark + ' dich noi bo'));
  }

  if (plan.poolChanges.length) {
    c.push(sec('Thu hẹp DHCP pool để IP gateway không bị cấp cho máy khác'));
    for (const [name, , nw] of plan.poolChanges) {
      c.push('/ip pool set [find name=' + q(name) + '] ranges=' + nw);
    }
  }

  if (plan.dialect === 'v7') {
    c.push(sec('Bảng định tuyến riêng cho từng nút'));
    for (const a of plan.allocations) {
      if (a.table) c.push('/routing table add fib name=' + a.table + ' comment=' + q(mark + ' ' + a.profile.name));
    }
  } else {
    c.push(sec('RouterOS v6: không có menu /routing table, bảng sinh ra từ routing-mark'));
  }

  c.push(sec('Route mặc định của từng bảng'));
  const chk = cfg.checkGateway ? ' check-gateway=ping' : '';
  const rt = plan.dialect === 'v7' ? 'routing-table' : 'routing-mark';
  for (const a of plan.allocations) {
    if (!a.table) continue;
    c.push('/ip route add dst-address=0.0.0.0/0 gateway=' + a.profile.wans.join(',') +
           ' ' + rt + '=' + a.table + ' distance=1' + chk + ' comment=' + q(mark + ' ' + a.profile.name));
  }

  const othersExist = plan.allocations.some(a => a.table && a.profile.wans.length < cfg.wans.length);
  if (othersExist) {
    const state = cfg.failover ? '' : ' disabled=yes';
    c.push(sec('Route dự phòng khi WAN đã chọn bị đứt (' + (cfg.failover ? 'đang BẬT' : 'đang TẮT') + ')'));
    for (const a of plan.allocations) {
      if (!a.table) continue;
      const rest = cfg.wans.map(w => w.interface).filter(i => !a.profile.wans.includes(i));
      if (!rest.length) continue;
      c.push('/ip route add dst-address=0.0.0.0/0 gateway=' + rest.join(',') + ' ' + rt + '=' + a.table +
             ' distance=10' + state + ' comment=' + q(mark + ' du phong cho ' + a.profile.name));
    }
  }

  c.push(sec('Gateway ảo: mỗi nút một địa chỉ MAC riêng (VRRP)'));
  for (const a of plan.allocations) {
    if (!a.vrrpName) continue;
    c.push('/interface vrrp add name=' + a.vrrpName + ' interface=' + cfg.lanInterface +
           ' vrid=' + a.vrid + ' priority=254 comment=' + q(mark + ' gateway ' + a.gateway + ' = ' + a.profile.name));
  }

  c.push(sec('Gán IP cho gateway ảo — BẮT BUỘC /32'));
  c.push('#   Dùng /24 sẽ sinh connected route ECMP trùng subnet LAN, làm phình bảng ARP.');
  for (const a of plan.allocations) {
    if (!a.vrrpName) continue;
    c.push('/ip address add address=' + a.gateway + '/32 network=' + a.gateway +
           ' interface=' + a.vrrpName + ' comment=' + q(mark + ' gateway ' + a.profile.name));
  }

  const idx = mangleIndex(facts, tag);
  c.push(sec('Mangle: chèn tại vị trí ' + idx + ', trước mọi rule đánh dấu sẵn có'));
  c.push('#   in-interface trên rule mark-routing là BẮT BUỘC: thiếu nó thì gói TRẢ LỜI');
  c.push('#   từ Internet cũng bị gán routing-mark và bị đẩy ngược ra WAN thay vì về máy.');
  let step = 0;
  for (const a of plan.allocations) {
    if (!a.vrrpName) continue;
    c.push('/ip firewall mangle add place-before=' + (idx + step) + ' chain=prerouting' +
           ' in-interface=' + a.vrrpName + ' connection-mark=no-mark dst-address-type=!local' +
           ' dst-address-list=!' + bypassList + ' action=mark-connection' +
           ' new-connection-mark=' + a.connMark + ' passthrough=yes' +
           ' comment=' + q(mark + ' ' + a.profile.name + ' 1/2 - danh dau connection'));
    step++;
    c.push('/ip firewall mangle add place-before=' + (idx + step) + ' chain=prerouting' +
           ' in-interface=' + a.vrrpName + ' connection-mark=' + a.connMark +
           ' action=mark-routing new-routing-mark=' + a.table + ' passthrough=yes' +
           ' comment=' + q(mark + ' ' + a.profile.name + ' 2/2 - dinh tuyen ra ' + a.profile.wans.join('+')));
    step++;
  }

  plan.warnings.push(...doctor(cfg, facts, plan));
  return plan;
}

/** Cảnh báo các cạm bẫy đã biết. Không chặn, chỉ để người cấu hình biết trước.
 *  Port từ core/plan.py::doctor, bỏ phần dò router phụ (cần SSH mới làm được). */
function doctor(cfg, facts, plan) {
  const w = [];

  if (facts.freeMiB !== null && facts.freeMiB < 1) {
    w.push('Router chỉ còn ' + facts.freeMiB + ' MiB trống. Bản backup nhị phân cỡ ~70 KiB ' +
           'vẫn đủ chỗ, nhưng nên dọn bớt /file trước khi nâng cấp RouterOS.');
  }

  const existing = facts.mangle.filter(r =>
    r.chain === 'prerouting' && POLICY_ACTIONS.has(r.action) && !r.comment.includes('[' + cfg.tag + ']'));
  if (existing.length) {
    w.push('Router đã có sẵn ' + existing.length + ' rule mangle đánh dấu định tuyến ' +
           '(nhiều khả năng là cân bằng tải PCC). Rule mới được chèn TRƯỚC chúng và dùng ' +
           'connection-mark riêng nên không xung đột, nhưng nên xem lại cho chắc.');
  }

  const down = facts.wans.filter(x => !x.running).map(x => x.interface);
  for (const wan of cfg.wans) {
    if (down.includes(wan.interface)) {
      w.push('Đường ' + wan.name + ' (' + wan.interface + ') hiện KHÔNG kết nối. ' +
             'Cấu hình vẫn tạo được nhưng chưa test được.');
    }
  }

  const n = plan.allocations.filter(a => a.vrrpName).length;
  if (n > 6) {
    w.push(n + ' gateway ảo là khá nhiều, mỗi cái gửi một gói VRRP mỗi giây lên LAN.');
  }

  // Máy nằm sau router phụ có NAT thì không chọn được. Trang này không SSH vào
  // router nên không dò được, chỉ nhắc.
  w.push('Nhắc trước: máy nằm sau một router NAT khác (bộ phát Wi-Fi riêng, router phụ) ' +
         'sẽ KHÔNG chọn được, vì MikroTik chỉ thấy đúng một địa chỉ nguồn cho cả cụm đó.');

  return w;
}

function rollbackScript(cfg, plan) {
  const tag = cfg.tag;
  const s = [
    '# Go sach moi thu mang nhan [' + tag + ']. Dan vao Winbox > New Terminal.',
    '/ip firewall mangle remove [find comment~' + q(tag) + ']',
    '/ip firewall address-list remove [find comment~' + q(tag) + ']',
    '/ip address remove [find comment~' + q(tag) + ']',
    '/interface vrrp remove [find comment~' + q(tag) + ']',
    '/ip route remove [find comment~' + q(tag) + ']',
  ];
  if (!plan || plan.dialect === 'v7') s.push('/routing table remove [find comment~' + q(tag) + ']');
  for (const [name, old] of (plan ? plan.poolChanges : [])) {
    s.push('/ip pool set [find name=' + q(name) + '] ranges=' + old);
  }
  s.push('/system scheduler remove [find name~' + q(tag) + ']');
  return s.join('\n');
}

/* ================== 3. Đóng gói app ngay trong browser ================= */

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[i] = c >>> 0;
  }
  return t;
})();
function crc32(bytes) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

/**
 * Zip "stored" (không nén). File toàn văn bản vài chục KB nên khỏi cần deflate,
 * đổi lại không phải kéo thêm thư viện nào.
 * files = [{ name, bytes:Uint8Array, exec:bool }]
 */
function makeZip(files) {
  const chunks = [], central = [];
  let offset = 0;
  const enc = new TextEncoder();
  // Giờ cố định để build lại cho ra file y hệt: 2026-01-01 00:00
  const dosTime = 0, dosDate = ((2026 - 1980) << 9) | (1 << 5) | 1;

  const u16 = v => [v & 255, (v >> 8) & 255];
  const u32 = v => [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255];

  for (const f of files) {
    const nameBytes = enc.encode(f.name);
    const crc = crc32(f.bytes);
    const local = [].concat(
      u32(0x04034b50), u16(20), u16(0x0800), u16(0),      // 0x0800 = tên file UTF-8
      u16(dosTime), u16(dosDate), u32(crc), u32(f.bytes.length), u32(f.bytes.length),
      u16(nameBytes.length), u16(0));
    chunks.push(new Uint8Array(local), nameBytes, f.bytes);

    // external attr: quyền Unix nằm ở 16 bit cao. 0755 cho file chạy được.
    const mode = f.exec ? 0o100755 : 0o100644;
    central.push([].concat(
      u32(0x02014b50), u16(0x031E), u16(20), u16(0x0800), u16(0),
      u16(dosTime), u16(dosDate), u32(crc), u32(f.bytes.length), u32(f.bytes.length),
      u16(nameBytes.length), u16(0), u16(0), u16(0), u16(0),
      u32(mode << 16), u32(offset)));
    central[central.length - 1].nameBytes = nameBytes;
    offset += local.length + nameBytes.length + f.bytes.length;
  }

  const cdStart = offset;
  let cdSize = 0;
  for (const arr of central) {
    chunks.push(new Uint8Array(arr), arr.nameBytes);
    cdSize += arr.length + arr.nameBytes.length;
  }
  chunks.push(new Uint8Array([].concat(
    u32(0x06054b50), u16(0), u16(0), u16(central.length), u16(central.length),
    u32(cdSize), u32(cdStart), u16(0))));

  return new Blob(chunks, { type: 'application/zip' });
}

/* ---- ghép nội dung file cho từng hệ điều hành ---- */

const BOM = new Uint8Array([0xEF, 0xBB, 0xBF]);
function concatBytes(...parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let i = 0;
  for (const p of parts) { out.set(p, i); i += p.length; }
  return out;
}
function toBytes(text, eol, bom) {
  let body = String(text).replace(/\r\n/g, '\n');
  if (eol === 'crlf') body = body.replace(/\n/g, '\r\n');
  const b = new TextEncoder().encode(body);
  return bom ? concatBytes(BOM, b) : b;
}

function shQuote(v) { return "'" + String(v).replace(/'/g, "'\\''") + "'"; }

function profilesJson(cfg, plan) {
  return {
    system_name: cfg.systemName,
    lan_subnet: cfg.lanSubnet,
    profiles: plan.allocations.map(a => ({
      name: a.profile.name,
      gateway: a.gateway,
      color: a.profile.color,
      detail: a.profile.detail || '',
      is_default: !!a.profile.isDefault,
    })),
  };
}

function profilesSh(data) {
  const prefix = String(data.lan_subnet || '').split('/')[0].split('.').slice(0, 3).join('.') + '.';
  const lines = ['# Sinh tự động, đừng sửa tay.',
    'SYSTEM_NAME=' + shQuote(data.system_name),
    'LAN_PREFIX=' + shQuote(prefix),
    'PROFILE_COUNT=' + data.profiles.length];
  data.profiles.forEach((p, i) => {
    const n = i + 1;
    lines.push('P' + n + '_NAME=' + shQuote(p.name),
               'P' + n + '_GW=' + shQuote(p.gateway),
               'P' + n + '_DETAIL=' + shQuote(p.detail || ''),
               'P' + n + '_DEFAULT=' + (p.is_default ? 1 : 0));
  });
  return lines.join('\n') + '\n';
}

function profileTable(data) {
  const w = Math.max(12, ...data.profiles.map(p => p.name.length));
  const pad = (s, n) => String(s) + ' '.repeat(Math.max(0, n - String(s).length));
  const rows = ['', '', 'CÁC LỰA CHỌN CỦA HỆ THỐNG NÀY',
    '------------------------------------------------------------------------',
    '  Lớp mạng LAN : ' + data.lan_subnet, '',
    '  ' + pad('Tên lựa chọn', w) + '   Gateway            Ý nghĩa'];
  for (const p of data.profiles) rows.push('  ' + pad(p.name, w) + '   ' + pad(p.gateway, 18) + ' ' + (p.detail || ''));
  rows.push('', "  Gõ đúng 'Tên lựa chọn' (cả dấu tiếng Việt) khi dùng bằng dòng lệnh.", '');
  return rows.join('\n');
}

/** platform = 'Windows' | 'macOS'. CLIENT_FILES do build.py nhúng vào trang. */
function buildPackage(cfg, plan, platform) {
  const data = profilesJson(cfg, plan);
  const json = JSON.stringify(data, null, 2);
  const readmeExtra = profileTable(data);
  const dir = 'ChonNhaMang-' + platform + '/';
  const F = CLIENT_FILES;
  const files = [];

  if (platform === 'Windows') {
    files.push({ name: dir + 'ChonNhaMang.cmd', bytes: toBytes(F['ChonNhaMang.cmd'], 'crlf', false), exec: false });
    files.push({ name: dir + 'WanSwitch.ps1', bytes: toBytes(F['WanSwitch.ps1'], 'crlf', true), exec: false });
    files.push({ name: dir + 'HUONG-DAN.txt', bytes: toBytes(F['README-Windows.txt'] + readmeExtra, 'crlf', false), exec: false });
    files.push({ name: dir + 'profiles.json', bytes: toBytes(json, 'keep', false), exec: false });
  } else {
    files.push({ name: dir + 'ChonNhaMang.command', bytes: toBytes(F['ChonNhaMang.command'], 'lf', false), exec: true });
    files.push({ name: dir + 'wanswitch.sh', bytes: toBytes(F['wanswitch.sh'], 'lf', false), exec: true });
    files.push({ name: dir + 'HUONG-DAN.txt', bytes: toBytes(F['README-macOS.txt'] + readmeExtra, 'lf', false), exec: false });
    files.push({ name: dir + 'profiles.sh', bytes: toBytes(profilesSh(data), 'lf', false), exec: false });
    files.push({ name: dir + 'profiles.json', bytes: toBytes(json, 'keep', false), exec: false });
  }
  return makeZip(files);
}

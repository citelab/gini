//! utils.zig — gRouter pure helper functions, ported from C to Zig (Z3).
//!
//! These are the small, pure, widely-used helpers (IP math, address conversions,
//! Internet checksum, byte-order). Same C ABI, so every caller links unchanged.
//! Targets little-endian hosts (x86/arm64), as GINI always has. The handlers that
//! need libc signal/timeval/printf (redefineSignalHandler, subTimeVal, printTimeVal)
//! stay in utils.c.
const std = @import("std");

const EXIT_SUCCESS: c_int = 0;

export fn netMaskLen(nmask: [*c]const u8) c_int {
    var len: c_int = 0;
    var i: usize = 0;
    while (i < 4) : (i += 1) {
        if (nmask[i] > 0) len += 1;
    }
    return len;
}

// 0 if ip_addr is in (network & netmask), -1 otherwise.
export fn compareIPUsingMask(ip_addr: [*c]const u8, network: [*c]const u8, netmask: [*c]const u8) c_int {
    var i: usize = 0;
    while (i < 4) : (i += 1) {
        if ((ip_addr[i] & netmask[i]) != network[i]) return -1;
    }
    return 0;
}

// GINI stores IP bytes least-significant-first, so dotted form prints them reversed.
export fn IP2Dot(buf: [*c]u8, ip_addr: [*c]const u8) [*c]u8 {
    var tmp: [20]u8 = undefined;
    const s = std.fmt.bufPrint(&tmp, "{d}.{d}.{d}.{d}", .{
        ip_addr[3], ip_addr[2], ip_addr[1], ip_addr[0],
    }) catch return buf;
    var i: usize = 0;
    while (i < s.len) : (i += 1) buf[i] = s[i];
    buf[s.len] = 0;
    return buf;
}

export fn Dot2IP(buf: [*c]const u8, ip_addr: [*c]u8) c_int {
    var oct: [4]u32 = .{ 0, 0, 0, 0 };
    var idx: usize = 0;
    var val: u32 = 0;
    var have = false;
    var p: usize = 0;
    while (true) : (p += 1) {
        const c = buf[p];
        if (c >= '0' and c <= '9') {
            val = val * 10 + (c - '0');
            have = true;
        } else {
            if (have and idx < 4) {
                oct[idx] = val;
                idx += 1;
            }
            val = 0;
            have = false;
            if (c == 0) break;
        }
    }
    // oct[0]=a … oct[3]=d  ->  stored reversed (ip_addr[3]=a … ip_addr[0]=d)
    ip_addr[3] = @intCast(oct[0] & 0xff);
    ip_addr[2] = @intCast(oct[1] & 0xff);
    ip_addr[1] = @intCast(oct[2] & 0xff);
    ip_addr[0] = @intCast(oct[3] & 0xff);
    return EXIT_SUCCESS;
}

// MAC treated as a plain string (endianness-independent).
export fn Colon2MAC(buf: [*c]const u8, mac_addr: [*c]u8) c_int {
    var byte: usize = 0;
    var val: u32 = 0;
    var have = false;
    var p: usize = 0;
    while (true) : (p += 1) {
        const c = buf[p];
        const hex: ?u32 = switch (c) {
            '0'...'9' => c - '0',
            'a'...'f' => c - 'a' + 10,
            'A'...'F' => c - 'A' + 10,
            else => null,
        };
        if (hex) |h| {
            val = val * 16 + h;
            have = true;
        } else {
            if (have and byte < 6) {
                mac_addr[byte] = @intCast(val & 0xff);
                byte += 1;
            }
            val = 0;
            have = false;
            if (c == 0) break;
        }
    }
    return EXIT_SUCCESS;
}

export fn MAC2Colon(buf: [*c]u8, mac_addr: [*c]const u8) [*c]u8 {
    var tmp: [24]u8 = undefined;
    const s = std.fmt.bufPrint(&tmp, "{x:0>2}:{x:0>2}:{x:0>2}:{x:0>2}:{x:0>2}:{x:0>2}", .{
        mac_addr[0], mac_addr[1], mac_addr[2], mac_addr[3], mac_addr[4], mac_addr[5],
    }) catch return buf;
    var i: usize = 0;
    while (i < s.len) : (i += 1) buf[i] = s[i];
    buf[s.len] = 0;
    return buf;
}

// extract the trailing integer from a string (e.g. "tun3" -> 3).
export fn gAtoi(str: [*c]const u8) c_int {
    var val: c_int = 0;
    var indx: c_int = 1;
    var n: usize = 0;
    while (str[n] != 0) : (n += 1) {} // strlen
    var i: isize = @intCast(n);
    while (i >= 0) : (i -= 1) {
        const c = str[@intCast(i)];
        if (c >= '0' and c <= '9') {
            val += indx * @as(c_int, @intCast(c - '0'));
            indx *= 10;
        }
    }
    return val;
}

// host<->network for a 4-byte address on a little-endian host: reverse the bytes.
export fn gHtonl(tbuf: [*c]u8, val: [*c]const u8) [*c]u8 {
    tbuf[0] = val[3];
    tbuf[1] = val[2];
    tbuf[2] = val[1];
    tbuf[3] = val[0];
    return tbuf;
}
export fn gNtohl(tbuf: [*c]u8, val: [*c]const u8) [*c]u8 {
    tbuf[0] = val[3];
    tbuf[1] = val[2];
    tbuf[2] = val[1];
    tbuf[3] = val[0];
    return tbuf;
}

// Internet checksum: sum 16-bit big-endian words, fold carries, one's complement.
export fn checksum(buf: [*c]u8, iwords: c_int) u16 {
    var cksum: u32 = 0;
    var b = buf;
    var i: c_int = 0;
    while (i < iwords) : (i += 1) {
        cksum += @as(u32, b[0]) << 8;
        cksum += b[1];
        b += 2;
    }
    while (cksum >> 16 != 0) cksum = (cksum & 0xFFFF) + (cksum >> 16);
    return @truncate(~cksum);
}

export fn ntohll(arg: u64) u64 {
    return if (@import("builtin").cpu.arch.endian() == .little) @byteSwap(arg) else arg;
}
export fn htonll(arg: u64) u64 {
    return if (@import("builtin").cpu.arch.endian() == .little) @byteSwap(arg) else arg;
}

pub const panic = std.debug.FullPanic(struct {
    fn p(_: []const u8, _: ?usize) noreturn {
        @trap();
    }
}.p);

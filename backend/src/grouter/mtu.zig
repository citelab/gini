//! mtu.zig — the gRouter MTU table, ported from C to Zig (Z3).
//!
//! Same C ABI and `mtu_entry_t` layout (is_empty:1B, mtu@4, ip_addr@8, sizeof 12), so
//! ip.c / fragment.c / cli.c link unchanged. printMTUTable stays in C. The table is a
//! direct-indexed array keyed by interface id.
const std = @import("std");

const MAX_MTU: usize = 20;
const DEFAULT_MTU: c_int = 1500;
const TRUE: u8 = 1;
const FALSE: u8 = 0;
const EXIT_SUCCESS: c_int = 0;
const EXIT_FAILURE: c_int = 1;

const MtuEntry = extern struct {
    is_empty: u8,
    mtu: c_int,
    ip_addr: [4]u8,
};

inline fn ipCopy(dst: [*c]u8, src: [*c]const u8) void {
    dst[0] = src[0];
    dst[1] = src[1];
    dst[2] = src[2];
    dst[3] = src[3];
}

export fn MTUTableInit(mtable: [*c]MtuEntry) void {
    var i: usize = 0;
    while (i < MAX_MTU) : (i += 1) mtable[i].is_empty = TRUE;
}

export fn findMTU(mtable: [*c]MtuEntry, index: c_int) c_int {
    const k: usize = @intCast(index);
    if (mtable[k].is_empty != TRUE) return mtable[k].mtu;
    return -1;
}

export fn findInterfaceIP(mtable: [*c]MtuEntry, index: c_int, ip_addr: [*c]u8) c_int {
    const k: usize = @intCast(index);
    if (mtable[k].is_empty != TRUE) {
        ipCopy(ip_addr, &mtable[k].ip_addr);
        return EXIT_SUCCESS;
    }
    return EXIT_FAILURE;
}

export fn findAllInterfaceIPs(mtable: [*c]MtuEntry, buf: [*c][4]u8) c_int {
    var count: usize = 0;
    var i: usize = 0;
    while (i < MAX_MTU) : (i += 1) {
        if (mtable[i].is_empty == FALSE) {
            ipCopy(&buf[count], &mtable[i].ip_addr);
            count += 1;
        }
    }
    return @intCast(count);
}

export fn deleteMTUEntry(mtable: [*c]MtuEntry, index: c_int) void {
    mtable[@intCast(index)].is_empty = TRUE;
}

export fn addMTUEntry(mtable: [*c]MtuEntry, index: c_int, mtu: c_int, ip_addr: [*c]u8) void {
    const k: usize = @intCast(index);
    mtable[k].is_empty = FALSE;
    mtable[k].mtu = if (mtu <= 0) DEFAULT_MTU else mtu;
    ipCopy(&mtable[k].ip_addr, ip_addr);
}

pub const panic = std.debug.FullPanic(struct {
    fn p(_: []const u8, _: ?usize) noreturn {
        @trap();
    }
}.p);

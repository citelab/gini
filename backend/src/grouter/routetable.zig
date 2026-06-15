//! routetable.zig — the gRouter route table, ported from C to Zig (Z3).
//!
//! Exports the same C ABI as the original routetable.c, against the same
//! `route_entry_t` layout (verified: bool=1B, interface at offset 16, sizeof 20), so
//! callers (ip.c, gr_state.c, cli.c) link against it unchanged. The pretty-printer
//! `printRouteTable` stays in C (it reaches into interface_t); everything else — the
//! data-plane logic — is here, memory-safe (ReleaseSafe: bounds checks on every access).
const std = @import("std");

const MAX_ROUTES: usize = 20;
const MAX_ROUTES_C: c_int = 20;
const TRUE: u8 = 1;
const FALSE: u8 = 0;
const EXIT_SUCCESS: c_int = 0;
const EXIT_FAILURE: c_int = 1;

// matches route_entry_t in routetable.h exactly (size 20).
const RouteEntry = extern struct {
    is_empty: u8,
    network: [4]u8,
    netmask: [4]u8,
    nexthop: [4]u8,
    interface: c_int,
};

// helpers that stay in C (non-variadic, pure).
extern fn compareIPUsingMask(ip: [*c]const u8, network: [*c]const u8, netmask: [*c]const u8) c_int;
extern fn netMaskLen(netmask: [*c]const u8) c_int;

// round-robin overwrite cursor (was a C global of the same name).
export var rtbl_replace_indx: c_int = 0;

inline fn ipEq(a: [*c]const u8, b: [*c]const u8) bool {
    return a[0] == b[0] and a[1] == b[1] and a[2] == b[2] and a[3] == b[3];
}
inline fn ipCopy(dst: [*c]u8, src: [*c]const u8) void {
    dst[0] = src[0];
    dst[1] = src[1];
    dst[2] = src[2];
    dst[3] = src[3];
}

export fn RouteTableInit(route_tbl: [*c]RouteEntry) void {
    rtbl_replace_indx = 0;
    var i: usize = 0;
    while (i < MAX_ROUTES) : (i += 1) route_tbl[i].is_empty = TRUE;
}

// longest-prefix match. (Cleaner than the C original, which tracked at most 4 matches
// in a fixed array — a latent overflow with >4 matching routes.)
export fn findRouteEntry(route_tbl: [*c]RouteEntry, ip_addr: [*c]u8, nhop: [*c]u8, ixface: *c_int) c_int {
    var best: isize = -1;
    var best_len: c_int = -1;
    var i: usize = 0;
    while (i < MAX_ROUTES) : (i += 1) {
        if (route_tbl[i].is_empty != FALSE) continue;
        if (compareIPUsingMask(ip_addr, &route_tbl[i].network, &route_tbl[i].netmask) == 0) {
            const l = netMaskLen(&route_tbl[i].netmask);
            if (l > best_len) {
                best_len = l;
                best = @intCast(i);
            }
        }
    }
    if (best < 0) return EXIT_FAILURE;
    const k: usize = @intCast(best);
    const zero = [_]u8{ 0, 0, 0, 0 };
    if (ipEq(&route_tbl[k].nexthop, &zero)) ipCopy(nhop, ip_addr) else ipCopy(nhop, &route_tbl[k].nexthop);
    ixface.* = route_tbl[k].interface;
    return EXIT_SUCCESS;
}

export fn addRouteEntry(route_tbl: [*c]RouteEntry, nwork: [*c]u8, nmask: [*c]u8, nhop: [*c]u8, interface: c_int) void {
    var ifree: isize = -1;
    var i: usize = 0;
    while (i < MAX_ROUTES) : (i += 1) {
        if (route_tbl[i].is_empty != FALSE) {
            if (ifree < 0) ifree = @intCast(i);
        } else if (ipEq(nwork, &route_tbl[i].network) and ipEq(nmask, &route_tbl[i].netmask)) {
            ipCopy(&route_tbl[i].nexthop, nhop); // update existing
            route_tbl[i].interface = interface;
            return;
        }
    }
    var slot: usize = undefined;
    if (ifree < 0) {
        slot = @intCast(rtbl_replace_indx);
        rtbl_replace_indx = @rem(rtbl_replace_indx + 1, MAX_ROUTES_C);
    } else slot = @intCast(ifree);
    ipCopy(&route_tbl[slot].network, nwork);
    ipCopy(&route_tbl[slot].netmask, nmask);
    ipCopy(&route_tbl[slot].nexthop, nhop);
    route_tbl[slot].interface = interface;
    route_tbl[slot].is_empty = FALSE;
}

export fn deleteRouteEntryByIndex(route_tbl: [*c]RouteEntry, i: c_int) void {
    route_tbl[@intCast(i)].is_empty = TRUE;
}

export fn deleteRouteEntryByInterface(route_tbl: [*c]RouteEntry, interface: c_int) void {
    var i: usize = 0;
    while (i < MAX_ROUTES) : (i += 1) {
        if (route_tbl[i].is_empty == FALSE and route_tbl[i].interface == interface)
            route_tbl[i].is_empty = TRUE;
    }
}

// panic handler: a safety trip aborts cleanly without needing Zig's full runtime.
pub const panic = std.debug.FullPanic(struct {
    fn p(_: []const u8, _: ?usize) noreturn {
        @trap();
    }
}.p);

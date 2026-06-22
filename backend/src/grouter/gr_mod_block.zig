//! gr_mod_block.zig — a native gRouter pipeline module, written in Zig.
//!
//! It DROPs packets whose IPv4 destination equals a configured address, and otherwise
//! lets them CONTINUE down the pipeline. It is the "hello, world" of a native module: the
//! same job as the C ACL module, but as a self-contained example of writing a gr_module_t
//! in Zig.
//!
//! It conforms to the gr_module_t C ABI (see include/gr_module.h), so the runner chains it
//! exactly like any other module. Note that it never mirrors gpacket_t: it reads the packet
//! only through the C accessor gr_pkt_ipdst(), so this file stays small and safe.
//!
//! Build: listed in build.zig ZIG_SRCS (and picked up by grouter-zig/build.sh's *.zig glob),
//! compiled -OReleaseSafe (bounds/overflow-checked) and linked alongside the C.
const std = @import("std");

// --- the slice of the C ABI this module needs (small + stable; see gr_module.h) ---
const GrVerdict = extern struct { action: c_int, out_iface: c_int };

const GrModule = extern struct {
    type: [*c]const u8,
    state: ?*anyopaque,
    init: ?*const fn (?*GrModule, [*c]const u8) callconv(.c) c_int,
    process: ?*const fn (?*GrModule, ?*anyopaque) callconv(.c) GrVerdict,
    destroy: ?*const fn (?*GrModule) callconv(.c) void,
};

const GR_CONTINUE: c_int = 0; // pass to the next module
const GR_DROP: c_int = 1; // discard the packet

const BlockState = struct { ip: u32 };

// C helpers from gr_modules.c — so we read the packet without mirroring gpacket_t in Zig.
extern fn gr_pkt_ipdst(pkt: ?*anyopaque) u32;
extern fn gr_parse_ipv4(s: [*c]const u8, out: *u32) c_int;
extern fn malloc(n: usize) ?*anyopaque;
extern fn free(p: ?*anyopaque) void;

// process(): the per-packet verdict — the heart of every module.
fn block_process(self: ?*GrModule, pkt: ?*anyopaque) callconv(.c) GrVerdict {
    const s: *BlockState = @ptrCast(@alignCast(self.?.state.?));
    if (gr_pkt_ipdst(pkt) == s.ip)
        return .{ .action = GR_DROP, .out_iface = -1 };
    return .{ .action = GR_CONTINUE, .out_iface = -1 };
}

fn block_destroy(self: ?*GrModule) callconv(.c) void {
    free(self.?.state);
    free(@ptrCast(self));
}

// gr_mod_block(ip): the constructor the registry calls (`gpipe add block <ip>`).
export fn gr_mod_block(ip: [*c]const u8) ?*GrModule {
    var parsed: u32 = 0;
    _ = gr_parse_ipv4(ip, &parsed); // 0.0.0.0 on a bad arg -> matches nothing

    const s: *BlockState = @ptrCast(@alignCast(malloc(@sizeOf(BlockState)) orelse return null));
    s.ip = parsed;

    const m: *GrModule = @ptrCast(@alignCast(malloc(@sizeOf(GrModule)) orelse {
        free(s);
        return null;
    }));
    m.type = "block";
    m.state = s;
    m.init = null;
    m.process = &block_process;
    m.destroy = &block_destroy;
    return m;
}

// a safety trip aborts cleanly without needing Zig's full runtime.
pub const panic = std.debug.FullPanic(struct {
    fn p(_: []const u8, _: ?usize) noreturn {
        @trap();
    }
}.p);

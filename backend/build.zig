//! Build the real GINI gRouter with Zig (Z0) — and, with Z1's seams, optionally
//! drop the lwIP host stack for a leaner pure forwarder.
//!
//!   zig build                       # full router (host stack on)
//!   zig build -Dhost_stack=false     # pure forwarder: no lwIP, smaller binary
//!   zig build -Dtarget=...           # cross-compile (e.g. for the fabric container)
//!
//! External deps for the linker: libslack, readline, termcap, pthread, util, m.
//! (-Dhost_stack=false also lets you drop libs lwIP would need.) The Docker build
//! (grouter-zig/Dockerfile) provides them.
const std = @import("std");

// Core gRouter — always built.
const CORE_SRCS = [_][]const u8{
    "arp.c", "classifier.c", "cli.c", "console.c", "ethernet.c", "filter.c",
    "fragment.c", "gnet.c", "grouter.c", "icmp.c", "inet_chksum.c", "info.c",
    "ip.c", "message.c", "mtu.c", "openflow_config.c",
    "openflow_ctrl_iface.c", "openflow_flowtable.c", "openflow_pkt_proc.c",
    "packetcore.c", "qdisc.c", "raw.c", "roundrobin.c", "routetable.c",
    "simplequeue.c", "tap.c", "tapio.c", "tun.c", "utils.c", "vpl.c", "wfq.c",
    // Z1 module seams
    "gr_state.c", "host_stack.c", "sdn.c",
    // Z2 module-graph runner + built-in modules + control surface
    "gr_pipeline.c", "gr_modules.c", "gr_control.c", "gr_mod_legacy.c",
    // remote control socket (interactive console + Router Lab live binding)
    "gr_rctl.c",
};

// lwIP host stack — only when host_stack is on (Z1: sealed behind host_stack.c).
const LWIP_SRCS = [_][]const u8{
    "tcp.c", "tcp_in.c", "tcp_out.c", "udp.c", "pbuf.c", "memp.c",
};

// Z3: gRouter modules ported from C to the Zig language (built as objects, C ABI).
const ZIG_SRCS = [_][]const u8{
    "src/grouter/routetable.zig",
    "src/grouter/mtu.zig",
    "src/grouter/utils.zig",
};

const SYS_LIBS = [_][]const u8{ "readline", "termcap", "slack", "pthread", "util", "m" };

// Legacy-C build flags. This is ~20k lines of pre-C99-style GINI code that
// predates modern compiler hardening; these make it build *and run* under a
// modern clang/lld the way it did under the original gcc:
//   -fcommon                            tentative globals defined in headers
//                                       (route_tbl, MTU_tbl, …) must merge under one
//                                       symbol — modern clang defaults to -fno-common,
//                                       which turns them into duplicate-symbol link errors.
//   -Wno-implicit-function-declaration  K&R-style cross-TU calls (consoleRestart,
//                                       openflow_config_* …) are warnings, not errors.
//   -Wno-int-conversion                 legacy int<->pointer casts.
//   -fno-sanitize=undefined             zig cc traps on UB by default; the legacy code
//                                       has benign UB (unaligned casts, bitfields) that
//                                       would SIGILL/SIGTRAP at runtime otherwise.
//   -fno-stack-protector / _FORTIFY=0   the code predates stack-protector/_FORTIFY and
//                                       has latent small overwrites the canary aborts on.
const FLAGS_BASE = [_][]const u8{
    "-DHAVE_PTHREAD_RWLOCK=1", "-DHAVE_GETOPT_LONG", "-DGR_LEGACY_MODULES",
    "-fcommon", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
    "-fno-sanitize=undefined", "-fno-stack-protector", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=0",
    "-g", "-w",
};
const FLAGS_NO_HS = FLAGS_BASE ++ [_][]const u8{"-DGR_NO_HOST_STACK"};

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const host_stack = b.option(bool, "host_stack",
        "build the lwIP host stack (UDP/TCP to the router)") orelse true;
    const lua = b.option(bool, "lua",
        "build the Lua script module (links liblua5.4)") orelse false;

    const flags: []const []const u8 = if (host_stack) &FLAGS_BASE else &FLAGS_NO_HS;

    const mod = b.createModule(.{
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    mod.addIncludePath(b.path("include"));
    mod.addCSourceFiles(.{
        .root = b.path("src/grouter"),
        .files = &CORE_SRCS,
        .flags = flags,
    });
    if (host_stack) {
        mod.addCSourceFiles(.{
            .root = b.path("src/grouter"),
            .files = &LWIP_SRCS,
            .flags = flags,
        });
    }
    if (lua) {
        mod.addCSourceFiles(.{
            .root = b.path("src/grouter"),
            .files = &[_][]const u8{"gr_mod_lua.c"},
            .flags = flags,
        });
        mod.linkSystemLibrary("lua5.4", .{});
    }
    for (SYS_LIBS) |lib| mod.linkSystemLibrary(lib, .{});

    const exe = b.addExecutable(.{ .name = "grouter", .root_module = mod });

    // Z3: modules ported to the Zig language, each built as a memory-safe (ReleaseSafe)
    // object and linked in. They export the same C ABI, so the C callers are unchanged.
    for (ZIG_SRCS) |zsrc| {
        const zmod = b.createModule(.{
            .root_source_file = b.path(zsrc),
            .target = target,
            .optimize = .ReleaseSafe,
        });
        const zobj = b.addObject(.{ .name = "zmod", .root_module = zmod });
        exe.addObject(zobj);
    }

    b.installArtifact(exe);
}

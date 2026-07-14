#!/bin/sh
# Boot xv6 under QEMU-RISC-V with the GDB stub and serial console exposed over TCP.
#
#   -s               : gdb stub on tcp :1234 (shorthand for -gdb tcp::1234)
#   -serial tcp::4444,server,nowait : the xv6 console on tcp :4444 (Ctrl-P -> procdump; shell)
#   XV6_CPUS         : number of harts (xv6 SMP); XV6_QUANTUM seeds the scheduler time-slice.
#
# We do NOT pass -S, so the kernel runs immediately; the bridge attaches without freezing boot.
set -e
cd /opt/xv6-riscv

CPUS="${XV6_CPUS:-1}"
QUANTUM="${XV6_QUANTUM:-1}"

# Seed the initial quantum the kernel patch reads (the bridge can change it live over gdb).
export XV6_QUANTUM

# Start the in-container agent (gdb -> HTTP :5000) in the background; it lazily connects to the
# gdb stub, so it can start before QEMU is up. Then exec QEMU in the foreground so the container's
# lifetime tracks the kernel (QEMU exits -> container exits -> badge shows error).
python3 /opt/gini_agent.py &

# Headless: xv6 console (serial0) -> tcp:4444, gdb stub -> tcp:1234. We use `-display none
# -monitor none` rather than `-nographic` (which muxes serial+monitor onto stdio and fights the
# explicit `-serial tcp`).
exec qemu-system-riscv64 \
    -machine virt -bios none -kernel kernel/kernel \
    -m 128M -smp "${CPUS}" \
    -display none -monitor none \
    -global virtio-mmio.force-legacy=false \
    -drive file=fs.img,if=none,format=raw,id=x0 \
    -device virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0 \
    -serial tcp::4444,server,nowait \
    -gdb tcp::1234

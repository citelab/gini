# gini-xv6 — a real xv6 teaching kernel (MIT 6.1810) running under QEMU-RISC-V, exposing its
# GDB stub and serial console over TCP so GINI's Machine Lab can read and steer it.
#
# Build (on the Mac / desktop, from backend/xv6/):
#   docker build -t gini-xv6:latest .
#
# The compiled ServiceSpec publishes container port 1234 (gdb) and 4444 (serial); the Machine
# Lab's Xv6Bridge (gini.runtime.xv6_bridge.connect) attaches to both.
FROM debian:bookworm-slim

# Toolchain + QEMU (riscv64) + gdb-multiarch to build and run xv6.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential gcc-riscv64-unknown-elf \
        qemu-system-misc gdb-multiarch ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

# xv6-riscv (MIT 6.1810). Pin a commit for reproducibility in a real deployment.
RUN git clone --depth 1 https://github.com/mit-pdos/xv6-riscv.git

# The GINI kernel changes (settable time-slice quantum, scheduler-policy hook, vmprint for the
# Memory face) via an anchored patcher that always leaves a buildable kernel — see gini_patch.py.
COPY gini_patch.py /opt/gini_patch.py
# `make` alone builds only kernel/kernel (the default target); fs.img is a separate target
# (normally a dep of `make qemu`), so build both explicitly or QEMU can't open the disk.
RUN cd xv6-riscv && python3 /opt/gini_patch.py . && make kernel/kernel fs.img
# Stash the pristine shadow stub OUTSIDE kernel/shadows/ (which the Load loop bind-mounts over at
# runtime), so the agent can compute "present" (does the student's file differ from the stub?) and
# /revert can restore a known-good kernel.
RUN cp /opt/xv6-riscv/kernel/shadows/gini_sched.c /opt/gini_sched_ref.c \
 && cp /opt/xv6-riscv/kernel/shadows/gini_vm.c    /opt/gini_vm_ref.c \
 && cp /opt/xv6-riscv/kernel/shadows/gini_fs.c    /opt/gini_fs_ref.c

COPY gini_agent.py /opt/gini_agent.py
COPY boot.sh /opt/boot.sh
# strip CR (a Windows/CRLF checkout makes the `#!` line unrunnable: "no such file or directory")
RUN sed -i 's/\r$//' /opt/boot.sh && chmod +x /opt/boot.sh

# agent HTTP (state feed) + serial console; gdb stub stays internal to the container.
EXPOSE 5000 4444 1234
ENV XV6_QUANTUM=1 XV6_CPUS=1
ENTRYPOINT ["/opt/boot.sh"]

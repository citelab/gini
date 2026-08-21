#!/bin/sh
# Entry point for the gini-xv6 container.
#
# The AGENT is now the long-lived process (PID 1), and IT launches + manages QEMU as a child it can
# stop/rebuild/relaunch — that's what makes the Load loop possible (edit the shadow file -> `make`
# -> restart QEMU with the new kernel, without killing the container). Previously QEMU was exec'd
# here as PID 1, which could never be restarted.
#
# The agent reads XV6_CPUS / XV6_QUANTUM from the environment to build the QEMU command.
set -e
cd /opt/xv6-riscv
# The frontend sets XV6_CPUS from the element SIZE (S/M=1, L=2, XL=4). Default 1 for a bare
# `docker run` so the container matches what the smallest machine gets.
export XV6_CPUS="${XV6_CPUS:-1}"
export XV6_QUANTUM="${XV6_QUANTUM:-1}"
exec python3 /opt/gini_agent.py

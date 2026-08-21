# gini-oszoo — one image that boots a real historical OS under emulation and serves its screen
# as a web page (noVNC). The guest is chosen at run time by ZOO_OS; the same image serves every
# OS Zoo element, so `docker compose` builds it once.
#
# Build (on the Mac / desktop, from backend/oszoo/):
#   docker build -t gini-oszoo:latest .
#
# The compiled ServiceSpec publishes container port 6080 (the noVNC web console the Zoo Lab
# embeds) and 5900 (raw VNC). Full OSes (freely redistributable) are fetched on first boot into
# a cache; bring-your-own guests bind-mount the student's image at /zoo/byo.img.
FROM debian:bookworm-slim

# Emulators + the two display pipelines:
#  * QEMU (x86) — FreeDOS, KolibriOS, MenuetOS, and BYO x86 — uses QEMU's built-in VNC server.
#  * DOSBox (DOS / Windows 3.x) and Basilisk II (classic 68k Mac) are SDL apps with no VNC of
#    their own, so they render into a virtual X display (Xvfb) that x11vnc serves — see boot_zoo.sh.
# Basilisk II lives in Debian `contrib` (it needs a non-free Mac ROM the student supplies), so we
# enable contrib before installing. websockify + noVNC bridge the VNC framebuffer to a web port.
RUN sed -i 's/Components: main/Components: main contrib/' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's/ main$/ main contrib/'                       /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
        qemu-system-x86 qemu-utils \
        dosbox basilisk2 xvfb x11vnc fluxbox \
        novnc websockify \
        ca-certificates curl unzip gzip p7zip-full \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
COPY boot_zoo.sh /opt/boot_zoo.sh
# strip CR (a Windows/CRLF checkout makes the `#!` line unrunnable: "no such file or directory")
RUN sed -i 's/\r$//' /opt/boot_zoo.sh && chmod +x /opt/boot_zoo.sh

# Where fetched full-OS images and any persistent overlay live. Mount a volume here to cache
# downloads and (with ZOO_PERSIST=1) keep changes across runs.
VOLUME ["/zoo/cache"]

# noVNC web console (embedded by the Zoo Lab) + raw VNC. No fabric wiring in v1.
EXPOSE 6080 5900
ENV ZOO_OS=freedos ZOO_PERSIST=0 ZOO_EMULATOR=qemu ZOO_ARCH=x86
ENTRYPOINT ["/opt/boot_zoo.sh"]

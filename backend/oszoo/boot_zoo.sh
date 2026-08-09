#!/usr/bin/env bash
# boot_zoo.sh — pick a guest OS from ZOO_OS, boot it under the right emulator with a VNC
# framebuffer, and serve that framebuffer as a web page via websockify + noVNC.
#
#   ZOO_OS       freedos | plan9 | reactos | byo   (byo = the "Classic OS (your image)" element)
#   ZOO_PERSIST  0 | 1                              (1 = keep changes in a qcow2 overlay)
#   ZOO_EMULATOR qemu | dosbox-x | basilisk         (byo only; full OSes set their own)
#   ZOO_ARCH     x86 | x86_64 | 68k                 (byo only)
#   ZOO_IMAGE    /path                              (byo only; bind-mounted at /zoo/byo.img)
#
# Design notes:
#  * The student's BYO image is bind-mounted read-only by the compiler; GINI hosts nothing
#    copyrighted. Full OSes are freely redistributable and fetched on first boot into a cache.
#  * A failed/incomplete download must NEVER take the container down — websockify always starts
#    and QEMU always launches (with no disk if need be) so the Zoo Lab shows a screen and the
#    reason, instead of "connection refused". So: no `set -e`; errors are handled explicitly.
set -uo pipefail

CACHE=/zoo/cache
mkdir -p "$CACHE"

# ---- per-OS table: emulator | arch | nic | media(hd|cd) | mem(MB) | kind(zip|gz|raw) | url ----
# NOTE: these are the freely-redistributable images, pinned to a specific release. Bump the URL
# when you want a newer build. Proprietary OSes never appear here (they arrive as ZOO_OS=byo).
declare -A EMU ARCH NIC MEDIA MEM KIND URL
# FreeDOS: use the LiveCD (not the LiteUSB *installer*, which loops in Setup). The LiveCD's
# isolinux menu has `DEFAULT live` -> "Live Environment mode", which boots FreeDOS from CD/RAM to
# a working C:\> prompt with no install and no disk writes. Boot as CD; inner image is FD14LIVE.iso.
# NOTE: the LiveCD builds a writable RAM drive (C:) and loads its packages into it; too little RAM
# makes it warn "Add more RAM!" and fall back to the read-only CD (D:). 1 GB gives a full C: drive.
EMU[freedos]=qemu; ARCH[freedos]=x86; NIC[freedos]=ne2k_pci; MEDIA[freedos]=cd; MEM[freedos]=1024; KIND[freedos]=zip
URL[freedos]="https://www.ibiblio.org/pub/micro/pc-stuff/freedos/files/distributions/1.4/FD14-LiveCD.zip"

# KolibriOS: a tiny assembly GUI OS on a single 1.44 MB floppy — boots to a desktop in seconds even
# under software emulation. Distributed as a .7z holding kolibri.img; boot it as a floppy.
EMU[kolibri]=qemu; ARCH[kolibri]=x86; NIC[kolibri]=rtl8139; MEDIA[kolibri]=floppy; MEM[kolibri]=128; KIND[kolibri]=7z
URL[kolibri]="https://builds.kolibrios.org/en_US/latest-img.7z"

# MenuetOS: the assembly GUI OS KolibriOS forked from. The 32-bit build (GPL) is a raw 1.44 MB
# floppy on archive.org — no unpacking needed. Boots to a desktop in seconds, like KolibriOS.
EMU[menuet]=qemu; ARCH[menuet]=x86; NIC[menuet]=rtl8139; MEDIA[menuet]=floppy; MEM[menuet]=128; KIND[menuet]=raw
URL[menuet]="https://archive.org/download/menuetos/M32-086B.IMG"

# Both sources serve fine with curl's default User-Agent — do NOT send a browser UA (ibiblio
# doesn't need it). Pin/replace URLs above as needed.
resolve_url() {  # $1=os -> echoes the concrete download URL (a hook for sources with rotating names)
    echo "${URL[$1]}"
}

# ---- resolve the disk/CD image (sets $disk, $media, $emu, $arch, $nic, $mem; "" disk = none) --
# vCPUs per guest. Docker-for-Mac has no KVM, so QEMU runs pure software emulation (TCG); giving
# a full GUI OS 2 CPUs lets multi-threaded TCG use more host cores and boot noticeably faster.
# DOS is single-CPU (extra CPUs are ignored), so keep it at 1.
declare -A CPUS=([freedos]=1 [kolibri]=1 [menuet]=1)   # all fast, single-CPU guests

os="${ZOO_OS:-freedos}"
disk=""; media=cd; emu=qemu; arch=x86; nic=e1000; mem=256; cpus=1

fetch_and_unpack() {  # $1=os -> echoes the image path, or nothing on failure
    local o="$1" kind="${KIND[$1]}" want="${MEDIA[$1]}" url
    local ext="iso"; { [ "$want" = "hd" ] || [ "$want" = "floppy" ]; } && ext="img"
    local out="$CACHE/$o.$ext"
    [ -f "$out" ] && { echo "$out"; return 0; }         # cached from a previous boot
    url="$(resolve_url "$o")"
    [ -n "$url" ] || { echo "OS Zoo: could not resolve a download URL for $o." >&2; return 1; }
    local dl="$CACHE/$o.download"
    echo "OS Zoo: fetching $o from $url (first boot; caching at $out) ..." >&2
    if ! curl -fSL --retry 3 "$url" -o "$dl"; then
        echo "OS Zoo: download failed for $o ($url)." >&2; rm -f "$dl"; return 1; fi
    local d="$CACHE/$o.d"
    case "$kind" in
        gz)  gunzip -c "$dl" > "$out" || { rm -f "$out"; return 1; } ;;
        zip) rm -rf "$d"; mkdir -p "$d"
             unzip -o -q "$dl" -d "$d" || return 1
             local inner; inner="$(find "$d" -iname "*.$ext" | head -1)"
             [ -n "$inner" ] || { echo "OS Zoo: no .$ext inside $o archive." >&2; return 1; }
             mv "$inner" "$out" ;;
        7z)  rm -rf "$d"; mkdir -p "$d"
             7z x -y -o"$d" "$dl" >/dev/null || { echo "OS Zoo: 7z extract failed for $o." >&2; return 1; }
             local inner7; inner7="$(find "$d" -iname "*.$ext" | head -1)"
             [ -n "$inner7" ] || { echo "OS Zoo: no .$ext inside $o archive." >&2; return 1; }
             mv "$inner7" "$out" ;;
        raw) mv "$dl" "$out" ;;
    esac
    rm -f "$dl"; echo "$out"
}

# Per-URL cache key so different guests never collide in the shared /zoo/cache (e.g. the Mac disk
# and the MS-DOS disk must not both be "byo-disk"). Falls back to a plain tag if md5sum is absent.
zkey() { printf '%s' "$1" | md5sum 2>/dev/null | cut -c1-12 || echo x; }

fetch_byo() {   # $1=url $2=role(disk|rom) -> echoes a cached local path (downloads once). BYO
                # Image/Rom can be an http(s) URL instead of a local file; we fetch it here.
    local url="$1" role="$2"        # NOTE: separate line — a same-line `out=…$role` would expand
                                    # $role before it's assigned (bash), tripping `set -u`.
    local k; k="$(zkey "$url")"
    # A zipped DISK becomes a DIRECTORY (DOSBox mounts a folder as C:); everything else -> one file.
    if [ "$role" = "disk" ] && { [ "${url##*.}" = "zip" ] || [ "${url##*.}" = "ZIP" ]; }; then
        local dir="$CACHE/byo-disk-$k.d"
        if [ ! -d "$dir" ]; then
            echo "OS Zoo (BYO): downloading disk (zip) from $url ..." >&2
            local ztmp="$CACHE/byo-disk-$k.zip"
            curl -fSL --retry 3 "$url" -o "$ztmp" \
                || { echo "OS Zoo (BYO): download failed ($url)." >&2; rm -f "$ztmp"; return 1; }
            mkdir -p "$dir"
            unzip -oq "$ztmp" -d "$dir" || { rm -rf "$dir"; rm -f "$ztmp"; return 1; }
            rm -f "$ztmp"
        fi
        echo "$dir"; return 0
    fi
    local out="$CACHE/byo-$role-$k"
    if [ ! -f "$out" ]; then
        echo "OS Zoo (BYO): downloading $role from $url ..." >&2
        local tmp="$out.dl"
        if ! curl -fSL --retry 3 "$url" -o "$tmp"; then
            echo "OS Zoo (BYO): download failed for $role ($url)." >&2; rm -f "$tmp"; return 1; fi
        case "${url##*.}" in
            zip|ZIP) unzip -p "$tmp" > "$out" 2>/dev/null && rm -f "$tmp" || { rm -f "$tmp"; return 1; } ;;
            gz|GZ)   gunzip -c "$tmp" > "$out" 2>/dev/null && rm -f "$tmp" || { rm -f "$tmp"; return 1; } ;;
            *)       mv "$tmp" "$out" ;;
        esac
    fi
    echo "$out"
}

if [ "$os" = "byo" ]; then
    emu="${ZOO_EMULATOR:-qemu}"; arch="${ZOO_ARCH:-x86}"; nic=e1000; media=hd; mem=512; cpus=1
    # ROM (Basilisk II): an http(s) URL is downloaded; a local path is bind-mounted at /zoo/rom.
    case "${ZOO_ROM:-}" in
        http://*|https://*) rom="$(fetch_byo "$ZOO_ROM" rom)" || rom="" ;;
        *)                  rom=/zoo/rom ;;
    esac
    # Disk: an http(s) URL is downloaded (writable in the cache, changes persist); a local FILE is
    # bind-mounted read-only, so we boot a writable working COPY; a local FOLDER (DOSBox C:) is used
    # in place — so the student's original is never modified either way.
    case "${ZOO_IMAGE:-}" in
        http://*|https://*)
            disk="$(fetch_byo "$ZOO_IMAGE" disk)" || disk="" ;;
        *)
            if [ -e /zoo/byo.img ]; then
                if [ -f /zoo/byo.img ]; then
                    work="$CACHE/byo-work-$(zkey "${ZOO_IMAGE:-local}").img"
                    [ -f "$work" ] || cp /zoo/byo.img "$work"; disk="$work"
                else
                    disk=/zoo/byo.img
                fi
            else
                echo "OS Zoo (BYO): no image — set the Image property to an http(s):// URL or a local path/folder." >&2
            fi ;;
    esac
elif [ -n "${URL[$os]:-}" ]; then
    emu="${EMU[$os]}"; arch="${ARCH[$os]}"; nic="${NIC[$os]}"; media="${MEDIA[$os]}"; mem="${MEM[$os]}"
    cpus="${CPUS[$os]:-1}"
    disk="$(fetch_and_unpack "$os")" || disk=""
else
    echo "OS Zoo: unknown ZOO_OS='$os' — booting with no disk." >&2
fi

# persistence: default ephemeral; ZOO_PERSIST=1 keeps changes in a qcow2 overlay (hd guests)
fmt=raw
if [ -n "$disk" ] && [ "${ZOO_PERSIST:-0}" = "1" ] && [ "$media" = "hd" ]; then
    overlay="$CACHE/$os.overlay.qcow2"
    [ -f "$overlay" ] || qemu-img create -f qcow2 -b "$disk" -F raw "$overlay" >/dev/null 2>&1
    [ -f "$overlay" ] && { disk="$overlay"; fmt=qcow2; }
fi

# Old x86 OSes (MS-DOS, Win9x) address the disk by CHS via INT13; if QEMU's auto-geometry differs
# from the geometry baked into the image, the OS boots the MBR but can't read its FAT. Read the
# real geometry from the partition table's end-CHS so we can hand it to QEMU.
qemu_geometry() {   # $1=raw disk image -> echoes "cyls=C,heads=H,secs=S" (or nothing)
    python3 - "$1" <<'PY' 2>/dev/null
import sys, os
p = sys.argv[1]
try:
    with open(p, "rb") as f: b = f.read(512)
except Exception: sys.exit(0)
if len(b) < 512 or b[510:512] != b"\x55\xaa": sys.exit(0)
for i in range(4):
    e = b[446 + i*16: 446 + i*16 + 16]
    if e[4] == 0: continue                      # empty partition slot
    heads = e[5] + 1                            # end-CHS head  -> head count
    secs  = e[6] & 0x3f                          # end-CHS sector-> sectors/track
    if heads < 1 or secs < 1: sys.exit(0)
    total = os.path.getsize(p) // 512
    cyls = total // (heads * secs)
    if cyls >= 1:
        print(f"cyls={cyls},heads={heads},secs={secs}")
    break
PY
}

# ---- launch the emulator with a VNC framebuffer on :0 (=5900) ---------------------------------
start_qemu() {
    local bin=qemu-system-i386; [ "$arch" = "x86_64" ] && bin=qemu-system-x86_64
    if ! command -v "$bin" >/dev/null; then
        echo "OS Zoo: '$bin' is not installed in this image — check the Dockerfile's qemu packages." >&2
        sleep infinity &
        return
    fi
    # v1 is display-only: NO networking. `-nic none` disables it cleanly (the old
    # `-net nic … -net none` combo could make QEMU refuse to start). v2 wires the fabric.
    local -a args=(-m "$mem" -vga std -rtc base=localtime -k en-us -vnc :0 -nic none)
    [ "${cpus:-1}" -gt 1 ] 2>/dev/null && args+=(-smp "$cpus")   # MTTCG speeds full-GUI boots
    if   [ -z "$disk" ];          then :                                           # no media -> "no bootable device"
    elif [ "$media" = "cd" ];     then args+=(-cdrom "$disk" -boot d)
    elif [ "$media" = "floppy" ]; then args+=(-fda "$disk" -boot a)
    else
        # hard disk: force the image's real CHS geometry so DOS/9x can read the FAT via INT13.
        local geo; geo="$(qemu_geometry "$disk")"
        if [ -n "$geo" ]; then
            args+=(-drive "if=none,id=zoodisk,file=$disk,format=$fmt"
                   -device "ide-hd,drive=zoodisk,$geo,bootindex=0")
        else
            args+=(-drive "file=$disk,format=$fmt" -boot c)
        fi
    fi
    echo "OS Zoo: launching: $bin ${args[*]}" >&2
    "$bin" "${args[@]}" &          # stderr inherits the container's -> visible in `docker logs`
}

# ---- SDL emulators (DOSBox, Basilisk II): no built-in VNC, so render into a virtual X display
# (Xvfb) that x11vnc serves on :5900 — the same port websockify proxies. One guest per container.
start_sdl() {                                        # $1 = dosbox | basilisk
    # 24-bit depth: SDL video (Basilisk II) throws X BadMatch on a 16-bit Xvfb. Force the X11
    # driver and no audio device (there's no sound card in the container).
    export HOME=/root DISPLAY=:0 SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy
    Xvfb :0 -screen 0 1024x768x24 -nolisten tcp -ac >/dev/null 2>&1 &
    sleep 2
    fluxbox >/dev/null 2>&1 &                         # minimal WM (maps + focuses the app window)
    sleep 1                                           # let the WM settle before the app maps a window
    x11vnc -display :0 -forever -shared -nopw -rfbport 5900 -bg -quiet -noxdamage >/dev/null 2>&1
    case "$1" in
        dosbox)
            # DOSBox tuned for Windows 3.x: an S3 SVGA card + 16 MB RAM (what Win 3.1 wants),
            # fullscreen into the Xvfb framebuffer, software output (safe headless).
            cat > /root/dosbox.conf <<'CONF'
[sdl]
fullscreen=true
fullresolution=desktop
output=surface
[dosbox]
machine=svga_s3
memsize=16
[cpu]
cycles=max
[autoexec]
CONF
            # mount the student's C: — a folder is mounted directly; a disk image is IMGMOUNTed
            if [ -d "$disk" ]; then echo "MOUNT C \"$disk\"" >> /root/dosbox.conf
            else                    echo "IMGMOUNT C \"$disk\" -t hdd -fs fat" >> /root/dosbox.conf; fi
            cat >> /root/dosbox.conf <<'CONF'
C:
PATH C:\;C:\DOS;C:\WINDOWS
@ECHO At the C:\ prompt, type WIN to start Windows 3.x (if installed).
WIN
CONF
            echo "OS Zoo: launching: dosbox (DOS/Windows 3.x) with $disk" >&2
            dosbox -conf /root/dosbox.conf &
            ;;
        basilisk)
            [ -e "$rom" ] || echo "OS Zoo (Mac): no ROM at $rom — set the element's Rom property to a Macintosh ROM you own." >&2
            cat > /root/.basilisk_ii_prefs <<PREFS
rom $rom
disk $disk
screen win/1024/768
nogui true
nosound true
modelid 14
cpu 4
fpu true
frameskip 0
PREFS
            echo "OS Zoo: launching: BasiliskII (classic 68k Mac) rom=$rom disk=$disk" >&2
            BasiliskII &
            ;;
    esac
}

case "$emu" in
    qemu)     start_qemu ;;
    dosbox)   start_sdl dosbox ;;      # DOS / Windows 3.x (BYO)
    basilisk) start_sdl basilisk ;;    # classic 68k Mac — System 7 / Mac OS 8 (BYO ROM + disk)
    *)        echo "OS Zoo: unsupported emulator '$emu' — idling." >&2; sleep infinity & ;;
esac
EMU_PID=$!

sleep 1
echo "OS Zoo: $os — noVNC on :6080, VNC on :5900${disk:+ (disk=$disk)}" >&2
websockify --web=/usr/share/novnc 6080 localhost:5900 &
WS_PID=$!

trap 'kill $EMU_PID $WS_PID 2>/dev/null || true' EXIT
wait -n "$EMU_PID" "$WS_PID"

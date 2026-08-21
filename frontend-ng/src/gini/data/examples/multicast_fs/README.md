# Multicast File Distribution — starter kit

Starter code for the capstone in the GINI book's **Network Multicasting** chapter.
GINI copies this folder into `~/.gini/shared/multicast_fs/`, which every station
mounts at `/shared/multicast_fs`.

| file          | role                                                              |
|---------------|-------------------------------------------------------------------|
| `multicast.h` | the socket-wrapper API, the checksum helper, and `mc_hdr_t`       |
| `multicast.c` | complete — nothing to change                                      |
| `sender.c`    | carousel skeleton: **the TODOs are the assignment**               |
| `receiver.c`  | join/reassemble/verify skeleton: **the TODOs are the assignment** |

## Build (on a station, Toolkit = full)

    apt install -y gcc
    gcc -O2 -o sender   /shared/multicast_fs/sender.c   /shared/multicast_fs/multicast.c
    gcc -O2 -o receiver /shared/multicast_fs/receiver.c /shared/multicast_fs/multicast.c

## Network side

Load the provided tree forwarder on **every** router, and force IGMPv2 on every
receiver station:

    router>  gpipe cp add lua /scripts/mcast_tree.lua 1000
    recv:~#  sysctl -w net.ipv4.conf.all.force_igmp_version=2

Watch the joins, the tree, and the per-interface copy counters in gBuilder's
**Multicast HUD** while a session runs.

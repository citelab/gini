#!/bin/sh
# Launch POX as an OpenFlow 1.0 controller. of_01 listens on 0.0.0.0:$POX_PORT so the
# gRouter-OVS containers can connect; the app (default forwarding.l2_learning) installs
# flows on packet-in.
set -e
cd /opt/pox
exec python3 pox.py \
    openflow.of_01 --port="${POX_PORT:-6633}" \
    "${POX_APP:-forwarding.l2_learning}"

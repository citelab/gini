#!/bin/sh
# Launch POX as an OpenFlow 1.0 controller. of_01 listens on 0.0.0.0:$POX_PORT so the
# gRouter-OVS containers can connect; the app (default gini.samples.switch) clears the
# Flow Switch's match-all -> NORMAL default and installs flows on packet-in.
#
# POX_APP may name SEVERAL modules separated by spaces, which is how POX composes
# behaviour -- e.g. a network-wide fabric is
#     POX_APP="openflow.discovery openflow.spanning_tree forwarding.l2_multi"
# Each module is a separate argv word, and a module may carry its own --flags, so
# POX_APP is deliberately left UNQUOTED below to let the shell word-split it.
# Quoting it would hand POX one module named "openflow.discovery forwarding.l2_multi",
# which fails to import.
set -e
cd /opt/pox
# shellcheck disable=SC2086  # intentional word splitting: POX_APP may list several modules
exec python3 pox.py \
    openflow.of_01 --port="${POX_PORT:-6633}" \
    ${POX_APP:-gini.samples.switch}

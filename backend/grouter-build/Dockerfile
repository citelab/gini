# Build the REAL GINI gRouter (C) and run it in the fabric.
# Build context MUST be the backend/ directory (so include/, src/grouter/, grouter-build/
# sit at the context root, where the COPYs below expect them).
#
#   cd backend && docker build -f grouter-build/Dockerfile -t gini-grouter .
#
# (The grouter-build/ directory keeps its name for now — its path is referenced from the frontend's
#  image layout; a rename is a separate, coordinated change. Zig itself is gone: the router is C.)
#
# TWO STAGES, deliberately. The router is a compiled C binary, so the compiler is a BUILD
# dependency, not a runtime one. The runtime stage copies out the one artifact that matters (the
# grouter binary) plus what the entrypoint needs, and nothing else — no compiler shipped to every
# student's Router/OVS container.
# --------------------------------------------------------------------------------------------- #
FROM debian:bookworm-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget ca-certificates make gcc libc6-dev \
        libreadline-dev libncurses-dev liblua5.4-dev \
    && rm -rf /var/lib/apt/lists/*

# libslack from source (the one external dep that isn't packaged). Build it with
# the same legacy-hardening-off flags as the router (CCFLAGS is libslack's knob):
# otherwise a stack canary inside libslack aborts the (protector-free) router at
# startup with "stack smashing detected".
# We skip `make install` (its man-page target needs perl's pod2man, absent in slim)
# and copy the library + headers directly — all the router's build needs.
RUN wget -q http://libslack.org/download/libslack-0.6.tar.gz \
    && tar xzf libslack-0.6.tar.gz \
    && cd libslack-0.6 && ./config \
    && make CCFLAGS="-O2 -fcommon -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -w" \
    && mkdir -p /usr/local/include/slack /usr/local/lib \
    && cp ./*.h /usr/local/include/slack/ \
    && cp ./libslack.a /usr/local/lib/ \
    && ldconfig \
    && cd / && rm -rf libslack-0.6*

WORKDIR /build
COPY include/      ./include/
COPY src/grouter/  ./src/grouter/
COPY grouter-build/  ./grouter-build/

# build the real gRouter with the C compiler (gcc). invoke via bash so it doesn't depend on the
# copied file keeping its executable bit.
#
# Strip CR first: a Windows clone (git core.autocrlf=true) checks the script out with CRLF, and
# bash inside the container then dies with `set: pipefail\r: invalid option name`. .gitattributes
# prevents this for fresh clones; this keeps EXISTING Windows checkouts building.
RUN sed -i 's/\r$//' ./grouter-build/build.sh \
 && CC=gcc OUT=/usr/local/bin/grouter bash ./grouter-build/build.sh

# --------------------------------------------------------------------------------------------- #
# RUNTIME: the binary + the shared libraries it links against + python3 for the entrypoint +
# iproute2 for the tun setup. NOT the compiler, not the source tree.
# ttyd — the terminal server the gBuilder Terminal tab connects to. Fetched in a throwaway stage
# so this image needs no curl or ca-certificates of its own; it is one static musl binary (~1 MB)
# with no runtime dependencies, which is why the same binary works on Alpine and Debian bases.
# `ttyd.$(uname -m)` matches the project's release asset names (ttyd.x86_64 / ttyd.aarch64).
FROM alpine:3.20 AS ttyd
RUN apk add --no-cache curl \
 && curl -fsSL -o /ttyd "https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.$(uname -m)" \
 && chmod +x /ttyd

FROM debian:bookworm-slim

# runtime shared libs only (libreadline8 / libncurses6 / liblua5.4-0 — the -dev packages above
# were build-time). If the router ever fails here with a missing .so, add the runtime lib, never
# the -dev one.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 iproute2 libreadline8 libncurses6 liblua5.4-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/local/bin/grouter /usr/local/bin/grouter
# the entrypoint + console scripts the RUNNING router needs. grconsole.py in particular is exec'd
# from outside (the Router Lab's flow-table view, the mission `flow()` probe, probe_doctor), so it
# has to exist in the runtime image — not just in the builder.
COPY --from=build /build/grouter-build/ /build/grouter-build/

COPY --from=ttyd /ttyd /usr/local/bin/ttyd
# What the terminal fronts is per-ELEMENT: this one image serves both gRouters and OVS switches,
# and a student wants the router CLI on one and a shell on the other. TTYD_CMD carries that from
# compose; it defaults to a shell so the terminal always works even if compose says nothing.
RUN printf '#!/bin/sh\nexec /usr/local/bin/ttyd -W -p 7681 -i 0.0.0.0 /bin/sh -c "${TTYD_CMD:-exec /bin/sh}"\n' \
    > /usr/local/bin/gini-term && chmod +x /usr/local/bin/gini-term

WORKDIR /run
ENV GINI_HOME=/run
# entrypoint turns a ROUTER_CONFIG env (our fabric wiring) into the gRouter's
# native config (ifconfig add tun…, route add…) and launches it. The terminal server runs beside
# it as a background child; `exec` keeps the router as PID 1 so stop/restart are unchanged.
CMD ["sh", "-c", "gini-term & exec python3 /build/grouter-build/run_grouter.py"]

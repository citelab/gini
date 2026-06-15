# Z0 — build the REAL GINI gRouter with Zig and run it in the fabric.
# Build context MUST be the backend/ directory (so include/, src/grouter/, grouter-zig/
# sit at the context root, where the COPYs below expect them).
#
#   cd backend && docker build -f grouter-zig/Dockerfile -t gini-grouter .
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget ca-certificates make gcc libc6-dev \
        libreadline-dev libncurses-dev \
        python3 python3-pip iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Zig toolchain (bundled clang) via the pip 'ziglang' package
RUN pip3 install --break-system-packages ziglang

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
COPY grouter-zig/  ./grouter-zig/

# build the real gRouter with zig cc (invoke via bash so it doesn't depend on the
# copied file keeping its executable bit)
RUN ZIG="python3 -m ziglang" OUT=/usr/local/bin/grouter bash ./grouter-zig/build.sh

WORKDIR /run
ENV GINI_HOME=/run
# entrypoint turns a ROUTER_CONFIG env (our fabric wiring) into the gRouter's
# native config (ifconfig add tun…, route add…) and launches it.
CMD ["python3", "/build/grouter-zig/run_grouter.py"]

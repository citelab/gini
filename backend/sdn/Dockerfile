# GINI SDN controller — POX (gar, Python 3) speaking OpenFlow 1.0.
# Build once:  cd backend/sdn && docker build -t gini-pox .
# POX (gar) officially supports Python 3.6–3.9; pin to 3.9 so its version check is happy
# and we avoid subtle stdlib-removal breakage on newer interpreters.
# ttyd — the terminal server the gBuilder Terminal tab connects to. One static musl binary,
# fetched in a throwaway stage so this image needs no curl of its own.
FROM alpine:3.20 AS ttyd
RUN apk add --no-cache curl \
 && curl -fsSL -o /ttyd "https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.$(uname -m)" \
 && chmod +x /ttyd

FROM python:3.9-slim

WORKDIR /opt
COPY pox/ /opt/pox/

# Fail the BUILD if the vendored POX tree arrived incomplete, instead of shipping an image that
# crashes at boot with "No module named 'pox.lib'" — which is what a Windows student hit while
# macOS worked, and which says nothing about the actual cause.
#
# A partial tree can happen several ways and they are hard to tell apart at runtime: a checkout
# that dropped files, a .dockerignore change, or an image pulled from a registry rather than
# built here. Checking at build time turns all of them into one clear message, at the moment the
# context is still in front of you.
RUN set -eux; \
    missing=""; \
    for m in pox/__init__.py pox/core.py pox/boot.py pox/lib/__init__.py \
             pox/lib/revent/__init__.py pox/lib/packet/__init__.py \
             pox/openflow/__init__.py ext/gini; do \
        [ -e "/opt/pox/$m" ] || missing="$missing $m"; \
    done; \
    if [ -n "$missing" ]; then \
        echo "=====================================================================" >&2; \
        echo "The vendored POX tree in backend/sdn/pox is INCOMPLETE." >&2; \
        echo "Missing:$missing" >&2; \
        echo >&2; \
        echo "POX ships its own .gitignore with 'ext/*', so GINI's controller apps" >&2; \
        echo "under pox/ext/gini are ignored by git unless they were added with -f." >&2; \
        echo "Check on the machine that has a WORKING build:" >&2; \
        echo "    git -C backend/sdn ls-files pox/ext/gini | head" >&2; \
        echo "    git -C backend/sdn ls-files pox/pox/lib  | head" >&2; \
        echo "Anything listed there is committed; anything only on disk is not." >&2; \
        echo "=====================================================================" >&2; \
        exit 1; \
    fi

COPY run-pox.sh /usr/local/bin/run-pox.sh
# strip CR (a Windows/CRLF checkout makes the `#!` line unrunnable: "no such file or directory")
RUN sed -i 's/\r$//' /usr/local/bin/run-pox.sh && chmod +x /usr/local/bin/run-pox.sh

# Which controller app to run, and the OpenFlow port to listen on. Overridden per
# container by the orchestrator (POX_APP / POX_PORT from the compiled topology).
ENV POX_APP=gini.samples.switch \
    POX_PORT=6633

COPY --from=ttyd /ttyd /usr/local/bin/ttyd
RUN printf '#!/bin/sh\nexec /usr/local/bin/ttyd -W -p 7681 -i 0.0.0.0 /bin/sh -c "${TTYD_CMD:-exec /bin/sh}"\n' \
    > /usr/local/bin/gini-term && chmod +x /usr/local/bin/gini-term

EXPOSE 6633 7681
# POX keeps PID 1 via exec; the terminal server is a background child that dies with it.
CMD ["sh", "-c", "gini-term & exec /usr/local/bin/run-pox.sh"]

# GINI SDN controller — POX (gar, Python 3) speaking OpenFlow 1.0.
# Build once:  cd backend/sdn && docker build -t gini-pox .
# POX (gar) officially supports Python 3.6–3.9; pin to 3.9 so its version check is happy
# and we avoid subtle stdlib-removal breakage on newer interpreters.
FROM python:3.9-slim

WORKDIR /opt
COPY pox/ /opt/pox/
COPY run-pox.sh /usr/local/bin/run-pox.sh
RUN chmod +x /usr/local/bin/run-pox.sh

# Which controller app to run, and the OpenFlow port to listen on. Overridden per
# container by the orchestrator (POX_APP / POX_PORT from the compiled topology).
ENV POX_APP=gini.samples.switch \
    POX_PORT=6633

EXPOSE 6633
CMD ["/usr/local/bin/run-pox.sh"]

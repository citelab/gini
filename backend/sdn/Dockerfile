# GINI SDN controller — POX (gar, Python 3) speaking OpenFlow 1.0.
# Build once:  cd backend/sdn && docker build -t gini-pox .
FROM python:3.11-slim

WORKDIR /opt
COPY pox/ /opt/pox/
COPY run-pox.sh /usr/local/bin/run-pox.sh
RUN chmod +x /usr/local/bin/run-pox.sh

# Which controller app to run, and the OpenFlow port to listen on. Overridden per
# container by the orchestrator (POX_APP / POX_PORT from the compiled topology).
ENV POX_APP=forwarding.l2_learning \
    POX_PORT=6633

EXPOSE 6633
CMD ["/usr/local/bin/run-pox.sh"]

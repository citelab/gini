#!/bin/bash

declare -a images=("citelab/alpine:latest"
                   "citelab/consul-server:latest"
                   "citelab/flask-server:latest"
                   "citelab/glinux:latest"
                   "citelab/buster:latest"
                   "citelab/jessie:latest"
                   "citelab/debian:latest"
                   "citelab/haproxy:latest"
                   "citelab/registrator:latest"
                   "citelab/sfc-network-monitoring:latest"
                   "citelab/sfc-rate-limiter:latest"
                   "citelab/simplecloud:latest")

for image in "${images[@]}"
do
    docker pull $image
done


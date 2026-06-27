#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

import docker
import os
import sys
import logging


daemon_url = os.getenv('DOCKER_SOCKET', "unix:///var/run/docker.sock")
docker_client = docker.DockerClient(base_url=daemon_url)
docker_api_client = docker.APIClient(base_url=daemon_url)

logger = logging.getLogger('simplecloud')
logger.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))

logger.addHandler(handler)


#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

import consul
from simplecloud import logger


class Registrator:
    def __init__(self, consul_container):
        consul_container.reload()
        consul_ip = consul_container.attrs['NetworkSettings']['Networks']['bridge']['IPAddress']

        logger.debug(f'Connecting to Consul at address {consul_ip}')
        self.consul = consul.Consul(host=consul_ip)

    def register(self, container, ip):
        container.reload()

        config = container.attrs['Config']
        env_vars = config['Env']
        ports = list(config['ExposedPorts'].keys())
        port = int(ports[0].split('/')[0])

        service = dict()
        for var in env_vars:
            key, val = var.split('=')
            if key.startswith('SERVICE'):
                _, attr = key.lower().split('_')
                service[attr] = val

        if 'name' in service:
            logger.debug(f'Registering container {container.id} with {ip}')
            self.consul.agent.service.register(
                service['name'],
                service_id=container.id,
                port=port,
                address=ip
            )

    def deregister(self, cid):
        logger.debug(f'De-registering container {cid}')
        self.consul.agent.service.deregister(cid)


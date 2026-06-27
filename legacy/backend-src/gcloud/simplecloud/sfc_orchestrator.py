#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

from simplecloud import docker_client, docker_api_client, logger
from simplecloud.utils.northbound import *
import socket

NORTHBOUND_PORT = 8081
BUFFER_SIZE = 4096


class SfcException(Exception):
    pass


class NetworkFunction:
    """
    This class represents a virtual network function in an SFC-enabled network
    topology. It is used as an API to configure the actual Docker container
    object for each network service node.
    """
    FUNCTION_TYPE = ''
    DOCKER_IMAGE = ''

    def __init__(self, name, shell_name):
        self.name = name
        self.container = None
        self.shell_name = shell_name
        self.active_chains = 0

    @property
    def id(self):
        return self.container.id

    def start(self):
        """
        Start the Docker container for this VNF
        """
        host_config = docker_api_client.create_host_config(
            auto_remove=True,
            cap_add=["NET_ADMIN"]
        )

        pending_container = docker_api_client.create_container(
            image=self.DOCKER_IMAGE,
            name=self.name,
            stdin_open=True,
            tty=True,
            detach=True,
            host_config=host_config,
        )

        self.container = docker_client.containers.get(pending_container)
        self.container.start()

    def stop(self):
        try:
            self.container.remove(force=True)
        except:
            pass

    def __str__(self):
        return self.shell_name

    def __repr__(self):
        return f"<{self.FUNCTION_TYPE}: {self.shell_name}>"


class Firewall(NetworkFunction):
    FUNCTION_TYPE = 'Firewall'
    DOCKER_IMAGE = 'citelab/sfc-firewall:latest'

    def __init__(self, *args):
        super().__init__(*args)


class RateLimiter(NetworkFunction):
    FUNCTION_TYPE = 'Rate Limiter'
    DOCKER_IMAGE = 'citelab/sfc-rate-limiter:latest'

    def __init__(self, *args):
        super().__init__(*args)


class NetworkMonitoring(NetworkFunction):
    FUNCTION_TYPE = 'Network Monitor'
    DOCKER_IMAGE = 'citelab/sfc-network-monitoring:latest'

    def __init__(self, *args):
        super().__init__(*args)


# A mapping of network function name -> Python object
vnf = {
    'firewall': Firewall,
    'rate-limiter': RateLimiter,
    'network-monitoring': NetworkMonitoring
}


class SfcOrchestrator:
    """
    This class implements a simple SFC orchestrator, which is responsible for
    monitoring of virtual network functions and service chains in a software-
    defined network.

    Some SDN controllers communicate via port 8080/8081 for northbound stuff.
    Here we're using port 8081 but communication will simply be done via TCP
    instead of HTTP.

    Attributes:
        network:        Network driver, which is also used by the cloud manager
        services:       Mapping of service function name -> Docker container
        chains:         Mapping of (DPID, idx) -> list of services
        paths:          Mapping of (src,dst) -> (DPID,idx)
        client:         TCP client, used to communicate with SDN controller
    """
    def __init__(self, network):
        # Docker network driver, must be OVS
        if not network.ovs:
            raise SfcException('Network must be using OVS driver.')

        # store the current network driver
        self.network = network

        self.controller_ip = self.network.reserve_ip('POX')

        self.services = {}
        self.service_index = 1
        self.chains = {}
        self.chain_index = 1
        self.paths = {}

        # variables for TCP client
        self.client = None

        # northbound message generator
        self.msg_gen = ControllerMessageGenerator(
            self.network.dpid,
            self.controller_ip
        )

    def open_connection(self):
        """
        Open a TCP connection and return a TCP client connection
        """
        if self.client:
            return
        server_name = '127.0.0.1'
        server_port = NORTHBOUND_PORT
        try:
            self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client.connect((server_name, server_port))
            msg = self.msg_gen.new_hello_message()
            self.client.send(msg.pack().encode())
            return True
        except:
            logger.error('Failed to connect to the SDN controller')
            self.client = None
            return False

    def _start_vnf(self, function, service_name):
        vnf_class = vnf.get(function)
        container_name = f'{function}_{self.service_index:05x}_{self.network.name}'
        self.service_index += 1

        network_function = vnf_class(container_name, service_name)
        network_function.start()
        self.network.add_container(network_function.container)

        return network_function

    def start_service_node(self, function, service_name):
        """
        Start an instance of a service function (in a Docker container)
        """
        if function not in vnf:
            logger.warning('Virtual network function not supported')
            return False
        else:
            if service_name not in self.services:
                network_function = self._start_vnf(function, service_name)
                self.services[service_name] = network_function
            else:
                logger.warning(f"Namespace {service_name} has already been used. "
                               f"Ignoring this operation")
            return True

    def remove_service_node(self, service_name, force=False):
        """
        Remove an instance that has service_name
        """
        network_function = self.services.get(service_name, None)
        if network_function:
            if network_function.active_chains == 0:
                self.network.remove_container(network_function.id)
                network_function.stop()
                del self.services[service_name]
                del network_function
            elif force:
                pending_chains = []
                for chain_id, chain in self.chains.items():
                    if network_function in chain:
                        pending_chains.append(chain_id)
                for chain_id in pending_chains:
                    self.remove_service_chain(chain_id, force=True)
                self.network.remove_container(network_function.id)
                network_function.stop()
                del self.services[service_name]
                del network_function
            else:
                logger.warning("Service node is currently used in an active SFC. "
                               "Please use option --force=true to remove it completely")

        return True

    def show_services(self):
        """
        List active network function services
        """
        res = {}
        for k, v in self.services.items():
            res[k] = {
                "Type": v.FUNCTION_TYPE,
                "Interface": str(self.network.resolve_interface(v.id))
            }
        return res

    def create_service_chain(self, services):
        """
        Create a service chain from existing service nodes in the network
        """
        if not services:
            logger.warning('List of services cannot be empty')
            return
        if self.client is None:
            logger.warning('There is no connection to the SDN controller')
            return

        chain_id = f"{self.network.dpid:08x}-{self.chain_index}"
        chain = []
        for service_name in services:
            node = self.services.get(service_name, None)
            if not node:
                logger.warning('Service not found. Chain creation is cancelled')
                return
            else:
                chain.append(node)
        self.chains[chain_id] = chain
        for node in chain:
            node.active_chains += 1
        self.chain_index += 1

        chain_str = []
        for node in chain:
            interface = self.network.resolve_interface(node.id)
            chain_str.append(interface.to_dict())

        msg = self.msg_gen.new_addchain_message(chain_id, chain_str)
        self.client.send(msg.pack().encode())

        return chain_id

    def remove_service_chain(self, chain_id, force=False):
        """
        Remove a service chain identified by (dpid,idx)
        """
        if self.client is None:
            logger.warning('There is no connection to the SDN controller')
            return

        chain = self.chains.get(chain_id, None)
        if chain:
            pending_paths = []
            for endpoints, cid in self.paths.items():
                if chain_id == cid:
                    pending_paths.append(endpoints)
            if len(pending_paths) == 0 or force:
                for ep in pending_paths:
                    u,v = ep
                    self.remove_service_path(u,v)
                for node in chain:
                    node.active_chains -= 1
                del chain
                del self.chains[chain_id]
            else:
                logger.warning("Service chain is currently used in an active "
                               "service path. Please use option --force=true "
                               "to remove it completely")
                return True

            msg = self.msg_gen.new_delchain_message(chain_id)
            self.client.send(msg.pack().encode())
        return True

    def show_service_chains(self):
        """
        List active service chains in the network
        """
        res = {}
        for chain_id, chain in self.chains.items():
            res[chain_id] = str([node.shell_name for node in chain])
        return res

    def create_service_path(self, src, dst, chain_id):
        """
        Create a service path from src to dst via the service chain (dpid,idx)
        """
        if self.client is None:
            logger.warning('There is no connection to the SDN controller')
            return False

        path_id = (src,dst)
        if path_id in self.paths:
            logger.warning('Path already exists between src and dst nodes')
            return False
        else:
            self.paths[path_id] = chain_id

            src_interface = self.network.resolve_interface(src.id)
            dst_interface = self.network.resolve_interface(dst.id)
            msg = self.msg_gen.new_addpath_message(
                src_interface.to_dict(),
                dst_interface.to_dict(),
                chain_id
            )
            self.client.send(msg.pack().encode())

            return True

    def remove_service_path(self, src, dst):
        """
        Remove a service path identified by (src,dst)
        """
        if self.client is None:
            logger.warning('There is no connection to the SDN controller')
            return False

        path_id = (src, dst)
        if path_id in self.paths:
            del self.paths[path_id]

            src_interface = self.network.resolve_interface(src.id)
            dst_interface = self.network.resolve_interface(dst.id)
            msg = self.msg_gen.new_delpath_message(
                src_interface.to_dict(),
                dst_interface.to_dict()
            )
            self.client.send(msg.pack().encode())
        return True

    def show_service_paths(self):
        """
        List active service paths in the network
        """
        res = {}
        for k, v in self.paths.items():
            chain = self.chains[v]
            res[f"{k[0].name} -> {k[1].name}"] = ",".join([node.shell_name for node in chain])
        return res

    def stop(self):
        """
        Remove all service function instances, service chains, paths and close
        the TCP connection with SDN controller
        """
        for nf in self.services.values():
            nf.stop()
        if self.client:
            try:
                msg = self.msg_gen.new_bye_message()
                self.client.send(msg.pack().encode())
                self.client.send('bye'.encode())
                self.client.close()
            except:
                pass


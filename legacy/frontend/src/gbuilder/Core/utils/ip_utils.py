import ipaddress


def is_valid_ip(address=None):
    """Check if a string makes up a valid IP address
    """
    try:
        ipaddress.ip_address(unicode(address))
        return True
    except ValueError:
        return False


def is_valid_network(network=None):
    try:
        ipaddress.ip_network(unicode(network))
        return True
    except ValueError:
        return False


def is_valid_flsm(network=None):
    """Check if a network has a valid fixed length subnet mask
    """
    try:
        network = ipaddress.ip_network(unicode(network))
        return network.prefixlen in [8, 16, 24]
    except ValueError:
        return False


def is_valid_base_network(network=None):
    """Check if a network can be a base address for Gini networks.
    It has to satisfy two conditions:
    - The network has a valid fixed length subnet mask
    - Length of the subnet mask cannot be greater than 16
    """
    try:
        network = ipaddress.ip_network(unicode(network))
        return network.prefixlen in [8, 16]
    except ValueError:
        return False


def is_valid_host_in_subnet(ip, subnet, netmask):
    ip = unicode(ip)
    subnet = unicode(subnet)
    netmask = unicode(netmask)
    return ipaddress.ip_address(ip) in ipaddress.ip_network((subnet, netmask)).hosts()


def len_to_mask(prefixlen):
    bin_mask = (0xffffffff >> (32 - prefixlen)) << (32 - prefixlen)
    a = str((bin_mask & 0xff000000) >> 24)
    b = str((bin_mask & 0x00ff0000) >> 16)
    c = str((bin_mask & 0x0000ff00) >> 8)
    d = str((bin_mask & 0x000000ff))
    return ".".join([a, b, c, d])


class BaseGiniIPv4Network(ipaddress.IPv4Network):
    def __init__(self, address, check=False, *args, **kwargs):
        super(BaseGiniIPv4Network, self).__init__(address, *args, **kwargs)
        self.subnets_generator = self.subnets(prefixlen_diff=8)
        if check:
            if not is_valid_base_network(self.with_netmask):
                raise ValueError(
                    "Not a valid base network address for Gini %s!" % self.with_netmask)

    def get_next_available_subnet(self):
        subnet = self.subnets_generator.next()
        return subnet

    def __str__(self):
        return self.with_netmask


class GiniIPv4Subnet(ipaddress.IPv4Network):
    def __init__(self, *args, **kwargs):
        super(GiniIPv4Subnet, self).__init__(*args, **kwargs)
        # skip value X.X.X.1, which is the default gateway for docker network
        self.host_generator = self.hosts().next()

    def get_next_available_host(self):
        host = self.host_generator.next()
        return str(host.exploded)

    def __str__(self):
        return self.with_netmask


class GiniIPv4Interface(ipaddress.IPv4Interface):
    pass

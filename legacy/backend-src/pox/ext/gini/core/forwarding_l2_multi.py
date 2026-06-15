#!/usr/bin/python2

from openflow import discovery
from forwarding import l2_multi

def launch():
    discovery.launch()
    l2_multi.launch()

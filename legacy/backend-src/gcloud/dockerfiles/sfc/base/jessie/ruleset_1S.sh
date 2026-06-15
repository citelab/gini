#!/bin/sh

# This iptables script opens up everything, i.e. no firewall

IP=iptables

# Flush all rules
$IP -F	

# Default all policies to DROP
$IP -P INPUT ACCEPT
$IP -P OUTPUT ACCEPT
$IP -P FORWARD ACCEPT

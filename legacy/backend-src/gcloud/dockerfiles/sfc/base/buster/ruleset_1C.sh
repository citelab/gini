#!/bin/sh

# This iptables script allows a user on the firewalled machine to connect
# to remote http servers

IP=iptables

# Flush all rules
$IP -F	

# Default all policies to DROP
$IP -P INPUT DROP
$IP -P OUTPUT DROP
$IP -P FORWARD DROP

# INPUT RULES #

# Allow packets from an http server (port 80) to connect to an unpriviledged
# port.  This allows a user on the machine to connect to an http server
# and receive data back.  The ! --syn command causes iptables to reject all
# SYN packets from the server.  Thus, a rouge user cannot tunnel through
# port 80 to make a connection.
$IP -A INPUT -p tcp ! --syn --sport 80 --dport 1024:65535 -j ACCEPT

# OUTPUT RULES #

# Allow all established states through the firewall
$IP -A OUTPUT -p tcp -m state --state ESTABLISHED -j ACCEPT
# Allow a local user to connect to an http server on an unpriviledged port
$IP -A OUTPUT -p tcp --sport 1024:65535 --dport 80 -m state --state NEW -j ACCEPT

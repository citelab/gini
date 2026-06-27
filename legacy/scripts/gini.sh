#!/bin/bash

# call this script as 
# gini.sh on to give permissions 
# gini.sh off to deny permissions
#

declare perm=$1
if [ $perm == "on" ]; then
	echo "Turning on permissions"
	sudo chmod a+s /sbin/ip
	sudo chmod a+s /sbin/brctl
else 
	echo "Turn off permissions"
	sudo chmod a-s /sbin/ip
	sudo chmod a-s /sbin/brctl
fi

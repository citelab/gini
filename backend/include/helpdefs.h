
#ifndef __HELPDEFINITIONS_H__
#define __HELPDEFINITIONS_H__


#define USAGE_HELP          "help [command]"
#define USAGE_VERSION       "version"
#define USAGE_SET           "set parameter [value]"
#define USAGE_GET			"get [parameter]"
#define USAGE_SOURCE        "source [filepath]"
#define USAGE_IFCONFIG		"ifconfig action device [action specific options]"
#define USAGE_ROUTE         "route action [action specific options]"
#define USAGE_ARP           "arp action [action specific options]"
#define USAGE_PING          "ping [options] target"
#define USAGE_CONSOLE    	"port (type) [restart]"
#define USAGE_HALT          "halt"
#define USAGE_EXIT          "exit"
#define USAGE_QUEUE   	    "queue action [action specific options]"
#define USAGE_QDISC			"qdisc qname tspec"
#define USAGE_SPOLICY		"spolicy action [action specific options]"
#define USAGE_CLASS		    "class cname [-src ip_spec [<min_port--max_port>]] [-dst ip_spec [<min_port--max_port>]] [-prot num] [-tos tos_spec]"
#define USAGE_FILTER     	"filter action [action specific options]"
#define USAGE_OPENFLOW      "openflow action [action specific options]"
#define USAGE_GNC           "gnc [-u] [-l <port>] <destination> <port>"


#define SHELP_HELP          "display help information on given command"
#define SHELP_VERSION       "get router version number"
#define SHELP_SET           "set control parameters"
#define SHELP_GET			"get (display) value of control parameters"
#define SHELP_SOURCE        "source filepath (if none, use .grouter at gRouter home)"
#define SHELP_IFCONFIG   	"add, del, and modify interface information"
#define SHELP_ROUTE         "add, del, and modify the route information"
#define SHELP_ARP           "add, del, and modify ARP table information"
#define SHELP_PING          "ping another router or machine"
#define SHELP_CONSOLE       "manage port (FIFO) used to interact with wireshark and visualizer"
#define SHELP_HALT          "halt the router"
#define SHELP_EXIT          "exit the command shell"
#define SHELP_QUEUE			"create, add, del, and view queues with given names"
#define SHELP_QDISC			"create a queuing discipline"
#define SHELP_SPOLICY		"set the inter queue scheduler"
#define SHELP_CLASS		    "create add, del, and view classifier information"
#define SHELP_FILTER		"create add, del, and view filtering rules; this uses class rules to group packets"
#define SHELP_OPENFLOW      "view OpenFlow switch information or force the OpenFlow switch to reconnect to the controller"
#define SHELP_GNC           "use gRouter netcat (gnc) to create udp and tcp connections"


/*
 * TODO: The long help descriptions should be revised with examples
 */

#define HELP_PREAMPLE       "These shell commands are defined internally. Type `help' to see \n\
this list. Type `help name' to find out more about the function `name'.\n\
Because the GINI shell uses the underlying Linux shell to run \n\
commands not recognized by it, most Linux commands can be accessed \n\
from this shell. If GINI shell reimplements a Linux command \n\
(e.g., ifconfig), then only GINI version can be accessed from the\n\
GINI shell. To access the Linux version, find the actual location\n\
of the Linux command (i.e., whereis ifconfig) and give the \n\
absolute path (e.g., /sbin/ifconfig). \n"

#define LHELP_HELP          "\tDisplay helpful information about builtin commands.  If <command> is\n\
\tspecified, gives detailed help on all commands matching <command>,\n\
\totherwise a list of the builtins is printed.\n"
#define LHELP_VERSION      	"\tShows the version information of the router."
#define LHELP_SET         	"set.hlp"
#define LHELP_GET         	"get.hlp"
#define LHELP_SOURCE       	"source.hlp"

#define LHELP_IFCONFIG     	"ifconfig.hlp"
#define LHELP_ROUTE         "route.hlp"
#define LHELP_ARP        	"arp.hlp"
#define LHELP_PING          "ping.hlp"
#define LHELP_CONSOLE      	"console.hlp"
#define LHELP_HALT          "\tHalts the router."
#define LHELP_EXIT          "\tExit the command shell without halting the router. The router\n\
\tshould go into the deamon once the CLI is exitted."
#define LHELP_QUEUE			"queue.hlp"
#define LHELP_QDISC			"qdisc.hlp"
#define LHELP_SPOLICY		"spolicy.hlp"
#define LHELP_CLASS			"class.hlp"
#define LHELP_FILTER		"filter.hlp"
#define LHELP_OPENFLOW      "openflow.hlp"
#define LHELP_GNC           "gnc.hlp"

#endif

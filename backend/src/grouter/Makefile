CFLAGS= -g -c -DHAVE_GETOPT_LONG=1 -DHAVE_SNPRINTF=1 -DHAVE_VSSCANF=1 -DHAVE_PTHREAD_RWLOCK=1 -I../../include

LDFLAGS=-lreadline -lslack -lpthread -lm -ldl
CC=gcc

SOURCES=arp.c classifier.c cli.c console.c ethernet.c filter.c fragment.c raw.c tun.c gnet.c grouter.c icmp.c info.c ip.c message.c mtu.c packetcore.c qdisc.c roundrobin.c routetable.c simplequeue.c tap.c tapio.c utils.c vpl.c wfq.c openflow_config.c openflow_flowtable.c openflow_ctrl_iface.c openflow_pkt_proc.c udp.c pbuf.c memp.c tcp_in.c tcp.c tcp_out.c inet_chksum.c


OBJECTS=$(SOURCES:.c=.o)
EXECUTABLE=gRouter

all: $(SOURCES) $(EXECUTABLE)

$(EXECUTABLE): $(OBJECTS)
	gcc $(OBJECTS) $(LDFLAGS) -o $@

.c.o:
	gcc $(CFLAGS) $< -o $@


clean:
	rm -rf *.o *~



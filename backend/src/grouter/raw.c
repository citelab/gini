/*
 * This is the low level driver for the wlan interface. 
 * It creates a wlan interface on the station and hooks up a 
 * raw socket to the interface.
 * 
 * Copyright (C) 2015 Ahmed Youssef (ahmed.youssef@mail.mcgill.ca)
 * Licensed under the GPL.
 */

#include <slack/err.h>
#include "sdn.h"        /* Z1: OpenFlow as ingress mode (seam) */
#include "raw.h"
#include "packetcore.h"
#include "classifier.h"
#include "filter.h"
#include "protocols.h"
#include "message.h"
#include "gnet.h"
#include "arp.h"
#include "ip.h"
#include "ethernet.h"
#include "icmp.h"

#include <netinet/in.h>
#include <errno.h>
#include <netdb.h>
#include <stdio.h> //For standard things
#include <stdlib.h>    //malloc
#include <string.h>    //strlen
#include <netpacket/packet.h> 
#include <netinet/ip_icmp.h>   //Provides declarations for icmp header
#include <netinet/udp.h>   //Provides declarations for udp header
#include <netinet/tcp.h>   //Provides declarations for tcp header
#include <netinet/ip.h>    //Provides declarations for ip header
#include <netinet/if_ether.h>  //For ETH_P_ALL
#include <net/ethernet.h>  //For ether_header
#include <sys/socket.h>
#include <arpa/inet.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>

extern pktcore_t *pcore;
extern classlist_t *classifier;
extern filtertab_t *filter;


extern router_config rconfig;

void *toRawDev(void *arg)
{
	gpacket_t *inpkt = (gpacket_t *)arg;
	interface_t *iface;
	arp_packet_t *apkt;
	char tmpbuf[MAX_TMPBUF_LEN];
	int pkt_size;

	verbose(1, "[toRawDev]:: entering the function.. ");
	// find the outgoing interface and device...
	if ((iface = findInterface(inpkt->frame.dst_interface)) != NULL)
	{
		char tmp[40];
		memset(tmp, 0, sizeof(tmp));
		IP2Dot(tmp, inpkt->frame.src_ip_addr);
		/* The Amazon network has the prefix 172, so if a packet is sent to the raw interface and it does
		not begin with that prefix, we know that the packet has come from the local gini network instead.
		In this case we want to apply a SNAT, to make the packet seem as if it has come from the cRouter 
		so that Amazon machines will be able to respond (recognize the address). Note that the reverse
		NAT operation is performed in ip.c*/
		/*if(inpkt->data.header.prot != htons(ARP_PROTOCOL) && !(tmp[0] == '1' && tmp[1] == '7' && tmp[2] == '2')) {
			//printf("\n\n TRYING TO PING AMAZON CLOUD\n");				
			//printGPacket(inpkt, 3, "CONNOR PACKET");
			ip_packet_t *ipkt = (ip_packet_t *)(inpkt->data.data);
			ipkt->ip_hdr_len = 5;                                  // no IP header options!!
			icmphdr_t *icmphdr = (icmphdr_t *)((uchar *)ipkt + ipkt->ip_hdr_len*4);
			printf("\n\nICMP ID: %d\n", icmphdr->un.echo.id); 
			The IP address given to the SNAT function is the private ip address of the 
			Amazon instance that is running the cRouter in reverse 
			applySNAT("62.44.31.172", (ip_packet_t*)inpkt->data.data, icmphdr->un.echo.id);
			printNAT();	
		}*/
		/* send IP packet or ARP reply */
		if (inpkt->data.header.prot == htons(ARP_PROTOCOL))
		{
			printf("CONNORS DEBUG arp in toRawDev\n");
			apkt = (arp_packet_t *) inpkt->data.data;
			COPY_MAC(apkt->src_hw_addr, iface->mac_addr);
			COPY_IP(apkt->src_ip_addr, gHtonl(tmpbuf, iface->ip_addr));
		}
		if(inpkt->data.header.prot == htons(ICMP_PROTOCOL)){
			printf("\nICMP Request over raw\n");
		}		
		pkt_size = findPacketSize(&(inpkt->data));
		verbose(2, "[toRawDev]:: raw_sendto called for interface %d.. ", iface->interface_id);
		raw_sendto(iface->vpl_data, &(inpkt->data), pkt_size);
		free(inpkt);          // finally destroy the memory allocated to the packet..
	} else
		error("[toRawDev]:: ERROR!! Could not find outgoing interface ...");

	// this is just a dummy return -- return value not used.
	return arg;
}


/*
 */
void* fromRawDev(void *arg)
{
    interface_t *iface = (interface_t *) arg;
    uchar bcast_mac[] = MAC_BCAST_ADDR;
    gpacket_t *in_pkt;
    int pktsize;
    char tmpbuf[MAX_TMPBUF_LEN];
    
    pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);		// die as soon as cancelled
    while (1)
    {
        verbose(2, "[fromRawDev]:: Receiving a packet ...");
        if ((in_pkt = (gpacket_t *)malloc(sizeof(gpacket_t))) == NULL)
        {
            fatal("[fromRawDev]:: unable to allocate memory for packet.. ");
            return NULL;
        }

        bzero(in_pkt, sizeof(gpacket_t));
        pktsize = raw_recvfrom(iface->vpl_data, &(in_pkt->data), sizeof(pkt_data_t));
        pthread_testcancel();
        
        verbose(2, "[fromRawDev]:: Destination MAC is %s ", MAC2Colon(tmpbuf, in_pkt->data.header.dst));
        // check whether the incoming packet is a layer 2 broadcast or
        // meant for this node... otherwise should be thrown..
        // TODO: fix for promiscuous mode packet snooping.
        if ((COMPARE_MAC(in_pkt->data.header.dst, iface->mac_addr) != 0) &&
                (COMPARE_MAC(in_pkt->data.header.dst, bcast_mac) != 0))
        {
            verbose(2, "[fromRawDev]:: Packet[%d] dropped .. not for this router!? ", pktsize);
            free(in_pkt);
            continue;
        }
		
        // copy fields into the message from the packet..
        in_pkt->frame.src_interface = iface->interface_id;
        COPY_MAC(in_pkt->frame.src_hw_addr, iface->mac_addr);
        COPY_IP(in_pkt->frame.src_ip_addr, iface->ip_addr);
	
	
	char buf[20];
	memset(buf, 0, sizeof(buf));
	IP2Dot(buf, in_pkt->frame.src_ip_addr);
//	if(strcmp(buf, "172.31.32.1")==0)
//		printf("FROM RAW IP %s\n", buf);
        // check for filtering.. if the it should be filtered.. then drop
     
	if (filteredPacket(filter, in_pkt))
        {
            verbose(2, "[fromRawDev]:: Packet filtered..!");
            free(in_pkt);
            continue;   // skip the rest of the loop
        }

        verbose(2, "[fromRawDev]:: Packet is sent for enqueuing..");
        enqueuePacket(pcore, in_pkt, sizeof(gpacket_t), (sdn_mode() == SDN_MODE_OPENFLOW));
    }
}


/*
 * Connect to the raw interface.
 */

vpl_data_t *raw_connect(uchar* mac_addr, char *bridge)
{
    struct sockaddr_ll sll;
    struct ifreq* ifr;
    int sock_raw;
    char interface[32];
    vpl_data_t *pri;
    
    verbose(2, "[raw_connect]:: starting connection.. ");
    
    sock_raw = socket( AF_PACKET , SOCK_RAW , htons(ETH_P_ALL)) ;	
    if (sock_raw == -1) {
        verbose(2, "[raw_connect]:: Creating raw socket failed, error = %s", strerror(errno));
        free(ifr);
        return NULL;
    }
    
    //interface[0] = 't';
    //sprintf(&interface[1], "%d", rconfig.top_num); 
    //Above is from Ahmeds implementation
    //For the cloud we want to use the eth0
    //interface on the amazon machine for the raw socket
    strcpy(interface, bridge);
    verbose(1, "[raw_connect]:: Binding to interface %s strlen = %d", interface, strlen(interface));
    ifr = calloc(1, sizeof(struct ifreq));
    strcpy((char*)ifr->ifr_name, interface);
            
    if((ioctl(sock_raw, SIOCGIFINDEX, ifr)) == -1)
    {
    	verbose(2, "[raw_connect]:: Unable to find interface index, error = %s", strerror(errno));
        free(ifr);
    	return NULL;
    }
    
    bzero(&sll, sizeof(sll));
    sll.sll_family = AF_PACKET;
    sll.sll_ifindex = ifr->ifr_ifindex;
    sll.sll_protocol = htons(ETH_P_ALL);
    
    verbose(2, "[raw_connect]:: ifr_index = %d", ifr->ifr_ifindex);
    if((bind(sock_raw, (struct sockaddr*) &sll, sizeof(sll))) == -1)
    {
        verbose(2, "[raw_connect]:: Binding raw Socket Failed, error = %s", strerror(errno));
        free(ifr);
        return NULL;
    }
    
    if((ioctl(sock_raw, SIOCGIFHWADDR, ifr)) == -1)
    {
    	verbose(2, "[raw_connect]:: Unable to find interface mac address, error = %s", strerror(errno));	
        free(ifr);
    	return NULL;
    }
    COPY_MAC(mac_addr, ifr->ifr_hwaddr.sa_data);
    
    pri = (vpl_data_t *)malloc(sizeof(vpl_data_t));
    bzero(pri, sizeof(sizeof(vpl_data_t))); 
    
    // initialize the vpl_data structure.. much of it is unused here.
    // we are reusing vpl_data_t to minimize the changes for other code.
    pri->sock_type = "raw";
    pri->ctl_sock = NULL;
    pri->ctl_addr = NULL;
    pri->data_addr = NULL;
    pri->control = -1;
    pri->data = sock_raw;
    pri->local_addr = (void*)ifr;

    return pri;
}



/*
 * Receive a packet from the vpl. You can use this with a "select"
 * function to multiplex between different interfaces or you can use
 * it in a multi-processed/multi-threaded server. The example code
 * given here should work in either mode.
 */
int raw_recvfrom(vpl_data_t *vpl, void *buf, int len)
{
    int n, rcv_addr_len;
    struct sockaddr rcvaddr;
    char tmpbuf[100];
    
    rcv_addr_len = sizeof(rcvaddr);
    n=recvfrom(vpl->data, buf, len, 0, &rcvaddr, &rcv_addr_len);
    if (n == -1) 
    {
        verbose(2, "[raw_recvfrom]:: unable to receive packet, error = %s", strerror(errno));		
        return EXIT_FAILURE;
    } 
    
    verbose(2, "[raw_recvfrom]:: Destination MAC is %s ", MAC2Colon(tmpbuf, buf));
    return EXIT_SUCCESS;   
}


/*
 * Send packet through the raw interface pointed by the vpl data structure..
 */
int raw_sendto(vpl_data_t *vpl, void *buf, int len)
{
    int n;
    
    n = send(vpl->data, buf, len, 0);
 
    if (n == -1) 
    {
	verbose(2, "[raw_sendto]:: unable to send packet, error = %s", strerror(errno));		
	return EXIT_FAILURE;
    }
       
    return EXIT_SUCCESS;
}


int create_raw_interface(unsigned char *nw_addr)
{
    int pid, status;                                             
    int error;                                        
    char* argv[4];                                               
                                                                 
    // Get executable path                                       
    argv[0] = malloc(strlen(rconfig.gini_home) + strlen("/iface.sh") + 5);
    strcpy(argv[0], rconfig.gini_home);                                  
    strcat(argv[0], "/iface.sh");                                                              
                                                                 
    // Get topology number in ascii                              
    argv[1] = malloc(4);                                         
    sprintf(argv[1], "%d", rconfig.top_num);                             
                                                                 
    // Get IP address in ascii                                   
    argv[2] = malloc(20);                                        
    IP2Dot(argv[2], nw_addr);                                                          
                                                                 
    argv[3] = NULL;                      
    
    pid = fork();                                                                
    if(pid == 0) {                       
        execvp(argv[0], argv);               
        error = -1;                      
        exit(-1);                        
    } else {                             
        waitpid(pid, &status, 0);        
        if(WEXITSTATUS(status) == 1) {   
            error = -1;                          
        }                                        
    }                                                 
    
    free(argv[0]);
    free(argv[1]); 
    free(argv[2]);   
    
    return error;   
}

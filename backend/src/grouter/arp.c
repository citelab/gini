/*
 * arp.c (collection of functions that implement the ARP
 * (address resolution protocol).
 * AUTHOR: Original version by Weiling Xu
 *         Revised by Muthucumaru Maheswaran
 * DATE:   Last revision on June 22, 2008
 *
 */

#include <slack/std.h>
#include <slack/err.h>
#include <slack/prog.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include <netinet/in.h>
#include "protocols.h"
#include "arp.h"
#include "gnet.h"
#include "moduledefs.h"
#include "grouter.h"
#include "packetcore.h"
#include "verbose.h"


int tbl_replace_indx;            // overwrite this element if no free space in ARP table
int buf_replace_indx;            // overwrite this element if no free space in ARP buffer
arp_entry_t ARPtable[MAX_ARP];		                // ARP table
arp_buffer_entry_t ARPbuffer[MAX_ARP_BUFFERS];   	// ARP buffer for unresolved packets


extern pktcore_t *pcore;


void ARPInit()
{
  gpacket_t in_pkt;
  char tmpbuf[MAX_NAME_LEN];

  verbose(2, "[initARP]:: Initializing the ARP table and buffer ");

  ARPInitTable();                    // initialize APR table
  ARPInitBuffer();                   // initialize ARP buffer

}


/*
 * ARPSend2Output: message is sent to the output queue from ARP.
 * The output queue is drained by the output thread.
 * ARGUMENTS: pkt - the GINI packet to be sent out
 *
 * RETURNS: EXIT_SUCCESS on success and EXIT_FAILURE otherwise.
 */

int ARPSend2Output(gpacket_t *pkt)
{
  int vlevel;

  if (pkt == NULL)
  {
    verbose(1, "[ARPSend2Output]:: NULL pointer error... nothing sent");
    return EXIT_FAILURE;
  }

  vlevel = prog_verbosity_level();
  if (vlevel >= 3)
    printGPacket(pkt, vlevel, "ARP_ROUTINE");

  return writeQueue(pcore->outputQ, (void *)pkt, sizeof(gpacket_t));
}


/*
 * ARPResolve: this routine is responsible for local ARP resolution.
 * It consults the local ARP cache to determine whether a valid ARP entry
 * is present. If a valid entry is not present, a remote request is sent out
 * and the packet that caused the request is buffered. The buffer is flushed
 * when the reply comes in.
 */
int ARPResolve(gpacket_t *in_pkt)
{
  uchar mac_addr[6];
  char tmpbuf[MAX_TMPBUF_LEN];

  in_pkt->data.header.prot = htons(IP_PROTOCOL);
  // lookup the ARP table for the MAC for next hop
  if (ARPFindEntry(in_pkt->frame.nxth_ip_addr, mac_addr) == EXIT_FAILURE)
  {
    // no ARP match, buffer and send ARP request for next
    verbose(2, "[ARPResolve]:: buffering packet, sending ARP request");
    ARPAddBuffer(in_pkt);
    in_pkt->frame.arp_bcast = TRUE;                        // tell gnet this is bcast to prevent recursive ARP lookup!
    // create a new message for ARP request
    ARPSendRequest(in_pkt);
    return EXIT_SUCCESS;;
  }

  verbose(2, "[ARPResolve]:: sent packet to MAC %s", MAC2Colon(tmpbuf, mac_addr));
  COPY_MAC(in_pkt->data.header.dst, mac_addr);
  in_pkt->frame.arp_valid = TRUE;
  ARPSend2Output(in_pkt);

  return EXIT_SUCCESS;
}


/*
 * ARPProcess: Process a received ARP packet... from remote nodes. If it is
 * a reply for a ARP request sent from the local node, use it
 * to update the local ARP cache. Flush (dequeue, process, and send) any packets
 * that are buffered for ARP processing that match the ARP reply.

 * If it a request, send a reply.. no need to record any state here.
 */
void ARPProcess(gpacket_t *pkt)
{
  char tmpbuf[MAX_TMPBUF_LEN];

  arp_packet_t *apkt = (arp_packet_t *) pkt->data.data;

  // check packet is ethernet and addresses of IP type.. otherwise throw away
  if ((ntohs(apkt->hw_addr_type) != 0x0001) || (ntohs(apkt->arp_prot) != IP_PROTOCOL))
  {
    verbose(2, "[ARPProcess]:: unknown hwtype or protocol, dropping ARP packet");
    return;
  }


  verbose(2, "[ARPProcess]:: adding sender of received packet to ARP table");
  ARPAddEntry((const char *)gNtohl((uchar *)tmpbuf, apkt->src_ip_addr), 
              (const char *)apkt->src_hw_addr);

  // Check it's actually destined to us,if not throw packet
  if (COMPARE_IP(apkt->dst_ip_addr, gHtonl((uchar *)tmpbuf, pkt->frame.src_ip_addr)) != 0)
  {
    verbose(2, "[APRProcess]:: packet has a frame source (after ntohl) %s ...",
        IP2Dot(tmpbuf, gNtohl((uchar *)tmpbuf, pkt->frame.src_ip_addr)));

    verbose(2, "[APRProcess]:: packet destined for %s, dropping",
        IP2Dot(tmpbuf, gNtohl((uchar *)tmpbuf, apkt->dst_ip_addr)));
    return;
  }

  // We have a valid ARP packet, lets process it now.
  // If it's a REQUEST, send a reply back
  if (ntohs(apkt->arp_opcode) == ARP_REQUEST)
  {
    apkt->arp_opcode = htons(ARP_REPLY);
    COPY_MAC(apkt->src_hw_addr, pkt->frame.src_hw_addr);
    COPY_MAC(apkt->dst_hw_addr, pkt->data.header.src);
    COPY_IP(apkt->dst_ip_addr, apkt->src_ip_addr);
    COPY_IP(apkt->src_ip_addr, gHtonl((uchar *)tmpbuf, pkt->frame.src_ip_addr));

    verbose(2, "[ARPProcess]:: packet was ARP REQUEST, sending ARP REPLY packet");

    // prepare for sending. Set some parameters that is going to be used
    // by the GNET adapter...

    pkt->frame.dst_interface = pkt->frame.src_interface;

    COPY_MAC(pkt->data.header.dst, pkt->data.header.src);
    COPY_MAC(pkt->data.header.src,  pkt->frame.src_hw_addr);
    COPY_IP(pkt->frame.nxth_ip_addr, gNtohl((uchar *)tmpbuf, apkt->dst_ip_addr));
    pkt->frame.arp_valid = TRUE;

    pkt->data.header.prot = htons(ARP_PROTOCOL);

    ARPSend2Output(pkt);
  }
  else if (ntohs(apkt->arp_opcode) == ARP_REPLY)
  {
    // Flush buffer of any packets waiting for the incoming ARP..
    verbose(2, "[ARPProcess]:: packet was ARP REPLY... ");
    ARPFlushBuffer((const char *)gNtohl((uchar *)tmpbuf, apkt->src_ip_addr), 
                   (const char *)apkt->src_hw_addr);
    verbose(2, "[ARPProcess]:: flushed the ARP buffer ... ");
  }
  else
    verbose(2, "[ARPProcess]:: unknown ARP type");

  return;
}


/*-------------------------------------------------------------------------
 *                   A R P  T A B L E  F U N C T I O N S
 *-------------------------------------------------------------------------*/

/*
 * initialize the ARP table
 */
void ARPInitTable()
{
  int i;

  tbl_replace_indx = 0;

  for (i = 0; i < MAX_ARP; i++)
    ARPtable[i].is_empty = TRUE;

  verbose(2, "[ARPInitTable]:: ARP table initialized.. ");
  return;
}

void ARPReInitTable()
{
  ARPInitTable();
}


/*
 * Find an ARP entry matching the supplied IP address in the ARP table
 * ARGUMENTS: uchar *ip_addr: IP address to look up
 *            uchar *mac_addr: returned MAC address corresponding to the IP
 * The MAC is only set when the return status is EXIT_SUCCESS. If error,
 * the MAC address (mac_addr) is undefined.
 */
int ARPFindEntry(uchar *ip_addr, uchar *mac_addr)
{
  int i;
  char tmpbuf[MAX_TMPBUF_LEN];

  for (i = 0; i < MAX_ARP; i++)
  {
    if(ARPtable[i].is_empty == FALSE &&
        COMPARE_IP(ARPtable[i].ip_addr, ip_addr) == 0)
    {
      // found IP address - copy the MAC address
      COPY_MAC(mac_addr, ARPtable[i].mac_addr);
      verbose(2, "[ARPFindEntry]:: found ARP entry #%d for IP %s", i, IP2Dot(tmpbuf, ip_addr));
      return EXIT_SUCCESS;
    }
  }

  verbose(2, "[ARPFindEntry]:: failed to find ARP entry for IP %s", IP2Dot(tmpbuf, ip_addr));
  return EXIT_FAILURE;
}



/*
 * add an entry to the ARP table
 * ARGUMENTS: uchar *ip_addr - the IP address (4 bytes)
 *            uchar *mac_addr - the MAC address (6 bytes)
 * RETURNS: Nothing
 */
void ARPAddEntry(const char *ip_addr, const char *mac_addr)
{
  int i;
  int empty_slot = MAX_ARP;
  char tmpbuf[MAX_TMPBUF_LEN];
  uchar *uip = (uchar *)ip_addr;
  uchar *umac = (uchar *)mac_addr;

  for (i = 0; i < MAX_ARP; i++)
  {
    if ((ARPtable[i].is_empty == FALSE) &&
        (COMPARE_IP(ARPtable[i].ip_addr, (const uchar *)ip_addr) == 0))
    {
      // update entry
      COPY_IP(ARPtable[i].ip_addr, (const uchar *)ip_addr);
      COPY_MAC(ARPtable[i].mac_addr, (const uchar *)mac_addr);

      verbose(2, "[ARPAddEntry]:: updated ARP table entry #%d: IP %s = MAC %s", i,
          IP2Dot(tmpbuf, uip), MAC2Colon(tmpbuf+20, umac));
      return;
    }
    if (ARPtable[i].is_empty == TRUE) {
      empty_slot = i;
      break;
    }
  }

  if (empty_slot == MAX_ARP)
  {
    // ARP table full.. do the replacement
    // use the FIFO strategy: table replace index is the FIFO pointer
    empty_slot = tbl_replace_indx;
    tbl_replace_indx = (tbl_replace_indx + 1) % MAX_ARP;
  }

  // add new entry or overwrite the replaced entry
  ARPtable[empty_slot].is_empty = FALSE;
  COPY_IP(ARPtable[empty_slot].ip_addr, (const uchar *)ip_addr);
  COPY_MAC(ARPtable[empty_slot].mac_addr, (const uchar *)mac_addr);

  verbose(2, "[ARPAddEntry]:: updated ARP table entry #%d: IP %s = MAC %s", empty_slot,
      IP2Dot(tmpbuf, uip), MAC2Colon(tmpbuf+20, umac));

  return;
}


/*
 * print the ARP table
 */
void ARPPrintTable(void)
{
  int i;
  char tmpbuf[MAX_TMPBUF_LEN];

  printf("-----------------------------------------------------------\n");
  printf("      A R P  T A B L E \n");
  printf("-----------------------------------------------------------\n");
  printf("Index\tIP address\tMAC address \n");

  for (i = 0; i < MAX_ARP; i++)
    if (ARPtable[i].is_empty == FALSE)
      printf("%d\t%s\t%s\n", i, IP2Dot(tmpbuf, ARPtable[i].ip_addr), MAC2Colon((tmpbuf+20), ARPtable[i].mac_addr));
  printf("-----------------------------------------------------------\n");
  return;
}

/*
 * Delete ARP entry with the given IP address
 */
void ARPDeleteEntry(const char *ip_addr)
{
  int i;

  for (i = 0; i < MAX_ARP; i++)
  {
    if ( (ARPtable[i].is_empty == FALSE) &&
        (COMPARE_IP(ARPtable[i].ip_addr, (const uchar *)ip_addr)) == 0)
    {
      ARPtable[i].is_empty = TRUE;
      verbose(2, "[ARPDeleteEntry]:: arp entry #%d deleted", i);
    }
  }
  return;
}


/*
 * send an ARP request to eventually process message,
 * a copy of which is now in the buffer
 */
void ARPSendRequest(gpacket_t *pkt)
{
  arp_packet_t *apkt = (arp_packet_t *) pkt->data.data;
  uchar bcast_addr[6];
  char tmpbuf[MAX_TMPBUF_LEN];

  memset(bcast_addr, 0xFF, 6);

  /*
   * Create ARP REQUEST packet
   * ether header will be set in GNET_ADAPTER
   * arp header
   */
  apkt->hw_addr_type = htons(0x0001);      // set hw type for Ethernet
  apkt->arp_prot = htons(IP_PROTOCOL);                // set prtotocol address format

  apkt->hw_addr_len = 6;                              // address length
  apkt->arp_prot_len = 4;                             // protocol address length
  apkt->arp_opcode = htons(ARP_REQUEST);              // set ARP request opcode

  // source hw addr will be set in GNET_ADAPTER
  // source ip addr will be set in GNET_ADAPTER
  COPY_MAC(apkt->dst_hw_addr, bcast_addr);            // target hw addr

  COPY_IP(apkt->dst_ip_addr, gHtonl((uchar *)tmpbuf, pkt->frame.nxth_ip_addr));    // target ip addr

  // send the ARP request packet
  verbose(2, "[sendARPRequest]:: sending ARP request for %s",
      IP2Dot(tmpbuf, pkt->frame.nxth_ip_addr));

  // prepare sending.. to GNET adapter..

  COPY_MAC(pkt->data.header.dst, bcast_addr);
  pkt->data.header.prot = htons(ARP_PROTOCOL);
  // actually send the message to the other module..
  ARPSend2Output(pkt);

  return;
}


/*-------------------------------------------------------------------------
 *                   A R P  B U F F E R  F U N C T I O N S
 *-------------------------------------------------------------------------*/


/*
 * initialize buffer
 */
void ARPInitBuffer()
{
  int i;

  buf_replace_indx = 0;

  for (i = 0; i < MAX_ARP_BUFFERS; i++)
    ARPbuffer[i].is_empty = TRUE;

  verbose(2, "[initARPBuffer]:: packet buffer initialized");
  return;
}


/*
 * Add a packet to ARP buffer: This packet is waiting resolution
 * ARGUMENTS: in_pkt - pointer to message that is to be copied into buffer
 * RETURNS: none
 */
void ARPAddBuffer(gpacket_t *in_pkt)
{
  int i;
  gpacket_t *cppkt;

  // duplicate the packet..
  cppkt = duplicatePacket(in_pkt);

  // Find an empty slot
  for (i = 0; i < MAX_ARP_BUFFERS; i++){
    if (ARPbuffer[i].is_empty == TRUE)
    {
      ARPbuffer[i].is_empty = FALSE;
      ARPbuffer[i].wait_msg = cppkt;
      verbose(2, "[addARPBuffer]:: packet stored in entry %d", i);
      return;
    }
  }

  // No empty spot? Replace a packet, we need to deallocate the old packet
  free(ARPbuffer[i].wait_msg);
  ARPbuffer[i].wait_msg = cppkt;
  verbose(2, "[addARPBuffer]:: buffer full, packet buffered to replaced entry %d",
      buf_replace_indx);
  buf_replace_indx = (buf_replace_indx + 1) % MAX_ARP_BUFFERS; // adjust for FIFO

  return;
}


/*
 * get a packet from the ARP buffer
 * ARGUMENTS: out_pkt - pointer at which packet matching message is to be copied
 *              nexthop - pointer to dest. IP address to search for
 * RETURNS: The function returns EXIT_SUCCESS if packet was found and copied,
 * or EXIT_FAILURE if it was not found.
 */
int ARPGetBuffer(gpacket_t **out_pkt, uchar *nexthop)
{
  int i;
  char tmpbuf[MAX_TMPBUF_LEN];

  // Search for packet in buffer
  for (i = 0; i < MAX_ARP_BUFFERS; i++)
  {
    if (ARPbuffer[i].is_empty == TRUE) continue;
    if (COMPARE_IP(ARPbuffer[i].wait_msg->frame.nxth_ip_addr, nexthop) == 0)
    {
      // match found
      *out_pkt =  ARPbuffer[i].wait_msg;
      ARPbuffer[i].is_empty = TRUE;
      verbose(2, "[ARPGetBuffer]:: found packet matching nexthop %s at entry %d",
          IP2Dot(tmpbuf, nexthop), i);
      return EXIT_SUCCESS;
    }
  }
  verbose(2, "[ARPGetBuffer]:: no match for nexthop %s", IP2Dot(tmpbuf, nexthop));
  return EXIT_FAILURE;
}


/*
 * flush all packets from buffer matching the nexthop
 * for which we now have an ARP entry
 */
void ARPFlushBuffer(const char *next_hop, const char *mac_addr)
{
  gpacket_t *bfrd_msg;
  char tmpbuf[MAX_TMPBUF_LEN];

  verbose(2, "[ARPFlushBuffer]:: Entering the function.. ");
  while (ARPGetBuffer(&bfrd_msg, (uchar *)next_hop) == EXIT_SUCCESS)
  {
    // a message is already buffered.. send it out..
    // TODO: include QoS routines.. for now they are removed!

    // send to gnetAdapter
    // no need to set dst_int_num -- why?

    verbose(2, "[ARPFlushBuffer]:: flushing the entry with next_hop %s ", IP2Dot(tmpbuf, (uchar *)next_hop));
    COPY_MAC(bfrd_msg->data.header.dst, (const uchar *)mac_addr);
    ARPSend2Output(bfrd_msg);
  }

  return;
}

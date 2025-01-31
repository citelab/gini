/*
 * classifier.c (simple classifier system for gRouter)
 * AUTHOR: Muthucumaru Maheswaran
 * DATE: June 27, 2008
 *
 * This is a simple packet classifier. This classifier maintains a set
 * of named class specifications. These class specifications can be used
 * in firewall or queue definitions.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <slack/list.h>
#include "classspec.h"
#include "classifier.h"
#include "ip.h"

#include <slack/std.h>
#include <slack/err.h>
#include <slack/prog.h>
#include <slack/map.h>
#include "verbose.h"  // For fatal()
#include "message.h"
#include "grouter.h"


// create a classifier with the given name
classlist_t *createClassifier()
{
	classlist_t *cl;

	if ((cl = (classlist_t *) malloc(sizeof(classlist_t))) == NULL)
	{
		fatal("[createClassifier]:: Could not allocate memory for the classifier object..");
		return NULL;
	}

	if (!(cl->deftab = list_create(free)))
	{
		fatal("[createClassifier]:: Could not create the rule list..");
		return NULL;
	}

	cl->defcnt = 0;
	// the list owns the elements.. memory is managed by the list
	list_own(cl->deftab, free);

	return cl;
}


/*
 * returns 1 the class definition is added successfully to the
 * class list. Otherwise returns 0. The successful addition means
 * memory has been allocated to the data structure and name is
 * initialized.
 */
int addClassDef(classlist_t *clas, char *cname)
{
	classdef_t *cdef;

	// do duplicate check..
	if (getClassDef(clas, cname) != NULL)
		return 0;

	if ((cdef = (classdef_t *) malloc(sizeof(classdef_t))) == NULL)
	{
		fatal("[createClassRule]:: Could not allocate memory for class rule structure ");
		return 0;
	}

	bzero(cdef, sizeof(classdef_t));
	strcpy(cdef->cname, cname);
	cdef->cdefid = ++(clas->defcnt);
	list_prepend(clas->deftab, cdef);

	return 1;
}

int delClassDef(classlist_t *clas, char *cname)
{
	classdef_t *ptr;
	Lister *lster;

	for (lster = lister_create(clas->deftab); lister_has_next(lster) == 1; )
	{
		ptr = (classdef_t *)lister_next(lster);
		if (strcmp(cname, ptr->cname) == 0)
		{
			lister_remove(lster);
			break;
		}
	}
	lister_release(lster);

	return 1;
}


// returns NULL if the class definition is not present
classdef_t *getClassDef(classlist_t *clas, char *cname)
{
	classdef_t *ptr, *saveptr;
	Lister *lster;
	int found = 0;

	for (lster = lister_create(clas->deftab); lister_has_next(lster) == 1; )
	{
		ptr = (classdef_t *)lister_next(lster);
		if (strcmp(cname, ptr->cname) == 0)
		{
			saveptr = ptr;
			found = 1;
			break;
		}
	}
	lister_release(lster);

	if (found)
		return saveptr;
	else
		return NULL;
}




// RuleID  RuleTag  SRC IP Src Port  Dst IP Dst Port Prot TOS
void printClassDef(classdef_t *cr)
{
	char tmpbuf[MAX_TMPBUF_LEN];

	printf("%d\t\%s\t", cr->cdefid, cr->cname);
	if (cr->srcspec == NULL)
		printf(" ANY-ADDR ");
	else
		printf("%s/%d\t", IP2Dot(tmpbuf, cr->srcspec->ip_addr), cr->srcspec->preflen);
	if (cr->srcports == NULL)
		printf(" ALL-PORTS ");
	else
		printf("<%d -- %d>\t", cr->srcports->minport, cr->srcports->maxport);

	if (cr->dstspec == NULL)
		printf(" ANY-ADDR ");
	else
		printf("%s/%d\t", IP2Dot(tmpbuf, cr->dstspec->ip_addr), cr->dstspec->preflen);
	if (cr->dstports == NULL)
		printf(" ALL-PORTS ");
	else
		printf("<%d -- %d>\t", cr->dstports->minport, cr->dstports->maxport);

	printf("%x\t", cr->prot);
	printf("%x\n", cr->tos);
}


void printClassifier(classlist_t *cl)
{
	Lister *lstr;
	classdef_t *ptr;

	printf("\nRule ID\tRule Tag\tSrc IP\t<Src Port Range>\tDst IP\t<Dst Port Range>\tProtocol\tTOS\n");
	lstr = lister_create(cl->deftab);
	while ((ptr = ((classdef_t*)lister_next(lstr))) != NULL)
		printClassDef(ptr);

	lister_release(lstr);
	printf("\n\n");
}


int insertIPSpec(classlist_t *clas, char *cname, int srcside, ip_spec_t *ipspec)
{
	Lister *lstr;
	classdef_t *ptr;

	lstr = lister_create(clas->deftab);
	while ((ptr = ((classdef_t*)lister_next(lstr))) != NULL)
	{
		if (!strcmp(ptr->cname, cname))
		{
			if (srcside)
				ptr->srcspec = ipspec;
			else
				ptr->dstspec = ipspec;
			break;
		}
	}
	lister_release(lstr);
	return 1;
}

int insertPortRangeSpec(classlist_t *clas, char *cname, int srcside, port_range_t *prspec)
{
	Lister *lstr;
	classdef_t *ptr;

	lstr = lister_create(clas->deftab);
	while ((ptr = ((classdef_t*)lister_next(lstr))) != NULL)
	{
		if (!strcmp(ptr->cname, cname))
		{
			if (srcside)
				ptr->srcports = prspec;
			else
				ptr->dstports = prspec;
			break;
		}
	}
	lister_release(lstr);
	return 1;
}

int insertProtSpec(classlist_t *clas, char *cname, int prot)
{
	Lister *lstr;
	classdef_t *ptr;

	lstr = lister_create(clas->deftab);
	while ((ptr = ((classdef_t*)lister_next(lstr))) != NULL)
	{
		if (!strcmp(ptr->cname, cname))
		{
			ptr->prot = prot;
			break;
		}
	}
	lister_release(lstr);
	return 1;
}

int insertTOSSpec(classlist_t *clas, char *cname, int tos)
{
	Lister *lstr;
	classdef_t *ptr;

	lstr = lister_create(clas->deftab);
	while ((ptr = ((classdef_t*)lister_next(lstr))) != NULL)
	{
		if (!strcmp(ptr->cname, cname))
		{
			ptr->tos = tos;
			break;
		}
	}
	lister_release(lstr);
	return 1;

}


int compareIP2Spec(uchar ip[], ip_spec_t *ips)
{
	int preflen;
	uchar spec[4];
	char tmpbuf[MAX_TMPBUF_LEN];
	uchar temp[4] = {0,0,0,0}, mask, tbyte = 0;
	int prefbytes;
	int i, j, rembits;

	if (ips == NULL) return 1;

	preflen = ips->preflen;
	prefbytes = preflen/8;

	{
		uchar tmp[4];
		bcopy(gHtonl(tmp, ips->ip_addr), spec, 4);
	}

	for (i = 0; i < prefbytes; i++)
		temp[i] = ip[i];

	if (prefbytes < 4)
	{
		rembits = preflen - (prefbytes * 8);
		mask = 0x80;

		for (j = 0; j < rembits; j++)
		{
			tbyte = tbyte | mask;
			mask = mask >> 1;
		}

		temp[prefbytes] = ip[prefbytes] & tbyte;
	}
	return COMPARE_IP(temp, spec) == 0;
}


int compareProt2Spec(int prot, int pspec)
{
	if (pspec == 0) return 1;
	if (prot == pspec) return 1;
	return 0;
}


int compareTos2Spec(int tos, int tspec)
{
	if (tspec == 0) return 1;
	if (tos == tspec) return 1;
	return 0;
}


/*
 * TODO: What happened to the port range check? Not implemented?
 * Returns 1 if the rule given by cdef matches the packet and 0 otherwise.
 */
int isRuleMatching(classdef_t *cdef, gpacket_t *in_pkt)
{

	ip_packet_t *ip_pkt = (ip_packet_t *)&in_pkt->data.data;

	return compareIP2Spec(ip_pkt->ip_src, cdef->srcspec) *
		compareIP2Spec(ip_pkt->ip_dst, cdef->dstspec) *
		compareProt2Spec(ip_pkt->ip_prot, cdef->prot) *
		compareTos2Spec(ip_pkt->ip_tos, cdef->tos);
}



/*
 * filter.c (simple packet filter for gRouter)
 * AUTHOR: Muthucumaru Maheswaran
 * DATE: October 1, 2008

 * This is a simple packet filter. It is called by the input thread to filter
 * an incoming packet according to the rule set configured by the user.
 * At startup, we don't have any filters.
 *
 * Using the packet filter, a state-less firewall can be easily configured.
 *
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <slack/std.h>
#include <slack/err.h>
#include <slack/list.h>
#include "classspec.h"
#include "classifier.h"
#include "filter.h"
#include "ip.h"
#include "verbose.h"
#include "message.h"
#include "grouter.h"
#include "protocols.h"


filtertab_t *createFilter(classlist_t *cl, int state)
{
	filtertab_t *ft;

	ft = (filtertab_t *) malloc(sizeof(filtertab_t));
	ft->filteron = state;
	ft->clist = cl;
	ft->rulecnt = 0;

	return ft;
}


void moveRuleDown(filtertab_t *ft, int rulenum)
{
	filterrule_t *fr;

	if (rulenum < (ft->rulecnt-1))
	{
		fr = ft->ruletab[rulenum];
		ft->ruletab[rulenum] = ft->ruletab[rulenum+1];
		ft->ruletab[rulenum+1] = fr;
	}
}

void moveRuleUp(filtertab_t *ft, int rulenum)
{
	filterrule_t *fr;

	if (rulenum  > 0)
	{
		fr = ft->ruletab[rulenum];
		ft->ruletab[rulenum] = ft->ruletab[rulenum-1];
		ft->ruletab[rulenum-1] = fr;
	}
}

void moveRuleTop(filtertab_t *ft, int rulenum)
{
	filterrule_t *fr;
	int j;

	if (rulenum  > 0)
	{
		fr = ft->ruletab[rulenum];
		for (j = rulenum; j > 0; j--)
			ft->ruletab[j] = ft->ruletab[j-1];
		ft->ruletab[0] = fr;
	}
}


void moveRuleBottom(filtertab_t *ft, int rulenum)
{
	filterrule_t *fr;
	int j;

	if (rulenum < (ft->rulecnt-1))
	{
		fr = ft->ruletab[rulenum];
		for (j = rulenum; j <(ft->rulecnt-1); j++)
			ft->ruletab[j] = ft->ruletab[j+1];
		ft->ruletab[ft->rulecnt-1] = fr;
	}
}

void moveRule(filtertab_t *ft, int rulenum, char *dir)
{
	if (!strcmp(dir, "up"))
			moveRuleUp(ft, rulenum);
	else if (!strcmp(dir, "down"))
		moveRuleDown(ft, rulenum);
	else if (!strcmp(dir, "top"))
		moveRuleTop(ft, rulenum);
	else if (!strcmp(dir, "bottom"))
		moveRuleBottom(ft, rulenum);
	else
		printf("Invalid direction %s\n", dir);
}


void delFilterRule(filtertab_t *ft, int rulenum)
{
	int j;

	free(ft->ruletab[rulenum]);
	for (j = rulenum; j <(ft->rulecnt-1); j++)
		ft->ruletab[j] = ft->ruletab[j+1];
	ft->rulecnt--;
	if (ft->rulecnt <= 0)
		ft->filteron = 0;
}

void flushFilter(filtertab_t *ft)
{
	int j;

	for (j = 0; j < ft->rulecnt; j++)
		free(ft->ruletab[j]);
	ft->rulecnt = 0;
	ft->filteron = 0;
}


/*
 * Returns 1 if successful in adding the filter or 0 otherwise.
 * Fails if another rule is present with the given classifier or
 * the classifier is not present.
 */
int addFilterRule(filtertab_t *ft, int type, char *cname)
{
	int j;
	filterrule_t *fr;

	for (j = 0; j < ft->rulecnt; j++)
	{
		if (!strcmp(ft->ruletab[j]->cname, cname))
		{
			verbose(2, "[addFilterRule]:: rule [%s] already present..denied addition", cname);
			return 0;
		}
	}

	if (getClassDef(ft->clist, cname) == NULL)
	{
		verbose(2, "[addFilterRule]:: classDef [%s] not present", cname);
		return 0;
	}

	fr = (filterrule_t *)malloc(sizeof(filterrule_t));
	fr->type = type;
	strcpy(fr->cname, cname);
	fr->failures = fr->passes = 0;
	ft->ruletab[ft->rulecnt] = fr;
	ft->rulecnt++;
	ft->filteron = 1;

	return 1;
}


// returns 1 if the packet is filtered.. otherwise returns 0
int filteredPacket(filtertab_t *ft, gpacket_t *in_pkt)
{
	int j, matched = 0;
	classdef_t *cdef;

	// if filtering is OFF, then return 0
	if (!ft->filteron)
		return 0;

	for (j = 0; j < ft->rulecnt; j++)
	{
		cdef = getClassDef(ft->clist, ft->ruletab[j]->cname);
		if (cdef == NULL)
			continue;
		if (isRuleMatching(cdef, in_pkt) && (ft->ruletab[j]->type == 0))
		{
			matched = 1;
			break;
		}
	}

	return matched;
}


void printFilterStats(filtertab_t *ft)
{
	int j;

	for (j =0; j < ft->rulecnt; j++)
	{
		if (ft->ruletab[j]->type)
			printf("Allow\t");
		else
			printf("Deny\t");
		printf("%s\t", ft->ruletab[j]->cname);
		printf("%d\t%d\n", ft->ruletab[j]->passes, ft->ruletab[j]->failures);
	}
}


void printFilter(filtertab_t *ft)
{
	int j;
	classdef_t *cdef;

	printf("Filter has %d rules.\n\n", ft->rulecnt);
	for (j = 0; j < ft->rulecnt; j++)
	{
		if (ft->ruletab[j]->type)
			printf("Allow\t");
		else
			printf("Deny\t");

		printf("%s\t", ft->ruletab[j]->cname);
		cdef = getClassDef(ft->clist, ft->ruletab[j]->cname);
		if (cdef == NULL)
			continue;
		printClassDef(cdef);
		printf("\n");
	}
	printf("\n");
}

/*
 * cli.h (header file for the Command line handler)
 *
 * AUTHOR: Muthucumaru Maheswaran
 * DATE:   December 25, 2004
 *
 * The CLI is used as a configuration file parser
 * as well. Right now the CLI module is only capable
 * of parsing a very simple format and limited command set...
 * Future development should make the CLI more IOS like??
 */

#ifndef __CLI_H__
#define __CLI_H__

#include <string.h>
#include <stdio.h>
#include <slack/std.h>
#include <slack/map.h>
#include <slack/err.h>
#include "message.h"
#include "grouter.h"
#include "routetable.h"  // For printRouteTable
#include "console.h"     // For console functions
#include "info.h"        // For time mode functions
#include "openflow_config.h" // For OpenFlow config functions
#include "openflow_ctrl_iface.h" // For OpenFlow control interface


#define PROGRAM                       10
#define JOIN                          11
#define COMMENT                       12
#define COMMENT_CHAR                  '#'
#define CONTINUE_CHAR                 '\\'
#define CARRIAGE_RETURN               '\r'
#define LINE_FEED                     '\n'

#define MAX_BUF_LEN                   4096
#define MAX_LINE_LEN                  1024
#define MAX_KEY_LEN                   64



typedef struct _cli_entry_t
{
	char keystr[MAX_KEY_LEN];
	char long_helpstr[MAX_BUF_LEN];
	char short_helpstr[MAX_BUF_LEN];
	char usagestr[MAX_BUF_LEN];
	void (*handler)();
} cli_entry_t;


// function prototypes...
void dummyFunction(int sign);
void parseACLICmd(char *str);
void CLIProcessCmds(FILE *fp, int online);
void CLIPrintHelpPreamble();
void *CLIProcessCmdsInteractive(void *arg);
void registerCLI(char *key, void (*handler)(),
		 char *shelp, char *usage, char *lhelp);
void CLIDestroy();
int CLIInit(router_config *rarg);

void helpCmd();
void versionCmd();
void setCmd();
void getCmd();
void sourceCmd();
void ifconfigCmd();
void routeCmd();
void arpCmd();
void pingCmd();
void consoleCmd();
void haltCmd();
void queueCmd();
void qdiscCmd();
void spolicyCmd();
void classCmd();
void filterCmd();
void openflowCmd();
void gncCmd();
void gncTerminate();

void printRouteTable(route_entry_t *route_tbl);
void setTimeMode(int mode);
int getTimeMode(void);
void setUpdateInterval(int interval);
int getUpdateInterval(void);
void consoleGetState(void);
void consoleRestart(char *config_dir, char *router_name);

void openflow_config_print_desc_stats(void);
void openflow_config_print_port_stats(void);
void openflow_config_print_port_stat(uint32_t num);
void openflow_config_print_ports(void);
void openflow_config_print_port(uint32_t num);
void openflow_ctrl_iface_reconnect(void);

#endif

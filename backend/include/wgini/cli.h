//=======================================================
//       File Name :  cli.h
//=======================================================
 #ifndef __WGINI_CLI_H_
 #define __WGINI_CLI_H_

 #include <stdio.h>
 #include <sys/socket.h>
 #include <arpa/inet.h>
 #include <stdlib.h>
 #include <string.h>
 #include <unistd.h>
 #include <math.h>
 #include <pthread.h>
 #include <slack/err.h>
 #include <slack/std.h>
 #include <slack/prog.h>
 #include <slack/map.h>
 #include <signal.h>
 #include <errno.h>

 #include "gwcenter.h"
 #include "mobility.h"
 #include "mathlib.h"
 #include "antenna.h"
 #include "energy.h"
 #include "channel.h"
 #include "propagation.h"
 #include "nomac.h"
 #include "mac.h"
 #include "802_11_frame.h"
 #include "timer.h"
 #include "wirelessphy.h"
 #include "llc.h"
 #include "vpl.h"
 #include "event.h"
 #include "stats.h"
 #include "errmodel.h"
 #include "awgn.h"
 #include "fading.h"
 #include "wcard.h"

#define USAGE_HELP           "\thelp <command>!\n"
#define USAGE_STATS         "stats action [action specific options]!\n"
#define USAGE_CH                "\tch action [action specific options]!\n"
#define USAGE_MAC             "\tmac action [action specific options]!\n"
#define USAGE_ANT              "\tant action [action specific options]!\n"
#define USAGE_WCARD       "wcard action [action specific options]!\n"
#define USAGE_MOV              "\tmov action [action specific options]!\n"
#define USAGE_ENERGY       "energy action [action specific options]!\n"
#define USAGE_SYS               "\tsys action [action specific options]!\n"
#define USAGE_EXIT              "\texit!\n"
#define USAGE_HALT             "\thalt!\n"
#define USAGE_ABOUT          "about!\n"

#define SHELP_HELP           "\tdisplay help information on given command!\n"
#define SHELP_STATS         "\toperations for statistical data analysis purpose!\n"
#define SHELP_CH                "\tset, modify and show channel attributions!\n"
#define SHELP_MAC             "\tset, modify and show mac layer attributions!\n"
#define SHELP_ANT              "\tset, modify and show antenna attributions!\n"
#define SHELP_WCARD       "\tset, modify and show wireless network interface card attributions!\n"
#define SHELP_MOV              "\tset, modify and show mobility attributions!\n"
#define SHELP_ENERGY       "\tset, modify and show energy attributions!\n"
#define SHELP_SYS               "\tset, modify and show genernal system information!\n"
#define SHELP_EXIT              "\texit the command shell!\n"
#define SHELP_HALT             "\thalt the wireless GINI center!\n"
#define SHELP_ABOUT          "\tdisplay the software information!\n"


#define HELP_PREAMPLE        "These shell commands are defined internally.  Type 'help' to see this\n\
list. Type 'help name' to find out more about the function 'name'.\n\
Because the wireless GINI shell uses the underlying Linux shell to run \n\
commands which are not recognized by it but are Linux commands can \n\
be accessed from this shell. \n"

#define LHELP_HELP            "\t'help' is used to display helpful information about builtin commands.  If <command> is\n\
\tspecified, gives detailed help on all commands matching <command>,\n\
\totherwise a list of the builtins is printed.\n"

#define LHELP_STATS         "\t'stats' is used to provide the statistical experimental data for analysis!\n\
           \n\
		   \n\
\tCOMMAND LIST: \n\
           \n\
\tstats report node <id> <P> > <output-file-path>\n\
           \n\
\t\tThe above command is used to create a file that provides the statistical analysis report\n\
\t\tfor a mobile node '>' is the redirection symbol followed by the path to which you want t\n\
\t\t-o store the output file. The <output-file-path> actually is the concatenation of the d-\n\
\t\t-estination directory and the file name. If it is not specified by the user, the report\n\
\t\twill be directed to the default directory.\n\
\t\te.g. stats report node 1 P > ./wgn/output/mov.txt.\n\
\t\t--P: packet report.\n\
           \n\
\tstats show node <id> interface\n\
           \n\
\t\tThe above command is used to display the detailed interface information for the specifi-\n\
\t\t-ed mobile node.\n\
           \n\
\tstats trace {ch|eve} on > <output-file-path>\n\
           \n\
\t\tThis above command is used to start the tracing procedure for different kinds of system\n\
\t\tinformation and record it in a default file or the one specified by the user. The <outp-\n\
\t\t-ut-file-path> actually is the concatenation of the destination directory and the file\n\
\t\tname.\n\
\t\t--ch: The channel fading information will be recorded.\n\
\t\t--eve: The system event information will be recorded.\n\
\t\te.g. stats report node 1 P > ./wgn/output/mov.txt.\n\
\t\tThe tracing method is normally used for debugging purpose. It can sometimes affect the\n\
\t\tefficiency of the system. Therefore it is recommendable to stop it after testing.\n\
           \n\
\tstats trace {ch|eve} off\n\
           \n\
\t\tThis above command is used to stop the corresponding tracing procedure.\n\
           \n\
\tstats trace th <on|off>\n\
           \n\
\t\tThe above command is used to This above command is used to start or terminate the syste-\n\
\t\t-m throughput calculation process. Currently the system time used by the wireless GINI\n\
\t\tcenter is different from the Machs. Therefore, the transmission rate calculated by the U-\n\
\t\t-ML is totally based on a different reference point. The command provide the capacity to\n\
\t\tcompute the throughput based on the local time managed by the wireless GINI center its-\n\
\t\t-elf.\n\
		   \n\
\tstats trace node <id> mov on > <output-file-path>\n\
           \n\
\t\tThe above command is used to start tracing the movement of the specified mobile node and\n\
\t\trecord it in a default file or the one specified by the user. <output-file-path> is the\n\
\t\tcontatenation of the directory and the file name. \n\
\t\te.g. stats trace node 1 mov on > ./wgn/output/mov.txt.\n\
           \n\
\tstats trace node <id> mov off\n\
           \n\
\t\tThe above command is used to stop tracing the movement of the specified mobile node.\n\
           \n\
\tstats clean node <id>\n\
           \n\
\t\tReset the statistical recorder for the specified node. It is strongly recommendable to do\n\
\t\tit after each individual experiment to guarantee the statistical data isolation. When the\n\
\t\t<id> is set to 0, the operated objects will be all mobile nodes.\n"


#define LHELP_CH                "\t'ch' is used to manage the shared channel of the WLAN.\n\
           \n\
		   \n\
\tCOMMAND LIST: \n\
           \n\
\tch set fad switch <on|off>\n\
           \n\
\t\tThe above command is used to turn on/off the Rayleigh fading affect of the channel.\n\
           \n\
\tch set fad oscnum <oscillator-number>\n\
           \n\
\t\tThis command is used to set the total number of the oscillators that used to simulate mu-\n\
\t\tltiple propagation paths. The number of the simulated paths equals to the number of osci-\n\
\t\tllators multiplied by four and then added by two. It can only be executed correctly when\n\
\t\tthe fading switch is turned on.\n\
           \n\
\tch set awgn switch <on|off>\n\
           \n\
\t\tThe above command is used to turn on/off the AWGN noise affect of the channel.\n\
           \n\
\tch set awgn mode <pwr|snr>\n\
           \n\
\t\tThis is used to set the mode for adding AWGN noise into the channel.\n\
\t\t--snr: add the noise leading to the specified SNR.\n\
\t\t--pwr: add the noise with a specified value in dB.\n\
           \n\
\tch set awgn para {noise|ratio} <para-value>\n\
           \n\
\t\tThis command is used to set the values for the awgn-related parameters. It can only be e-\n\
\t\txecuted correctly when the AWGN switch is on.\n\
\t\t--noise: the standard deviation of the AWGN noise added into the channel (dB) (applicable\n\
\t\t         only in 'pwr' mode).\n\
\t\t--ratio: keeps the fixed signal-to-noise ratio per bit while adding noise into the channel\n\
\t\t         (applicable only  in 'snr' mode).\n\
		   \n\
\tch set prop mode <F|T|S>\n\
           \n\
\t\tThis command is used to set the propagation mode for the channel.\n\
\t\t--F: Free Space mode.\n\
\t\t--T: Two-Ray Ground Reflection mode.\n\
\t\t--S: Shadowing mode.\n\
           \n\
\tch set prop para {exp|std|refdist} <para-value>\n\
           \n\
\t\tThis command is used to set the values of propagation-related parameters.\n\
\t\t--exp: channel path-loss exponential parameter (applicable only in shadowing mode).\n\
\t\t--std: channel standard deviation parameter (db) (applicable only in shadowing mode).\n\
\t\t--refdist: reference distance(m) (applicable only in shadowing mode).\n\
		   \n\
\tch show info\n\
           \n\
\t\tThis command is used to display the channel information, including the propagation infor-\n\
\t\tmation, fading information, AWGN information and the node inter-connection state informa-\n\
\t\ttion.\n"

#define LHELP_MAC              "\t'mac' is used to manage the mac layer of the virtual devices in the WLAN.\n\
           \n\
		   \n\
\tCOMMAND LIST: \n\
           \n\
\tPlease note that because the current version wireless GINI all mobile devices in the same WLAN h-\n\
\tave the identical RTS threshold value and run in the same mode. Therefore, the execution of the \n\
\tcommands, 'mac set node <id> mode <N/C/D11>' and 'mac set node <id> rtsthres <integer-value>', on \n\
\ta specified node will actually affect all other mobile nodes automatically as the same time to make \n\
\tthem uniform. The node <id> field in the command is only used to provide the interface for future\n\
\tdevelopment. To set the variable values of the mac layer, you can randomly input any valid id as\n\
\tthe value of the <id> field.\n\
           \n\
           \n\
\tmac set node <id> mode <N/C/D11>\n\
           \n\
\t\tThis command is used to set the medium access control mode.\n\
\t\t--N: NONE mode, without any medium access control mechanism.\n\
\t\t--C: P-PERSISTENT CSMA mode, p-persistent CSMA medium access control mechanism is used.\n\
\t\t--D11: DCF_802_11 mode, Distributed Coordinate Function specified by the standard 802.11\n\
\t\t       is used.\n\
           \n\
\tmac set node <id> rtsthres <value>\n\
           \n\
\t\tThis command is used to set the RTS threshold (only applicable in DCF_802_11 mode). The \n\
\t\tWLAN can work in a pure RTS-CTS mode while the RTS threshold is set to be 0 or in pure  \n\
\t\tNO-RTS-CTS mode by setting the RTS threshold to be big enough (e.g. 10000).\n\
           \n\
\tmac set node <id> txprob <tx-probability>\n\
           \n\
\t\tThis command is used to set the transmission probability (only applicable in CSMA mode).\n\
           \n\
\tmac set node <id> addr <mac-address>\n\
           \n\
\t\tThis command is used to set the mac address for a specified one-interface virtual devi-\n\
\t\tce. The <mac-address> should follow the format of @:@:@:@:@:@ (e.g.FE:FD:00:00:00:01). \n\
\t\tIt should matche the one that is set in the corresponding Mach machine.\n\
           \n\
\tmac show node <id> info\n\
           \n\
\t\tThis command is used to display the MAC layer information of the specified mobile node.\n"


#define LHELP_ANT              "\t'ant' is used to manage the antenna of the virtual devices in the WLAN.\n\
           \n\
		   \n\
\tCOMMAND LIST: \n\
           \n\
\tant set node <id> height <value>\n\
           \n\
\t\tThis command is used to set the height of the antenna for the specified node. It can be\n\
\t\tset in any circumstances but only affect the performance only if the current propagation\n\
\t\tmode is 'T' (two-ray ground reflection mode).\n\
           \n\
\tant set node <id> gain <value>\n\
           \n\
\t\tThis command is used to set the system gain of the antenna. This command is basically u-\n\
\t\tsed for software development purpose. It is not recommendable to be used by users.\n\
           \n\
\tant set node <id> sysloss <value>\n\
           \n\
\t\tThis command is used to set the system loss of the antenna. This command is basically u-\n\
\t\tsed for software development purpose. It is not recommendable to be used by users.\n\
           \n\
\tant set node <id> jam <on|off|int-value>\n\
           \n\
\t\tThis command is used to add or remove the artificial interference to or from the specif-\n\
\t\tied mobile node. It can only be used when mac layer works in DCF 802.11 mode.\n\
\t\t--on: add interference with default power value (carrier sense threshold) to on the spe-\n\
\t\t      cified mobile node.\n\
\t\t--off: remove all the artificial interference from the specified mobile node.\n\
\t\t--int-value: add interference on the specified mobile node with self-defined value in dB.\n\
		   \n\
\tant show node <id> info\n\
           \n\
\t\tThis command is used to display the antenna information of the specified mobile node.\n"


#define LHELP_WCARD       "\t'wcard' is used to manage the wireless NIC of the virtual devices in the WLAN.\n\
           \n\
		   \n\
\tCOMMAND LIST: \n\
           \n\
\tPlease note that because the current version wireless GINI can only support identical wireless n-\n\
\tetwork interface card in the same WLAN. Therefore, any operation to a specified node will be exe-\n\
\tcuted on all other mobile nodes automatically as well to make them uniform. The node <id> field i\n\
\tn the command is only used to provide the interface for future development. To set the values of\n\
\tthe wireless card parameters, you can randomly input any valid id as the value of the <id> field.\n\
\tThe commands below followed by a (*) sign is not recommanded to use.\n\
           \n\
\twcard set node <id> freq <double-value>\n\
           \n\
\t\tThis command is used to set the frequency of the wireless card in MHz.\n\
\t\te.g. 'wcard set node 1 freq 900' means set the frequency to be 900MHz.\n\
           \n\
\twcard set node <id> bandwidth <double-value>\n\
           \n\
\t\tThis command is used to set the bandwidth of the wireless card in Mbps.\n\
\t\te.g. 'wcard set node 1 bandwidth 2' means set the bandwidth to be 2Mbps.\n\
           \n\
\twcard set node <id> pt <double-value> (*)\n\
           \n\
\t\tThis command is used to set the transmission power of mobile node in W.\n\
           \n\
\twcard set node <id> ptx <double-value> (*)\n\
           \n\
\t\tThis command is used to set the power consuming while the node is in 'tx' state in W.\n\
           \n\
\twcard set node <id> prx <double-value> (*)\n\
           \n\
\t\tThis command is used to set the power consuming while the node is in 'rx' state in W.\n\
           \n\
\twcard set node <id> pidle <double-value> (*)\n\
           \n\
\t\tThis command is used to set the power consuming while the node is in 'idle' state in W.\n\
           \n\
\twcard set node <id> psleep <double-value> (*)\n\
           \n\
\t\tThis command is used to set the power consuming while the node is in 'sleep' state in W.\n\
           \n\
\twcard set node <id> poff <double-value> (*)\n\
           \n\
\t\tThis command is used to set the power consuming while the node is in 'off' state in W.\n\
           \n\
\twcard set node <id> csthreshold <double-value> \n\
           \n\
\t\tThis command is used to set the carrier sense threshold of the wireless card in 10^(-8)W.\n\
\t\te.g. 'wcard set node 1 csthreshold 0.2' means set the cs threshold to be 0.2*10^(-8)W.\n\
           \n\
\twcard set node <id> rxthreshold <double-value> (*)\n\
           \n\
\t\tThis command is used to set the receiving threshold of the wireless card in 10^(-8)W.\n\
\t\te.g. 'wcard set node 1 rxthreshold 0.2' means set the rx threshold to be 0.2*10^(-8)W.\n\
           \n\
\twcard set node <id> cpthreshold <double-value>\n\
           \n\
\t\tThis command is used to set the capture effect threshold of the wireless card in dB.\n\
           \n\
\twcard set node <id> cap <on/off>\n\
           \n\
\t\tThis command is used to set the capture effect option for the specified mobile node.\n\
           \n\
\twcard set node <id> modtype <D|F>\n\
           \n\
\t\tThis command is used to set the emission type of the wireless card.\n\
\t\t--D: Direct Sequence Spread Spectrum mode (DSSS).\n\
\t\t--F: Frequency Hopping Spread Spectrum mode (FHSS) (not implemented at this moment).\n\
           \n\
\twcard show node <id> info\n\
           \n\
\t\tThis command is used to display the wireless NIC information of the specified mobile node.\n"


#define LHELP_MOV              "\t'mac' is used to manage the movement of the virtual devices in the WLAN.\n\
           \n\
		   \n\
\tCOMMAND LIST: \n\
           \n\
\tmov set node <id> switch <on|off>\n\
           \n\
\t\tThis command is used to turn on/off the movement of the specified mobile node. Once the m-\n\
\t\tobility switch is set to off, the node will keep stationary at the current location.\n\
           \n\
\tmov set node <id> location <x> <y> <z>\n\
           \n\
\t\tThis command is used to set the new location for the specified node. It can only be execu-\n\
\t\tted correctly when the mobility switch of operated mobile node is off.\n\
           \n\
\tmov set node <id> mode <R|T|M>\n\
           \n\
\t\tThis command is used to set the mobility mode for the specified mobile node.\n\
\t\t--R: RandomWayPoint mode. \n\
\t\t--T:  TrajectoryBased mode (not available at this moment). \n\
\t\t--M: Manual mode (not available at this moment). \n\
           \n\
\tmov set node <id> spd {max|min} <value>\n\
           \n\
\t\tThis command is used to set the maximum or minimum speed for the specified mobile node.\n\
\t\tWe can set the minimum and maximum speed of the mobile node to the same value to get a fi-\n\
\t\txed speed.\n\
           \n\
\tmov show node <id> info\n\
           \n\
\t\tThis command is used to display the mobility information of the specified mobile node.\n"


#define LHELP_ENERGY       "\t'energy' is used to manage the energy storage of the virtual devices in the WLAN.\n\
           \n\
		   \n\
\tCOMMAND LIST: \n\
           \n\
\tPlease note that because the current version wireless GINI can not support energy consuming cal-\n\
\tculationn for the mobile nodes, therefore the 'set' group commands shown in this list only pro-\n\
\tvide the inteface for the future development and have no impact on the running program.\n\
		   \n\
\tenergy set node <id> switch <on|off>\n\
           \n\
\t\tThis command is used to set the power switch of the specified mobile node.\n\
           \n\
\tenergy show node <id> psm <on|off>\n\
           \n\
\t\tThis command is used to turn on/off the power saving mode of the specified mobile node.\n\
           \n\
\tenergy show node <id> totenergy <double-value>\n\
           \n\
\t\tThis command is used to set the total energy of the specified mobile node in Joule.\n\
           \n\
\tenergy show node <id> info\n\
           \n\
\t\tThis command is used to display the energy information of the specified mobile node.\n"



#define LHELP_SYS               "\t'sys' is used to manage the system configurations.\n\
           \n\
		   \n\
\tCOMMAND LIST: \n\
           \n\
\tsys set map size <x> <y> <z>\n\
           \n\
\t\tThe above command is used to set the three-dimensional map size in meter. <x>, <y>, and <z> \n\
\t\tare the maximum coordinate values for three axles, respectively. We can simply set hzi to 0 \n\
\t\tto obtain a two-dimensional map.\n\
           \n\
\tsys set verbose <level>\n\
           \n\
\t\tThe above command is used to set the verbose level of the running program.\n\
           \n\
\tsys show map size\n\
           \n\
\t\tThe above command is used to display the map size.\n\
           \n\
\tsys show verbose\n\
           \n\
\t\tThe above command is used to display the verbose level.\n\
           \n\
\tsys show dev\n\
           \n\
\t\tThe above command is used to display the information of all virtual devices in the WLAN.\n\
           \n\
\tsys show time\n\
           \n\
\t\tThe above command is used to display the current system time.\n\
           \n\
\tsys show timer\n\
           \n\
\t\tThe above command is used to display the timer information of virtual devices in the WLAN.\n\
           \n\
\tsys show event \n\
           \n\
\t\tThe above command is used to display the current system event list.\n\
           \n\
\tsys src <config-file-path>\n\
           \n\
\t\tThe above command is used to read a batch file and execute it. This is useful to run ini-\n\
\t\t-tial setup commands without typing them one by one in the command shell. It will only s-\n\
\t\t-imply execute all instructions in the configuration file without the initialization of t \n\
\t\t-he system. <config-file-path> is the contatenation of the directory and the file name.\n\
\t\te.g. sys src ./wgn/config/config.txt.\n\
           \n\
\tsys reload <config-file-path>\n\
           \n\
\t\tThe above command will execute the commands in the file after the initialization of the w\n\
\t\t-hole system. <config-file-path> is the contatenation of the directory and the file name.\n\
\t\te.g. sys src ./wgn/config/config.txt. \n"


#define LHELP_EXIT              "\tExit the command shell without halting the  wireless GINI center. The center\n\
\tshould go into the deamon once the CLI is exitted.\n"

#define LHELP_HALT              "\tHalts the wireless GINI center.\n"

#define LHELP_ABOUT           "\tDisplay the software information!\n"

#define PROGRAM                       10
#define JOIN                          11
#define COMMENT                       12
#define COMMENT_CHAR                  '#'
#define CONTINUE_CHAR                 '\\'
#define CARRIAGE_RETURN               '\r'
#define LINE_FEED                     '\n'

#define MAX_BUF_LEN                   4096
#define MAX_LINE_LEN                  256
#define MAX_KEY_LEN                   64
#define MAX_TMPBUF_LEN    256


typedef struct _cli_entry_t
{
	char keystr[MAX_KEY_LEN];
	char long_helpstr[MAX_BUF_LEN];
	char short_helpstr[MAX_BUF_LEN];
	char usagestr[MAX_BUF_LEN];
	void (*handler)();
} cli_entry_t;


void IniCli();
void cli_handler();
void cli_input_read();
void cli_file_read(char* pchFile);
void registerCLI(char *key, void (*handler)(), char *shelp, char *usage, char *lhelp);
void parseCmd(char *str);
void printCLIHelp();
void printCLIShellPreamble();
void printCLIAbout();
char *rlGets (char *prompt);
void processCLI(FILE *fp);

void helpCmd();
void haltCmd();
void exitCmd();
void chCmd();
void phyCmd();
void macCmd();
void antCmd();
void movCmd();
void wcardCmd();
void energyCmd();
void sysCmd();
void statsCmd();
void aboutCmd();

#endif

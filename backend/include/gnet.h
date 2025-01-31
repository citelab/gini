/*
 * gnet.h (header files for GNET)
 */


#ifndef __GNET_H__
#define __GNET_H__


// all includes go here!
#include "grouter.h"
#include "vpl.h"
#include "device.h"
#include "message.h"
#include "simplequeue.h"
#include <pthread.h>



#define	MAX_INTERFACES					1024      // max number of interfaces supported


#define INTERFACE_DOWN                  'D'     // the down state of the interface
#define INTERFACE_UP                    'U'     // the up state of the interface
#define IFACE_CLIENT_MODE               'C'     // client mode interface
#define IFACE_SERVER_MODE               'S'     // server mode interface

#define ETH_DEV							2
#define TAP_DEV							3

/*
 * NOTE: The interface will be created in down state if the gnet_adapter could
 * not connect to the socket. Client mode, the user needs to reconnect. In server
 * mode, the server socket will automatically update the state once the remote
 * node initiates the connection.
 */
typedef struct _interface_t
{
	int interface_id;					// interface identifier
	int state;                          // active OR inactive
	int mode;                       	// client OR server mode
	char device_type[MAX_DNAME_LEN];	// device type specification
	char device_name[MAX_DNAME_LEN];	// full device name (e.g., eth0, wlan1)
	char sock_name[MAX_DNAME_LEN];
	uchar mac_addr[6];		        	// 6 for Ethernet MACs
	uchar ip_addr[4];		        	// 4 for Internet protocol as network address
	int device_mtu;						// maximum transfer unit for the device
	int iface_fd;						// file descriptor for ??
	vpl_data_t *vpl_data;				// vpl library structure
	pthread_t threadid;					// thread ID assigned to this interface
	pthread_t sdwthread;
	device_t *devdriver;				// the device driver that include toXDev and fromXDev functions
	void *iarray;                       // pointer to interface array type
} interface_t;



typedef struct _interface_array_t
{
	int count;
	interface_t *elem[MAX_INTERFACES];
} interface_array_t;


typedef struct _vplinfo_t
{
	vpl_data_t *vdata;
	interface_t *iface;
} vplinfo_t;




// function prototype go here...
interface_t *GNETMakeEthInterface(char *vsock_name, char *device,
			   uchar *mac_addr, uchar *nw_addr, int iface_mtu, int cforce);
interface_t *GNETMakeTapInterface(char *device, uchar *mac_addr, uchar *nw_addr);
interface_t *GNETMakeTunInterface(char *device, uchar *mac_addr, uchar *nw_addr,
                                  uchar* dst_ip, short int dst_port);
interface_t *GNETMakeRawInterface(char *device, uchar *nw_addr, char *bridge);

device_t *findDeviceDriver(char *dev_type);
interface_t *findInterface(int indx);
void *delayedServerCall(void *arg);
void *GNETHandler(void *outq);
void GNETInit(pthread_t *handler, char *config_dir, char *router_name, simplequeue_t *outputQ);
void GNETHalt(pthread_t gnethandler);
int destroyInterfaceByIndex(int indx);

void GNETInsertInterface(interface_t *iface);
int changeInterfaceMTU(int index, int new_mtu);
void printInterfaces(int mode);

int upInterface(int index);
int downInterface(int index);

#endif //__GNET_H__

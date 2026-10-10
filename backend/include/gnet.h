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
	// Traffic counters for `ifstat` (the Router Lab's per-interface bandwidth meter). Appended,
	// never inserted, so no initializer elsewhere shifts. Bumped only through gnet_count_rx/tx
	// below. Cumulative and never reset: a reader takes deltas between two polls.
	unsigned long long rx_bytes, rx_pkts;
	unsigned long long tx_bytes, tx_pkts;
	// The receive thread is started once, by the first `up`, and runs until the interface is
	// destroyed. `down` no longer cancels it (see gnet_rx_discard); this records that it exists,
	// so a second `up` does not start a second reader -- which it used to, after which `down`
	// stopped only one of the two and reception carried on.
	int rx_running;
	// Subnet mask, in the router's (reversed, Dot2IP) byte order like ip_addr. The router kept no
	// mask at all, so everything that needed one -- a control-plane script's broadcast, the
	// local- and directed-broadcast checks -- assumed /24. `ifconfig add ... -netmask M` sets it;
	// absent, it defaults to 255.255.255.0, which is what every GINI subnet has been.
	uchar netmask[4];
	// Routing cost of this interface, 1-15 (docs/design/link-properties.md): an ABSTRACT number,
	// unrelated to delay or bandwidth. The router itself forwards by its table and does not read
	// it; a routing protocol does (Lua interfaces()[i].cost), and gBuilder's static routes are
	// computed from the same link costs. Default 1, which is hop count.
	int metric;
	// The connected route `down` withdrew, so `up` restores exactly that and nothing else: a real
	// router withdraws an interface's subnet when it loses carrier, and a failure is then visible
	// in `route show`. None is withdrawn in OpenFlow mode (no connected routes), and one a routing
	// protocol has since taken over (origin D) is not ours to touch.
	int conn_withdrawn;
	uchar conn_net[4], conn_mask[4];
} interface_t;

/* Count one frame in or out. Called from EVERY device driver (ethernet, tun, tap, raw) at the
 * point it actually reads or writes the wire: GINI's fabric interfaces are `tun`, which has its
 * own driver, so counting in one driver would have left the meter at zero for the interfaces
 * students actually use (it did, on the first try). Atomic both ways -- cheap, and it leaves no
 * thread assumption to get wrong. */
static inline void gnet_count_rx(interface_t *f, int n)
{
	if (f && n > 0) { __sync_fetch_and_add(&f->rx_bytes, (unsigned long long)n);
	                  __sync_fetch_and_add(&f->rx_pkts, 1ULL); }
}
static inline void gnet_count_tx(interface_t *f, int n)
{
	if (f && n > 0) { __sync_fetch_and_add(&f->tx_bytes, (unsigned long long)n);
	                  __sync_fetch_and_add(&f->tx_pkts, 1ULL); }
}

/* Called by EVERY driver straight after it reads a frame, before counting it: a down interface
 * receives nothing, so the frame is freed and the caller goes round again (returns 1).
 *
 * `down` used to pthread_cancel the receive thread instead. That left the socket undrained, so
 * everything the peer sent while the link was down sat in the kernel buffer and arrived as one
 * stale burst on `up` -- and the cancel was ASYNCHRONOUS, so it could land inside malloc. Keeping
 * the reader alive and discarding is what a down NIC actually does. */
static inline int gnet_rx_discard(interface_t *f, void *pkt)
{
	if (f && f->state == INTERFACE_DOWN) { free(pkt); return 1; }
	return 0;
}



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
                                  uchar* dst_ip, short int dst_port, int src_port);
interface_t *GNETMakeRawInterface(char *device, uchar *nw_addr, char *bridge);

device_t *findDeviceDriver(char *dev_type);
interface_t *findInterface(int indx);
void *delayedServerCall(void *arg);
void *GNETHandler(void *outq);
void GNETHalt(int gnethandler);
int destroyInterfaceByIndex(int indx);

void GNETInsertInterface(interface_t *iface);
int changeInterfaceMTU(int index, int new_mtu);
void printInterfaces(int mode);

int upInterface(int index);
int downInterface(int index);

#endif //__GNET_H__

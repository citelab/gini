//=======================================================
//       File Name :  gwcenter
//=======================================================
 #ifndef __GWCENTER_H_
 #define __GWCENTER_H_

 #include "vpl.h"

//general parameters
#define MIG 1000000
#define PI 3.1415926
#define WAVESPD 300000000
#define WGNnode(id) ((MobileNode *)(MobNodPtrAarry+(id)-1))
#define WGNAP(id) ((MobileNode *)(APPtrAarry+(id)-1))
#define MaxNodeNo 63
#define MaxAPNo 10
#define BROADCASTID 1000
#define NFACTOR 100000000
#define IFACTOR 1000
#define DEFAULT_MTU  1500
#define MAX_SEQ_NO 4095

//mac layer frame parameters
#define DOT_3_HDR_LEN	14

#define  MAC_802_11_DATA_FRAME_HEADER_LENGHT 30
#define  MAC_802_11_RTS_FRAME_HEADER_LENGHT  16
#define  MAC_802_11_CTS_FRAME_HEADER_LENGHT  10
#define  MAC_802_11_ACK_FRAME_HEADER_LENGHT  10
#define  MAC_802_11_FRAME_TAILER_LENGHT   4

#define MAC_PROTOCOL_VERSION 0x00

#define MAC_TYPE_MANAGEMENT 0x00
#define MAC_TYPE_CONTROL 0x01
#define MAC_TYPE_DATA 0x02
#define MAC_TYPE_RESERVED 0x03

#define MAC_SUBTYPE_ATIM 0x09
#define MAC_SUBTYPE_BEACON 0x0A
#define MAC_SUBTYPE_RTS 0x0B
#define MAC_SUBTYPE_CTS 0x0C
#define MAC_SUBTYPE_ACK 0x0D
#define MAC_SUBTYPE_DATA 0x00

//logical link control layer parameters
#define LLC_TO_MAC_BUF_SIZE  500
#define LLC_TO_MACH_BUF_SIZE  500
#define RCVBUFSIZE 2000

// mac layer parameters
#define RETX_MAX_NUM 11
#define TIMEOUT_TIME 0.15
#define MAC_DUP_CHK_BUF_SIZE 1
#define MAC_ADDR_LEN 6

//physical layer parameters
 #define DSSS_CWMin 31
 #define DSSS_CWMax 1023
 #define DSSS_SlotTime 0.000020
 #define DSSS_CCATime 0.000015
 #define DSSS_RxTxTurnaroundTime 0.000005
 #define DSSS_SIFSTime	0.000010
 #define DSSS_PreambleLength 144	     //bits	 NOT	 byte
 #define DSSS_PLCPHeaderLength 48	 //bits  NOT byte

//statistical parameters
#define MAX_REPORT_NAME_LEN  100
#define MAX_REPORT_PATH_LEN  100
#define STATS_REPORT_DIRECTORY  "var/gwcenter/"

//=======================================================
//       General stuff
//=======================================================
  typedef enum {
    FALSE,
    TRUE
  } WGNflag;

  typedef enum {
    ON,
    OFF
  } Switch;

//=======================================================
//       Time stuff
//=======================================================
  typedef struct {
     unsigned int tv_sec;
	 unsigned int tv_nsec;
  }  WGNTime;

typedef enum {
    BF_TIMER,
    DF_TIMER,
    NAV_TIMER,
	IF_TIMER,
	TX_TIMER,
	RX_TIMER
}   TimerType;

typedef struct {
	Switch switcher;
	WGNflag pause;
    WGNTime timeout_time;                                   //the time timeout happens
	WGNTime start_time;
	unsigned int remain_time;                                          //nanoseconds
	unsigned int wait_time;                                              //nanoseconds         // the waiting time before the backoff process
}   BackoffTimer;

typedef struct {
	Switch switcher;
	WGNTime start_time;
	WGNTime end_time;
}   NormalTimer;

typedef struct {
    BackoffTimer *bf_timer;
    NormalTimer *df_timer;
	NormalTimer *nav_timer;
	NormalTimer *if_timer;
	NormalTimer *tx_timer;
    NormalTimer *rx_timer;
}   NodeTimer;

typedef struct {
    double Mob_timeunit;
	double Ch_timeunit;
} TimeMib;

WGNTime SYSTEM_TIME;

//=======================================================
//       Map stuff
//=======================================================
  typedef struct {
    double x_width;
	double y_width;
	double z_width;
  }   MapMib;

//=======================================================
//        Event stuff
//=======================================================
  typedef enum {
    // mac layer
	BFTimeOut,
	DFTimeOut,
	NAVTimeOut,
	IFTimeOut,
	TXTimeOut,
    RXTimeOut,
	// phy layer
	INTTimeOut,
	// channel
	START_RX
  } Event_type;

  typedef struct WGNEvent {
	 int id;
	 Event_type eveType;
	 WGNTime eveTime;
	 void* eveObject;
	 struct WGNEvent* nextEventPtr;
  }  WGNEvent;

  WGNEvent* WGNEveQPtr;
  WGNTime timeOutTime;        //the execution time of the earliest timeout event
  pthread_mutex_t event_queue_mutex;			/* for several nodes write the event queue at the same time*/

//=======================================================
//       Statistical stuff
//=======================================================
  WGNflag ThroutputComSign;
  WGNflag PrintChFadSign;
  WGNflag PrintEventSign;
  WGNflag PrintMovArray[MaxNodeNo];
  int FileSeqArray[MaxNodeNo];
  int MobSeqArray[MaxNodeNo];
  char REPORT_PATH_PREFIX[MAX_REPORT_PATH_LEN];

  typedef struct {
	unsigned long int BroadcastTx;
	unsigned long int UnicastTx;
	unsigned long int RtsTxed;
	unsigned long int CtsTxed;
	unsigned long int DataTxed;
	unsigned long int AckTxed;
	unsigned long int TotPktTxed;
	//retransmission stuff
	unsigned long int ReTxPktNum;
	unsigned long int ReTxRtsNum;
	unsigned long int ReTxNum;
	//recording error stuff
	unsigned long int DropForExMaxRtsRetryNo;		//rts pkts
	unsigned long int DropForExMaxPktRetryNo;		//data pkts
	unsigned long int DropForExMaxRetryNo;
	unsigned long int DropForInTx;                            //recv packet when the node is sending
    //about collision
	unsigned long int RtsDropForCollision;
    unsigned long int CtsDropForCollision;
	unsigned long int AckDropForCollision;
    unsigned long int DataDropForCollision;
	unsigned long int DropForCollision;
	unsigned long int DropForLowRxPwr;
	unsigned long int DropForNoiseErr;
	unsigned long int TotPktErrorDrop;
  	unsigned long int AptCaptureNo;                         //accept for the capture
	//recording the pkt accepted correctly
	unsigned long int TotPktRxed;		                              //including the correct ones and not
	unsigned long int PktSucRxed;							   //the packets received correctly
	unsigned long int NotForMeRxed;
	unsigned long int RtsRxed;
	unsigned long int CtsRxed;
	unsigned long int DataRxed;
	unsigned long int AckRxed;
	//rate stuff
	double RtsReTranRate;
	double DataReTranRate;
    double ReTranRate;
	double RecvColErrorDropRate;
	double RecvWhileTxErrorDropRate;
	double RecvNoiseErrorDropRate;
	double RecvLowPwrErrorDropRate;
	double RecvErrorDropRate;
	double RecvDropRate;     //including the packets which are not for me
	double RecvColRate;
  	//for special purpose
	double TotTxByteNo;
	WGNTime ThCompStartTime;
	//for buffer
	unsigned long int LToMBufDrop;
	unsigned long int LToUBufDrop;
  } NodeStats;


//=======================================================
//             Frame stuff
//=======================================================
typedef struct {
   unsigned char dst[6];
   unsigned char src[6];
   unsigned char prot[2];
}  WGN_802_3_Mac_Hdr;

typedef struct {
  int payload_len;                   //header+data
  WGN_802_3_Mac_Hdr header;
  unsigned char *data;
} WGN_802_3_Mac_Frame;

typedef struct {
	int     fc_protocol_version;
	int     fc_type;
	int     fc_subtype;
	int     fc_to_ds;
	int     fc_from_ds;
	int     fc_more_frag;
	int     fc_retry;
	int     fc_pwr_mgt;
	int     fc_more_data;
	int     fc_wep;
	int     fc_order;
}	WGN_802_11_Mac_Pseudo_Frame_Control;

typedef struct {
	unsigned int     sc_fragment_number;
	unsigned int     sc_sequence;
}	WGN_802_11_Mac_Pseudo_Sequence_Control;

typedef enum {
    BIT,
    BYTE
}   Pkt_Unit;

typedef struct {
	// for mac layer, info in the dot 11 mac header
	WGN_802_11_Mac_Pseudo_Frame_Control fc;
	WGN_802_11_Mac_Pseudo_Sequence_Control sc;
	unsigned int duration;
	unsigned char SA[6];
	unsigned char DA[6];
	unsigned char TA[6];
	unsigned char RA[6];
	unsigned char BSSID[6];
	unsigned int fcs;
	// for mac layer, info not in the dot 11 mac header
	unsigned int srcid;
	unsigned int dstid;
	unsigned int txid;
	unsigned int rxid;
	WGNflag colErrorSign;		                     //collision with the transmitted pkt, not collision between two recv packets
	WGNflag fadErrorSign;
	WGNflag noiseErrorSign;
	int mac_size;                                            // size of the mac frame body (bytes)!
	//for physical layer
	int phy_size;                                              // size of the mac frame body + phy header size
	double tx_pwr;			                                  // (db)
	double rx_pwr;											  // (db)
	WGNTime link_start_time;
	WGNTime link_end_time;
	int signal;                                           //modulation spd
}	WGN_802_11_Mac_Pseudo_Header;

typedef struct {
  WGN_802_11_Mac_Pseudo_Header *pseudo_header;		           //just for computation in WGN
  WGN_802_3_Mac_Frame *frame_body;			 		                      //the packet mach wants
} WGN_802_11_Mac_Frame;

typedef enum {
    MAC_DATA_FRAME,
    MAC_RTS_FRAME,
    MAC_CTS_FRAME,
    MAC_ACK_FRAME
}   WGN_802_11_Mac_Frame_Type;

//=======================================================
//       Logical link control layer stuff
//=======================================================
  typedef struct {
    pthread_t buf_thread_id;
	struct interface *inf;                //interface to the Mach
    WGN_802_3_Mac_Frame* LToMArray[LLC_TO_MAC_BUF_SIZE];
	WGN_802_11_Mac_Frame* LToUArray[LLC_TO_MACH_BUF_SIZE];
	//logic link layer to mac layer
	int ltom_buf_Wcount;
	int ltom_buf_Rcount;
	pthread_mutex_t ltom_array_mutex;			/* LLToMachBuf array buffer mutex*/
    pthread_cond_t ltom_nonempty;				/* array non empty condition */
    pthread_cond_t ltom_nonfull;					/* array non full condition */
	WGNflag ltombufempty;
	//logic link layer to mach
    int ltou_buf_Wcount;
	int ltou_buf_Rcount;
	pthread_mutex_t ltou_array_mutex;			/* LLToMachBuf array buffer mutex*/
    pthread_cond_t ltou_nonempty;				/* array non empty condition */
    pthread_cond_t ltou_nonfull;					/* array non full condition */
}   NodeLlc;

 struct interface {
	int node_id;
	int iDescriptor;				/* file descriptor*/
	pthread_t threadID;				/* thread id assigned to this interface*/
	struct vpl_data sVPLdata;		/* vpl library stucture*/
};

char *pchSocket[MaxNodeNo];

//=======================================================
//             MAC layer stuff
//=======================================================
	int MAC_DATA_HDR_LEN;
	int MAC_RTS_LEN;
	int MAC_CTS_LEN;
	int MAC_ACK_LEN;

	double RTS_Time;       //sec
	double CTS_Time;		//sec
	double ACK_Time;		//sec
	double Wait_CTS_Timeout;    //sec

	typedef struct {
	   unsigned int id;
	   unsigned char mac_addr[MAC_ADDR_LEN];
	}  AddrToIDEntry;

	typedef enum {
	   NONE,
       CSMA,
       DCF_802_11,
    }  MAC_type;

	typedef struct {
       unsigned char addr[MAC_ADDR_LEN];
    }  MacAddr;

	typedef struct {
	   MAC_type macType;
       int MAC_RTSThreshold;
       int MAC_ShortRetryLimit;
	   int MAC_LongRetryLimit;
	   int MAC_Data_Frame_Header_Length;
	   int MAC_RTS_Frame_Header_Length;
	   int MAC_CTS_Frame_Header_Length;
	   int MAC_ACK_Frame_Header_Length;
	   int MAC_Frame_Tailer_Length;
   } MacMib;

   typedef enum {
      MAC_IDLE,
	  MAC_COLL,
      MAC_RECV,
      MAC_SEND,
      MAC_RTS,
      MAC_CTS,
      MAC_ACK
  }   MacStatus;

  typedef struct {
	//shared part
	char BSSID[MAC_ADDR_LEN];
	int seq_no;
	int frag_no;                       //not used now
	unsigned char mac_addr[MAC_ADDR_LEN];
	WGNflag TxActiveSign;
	MacStatus txStatus;
	MacStatus rxStatus;
    NodeTimer *nodeTimer;
	MacMib *macMib;
	WGN_802_11_Mac_Frame *pktTx;
	WGN_802_11_Mac_Frame *pktRsp;
	WGN_802_11_Mac_Frame *pktRx;
	int *mac_buffer;		                              	//used in duplication checking purpose
	int *mac_buf_index;
	WGNflag HasPkt;

	//none & csma
	int normal_retry_counter;

	//csma part
	double txprob;

	//dcf_802_11 part
	int cw;
	int short_retry_counter;
	int long_retry_counter;
	WGN_802_11_Mac_Frame *pktRts;

  } NodeMac;

  AddrToIDEntry *AddrToIDMap;
  MacAddr mac_addr_array[MaxNodeNo];
  unsigned char MAC_BROADCAST_ADDR[MAC_ADDR_LEN];

//=======================================================
//         Antenna stuff
//=======================================================
  typedef enum {
    OmniDirectionalAnt,
    SwitchedBeamAnt,
    AdaptiveArrayAnt
  } Antenna_type;

  typedef struct {
	//antenna attributes
	Antenna_type AntType;
    double Height;
    double Gain_dBi;
    double SysLoss;
	//handle the interference added on the antenna.
	double intPwr;                    //(W)
	double jamInt;                    //(W)
  } NodeAntenna;

//=======================================================
//       Energy stuff
//=======================================================
typedef enum {
       IDLE,
       TX,
	   RX,
	   SLEEP
    }  EnergyUpdate_type;

	typedef struct {
		Switch PowSwitch;
		Switch PSM;
		double TotalEnergy ;
		double Pt;
		double Pt_consume;
		double Pr_consume;
		double P_idle;
		double P_sleep;
		double P_off;
    } NodeEnergy;

//=======================================================
//       Mobility stuff
//=======================================================
  unsigned int Mob_update_remain;                      //nanosecond

  typedef enum {
    RandomWayPoint,
    TrajectoryBased,
	Manual
  } Mobility_type;

  typedef struct {
    double x;
    double y;
    double z;
  } NodePosition;

  typedef struct {
    double spd_x;      // the real speed in the x-axle of the node
    double spd_y;      // the real speed in the y-axle of the node
    double spd_z;      // the real speed in the z-axle of the node
	double spd_tot;    // the real total speed of the node
  } NodeSpd;

  /*RandomWay structure pointer, one kind of movStrPtr*/
  typedef struct {
    WGNflag MovChSign;      // the sign to show if it is the time to change the movement
    WGNflag EndSign;           // the sign to show whether to end the RWP moving mode
	double UpdateTimeUnit;  // the time interval that update parameters related to the node's movement
  } RWP_StrPtr;

  /*TrajectoryBased structure pointer, one kind of movStrPtr*/
  typedef struct {
    WGNflag EndSign;            // the sign to show whether to end the TB moving mode
    double UpdateTimeUnit;  // the time interval that update parameters related to the node's movement
  } TB_StrPtr;

  /*Manual structure pointer, one kind of movStrPtr*/
  typedef struct {
    WGNflag EndSign;           // the sign to show whether to end the MAN moving mode
	double UpdateTimeUnit;  // the time interval that update parameters related to the node's movement
  } MAN_StrPtr;

  typedef struct {
	Mobility_type mobilityType;
	void * movStrPtr;                            // the pointer point to the structure corresponding to the specific movement mode
	NodePosition *srcPosition;            // the starting location for each piece of movement
	NodePosition *dstPosition;            // the destination location for each piece of movement
    NodePosition *curPosition;            // the current location for each piece of movement
	double MaxSpd;                              // the max speed of the node
	double MinSpd;                               // the min speed of the node
	NodeSpd * nodeSpd;                      // the real speed of the node
	char* mov_rep_name;
  } NodeMobility;

//=======================================================
//       Wireless card stuff
//=======================================================
    typedef enum {
       DSSS,
       FHSS
    }  Modulation_type;

	typedef enum {
       SampleCard,
       DemoTS,
	   DemoTU,
	   DemoTT,
       SelfDefine
    }  WCard_type;

    typedef struct {
	   double freq;
       double bandwidth;
       double Pt;
       double Pt_consume;
       double Pr_consume;
       double P_idle;
       double P_sleep;
       double P_off;
       double RXThresh;						 //(W)
       double CSThresh;						 //(W)
       double CPThresh;	                     //db
       Modulation_type ModType;
	   Switch capsign;
    }  NodeWCard;

//=======================================================
//       Physical layer stuff
//=======================================================
    int PHY_DATA_HDR_LEN;
    int PHY_RTS_LEN;
    int PHY_CTS_LEN;
    int PHY_ACK_LEN;

    typedef struct {
		int    CWMin;
		int    CWMax;
		double SlotTime;                                      //seconds
		double CCATime;	                                     //seconds
		double RxTxTurnaroundTime;	                 //seconds
		double SIFSTime;                                    //seconds
		double PIFSTime;                                    //seconds
		double DIFSTime;                                    //seconds
		double EIFSTime;                                    //seconds
		int    PreambleLength;
		int    PLCPHeaderLength;
	}  PhyMib;

	typedef struct {
		PhyMib *phyMib;
		NodeWCard *nodeWCard;
		NodeEnergy *nodeEnergy;
		NodeAntenna *nodeAntenna;
		NodeMobility *nodeMobility;
	}	NodePhy;

//=======================================================
//       Propagation stuff
//=======================================================
typedef enum {
     FreeSpace,
	 TwoRayGround,
     Shadowing
   } Prop_type;

   typedef enum {
    OutDoor_FreeSpace,
	OutDoor_ShadowedUrbanArea,
    Inbuilding_LOS,
	Inbuilding_Obstructed
  }   PathLossEx_type;

  typedef enum {
    OutDoor,
	Office_SoftPartition,
	Office_HardPartition,
	Factory_LOS,
    Factory_Obstructed
  } ShadDev_type;

  typedef struct {
     Prop_type prop_type;
	 double pathlossexp;
     double dev_db;
     double dist0;
  }  ChannelProp;


//=======================================================
//        Awgn stuff
//=======================================================
   typedef enum {
    PWR,
	SNR
  } Awgn_Mode;

  typedef struct {
	  Switch switcher;
	  double noise;     //noise variance
      double snr;        //db
      Awgn_Mode mode;       // mode 1: set SNR directly    mode 2: add noise
  }  ChannelAwgn;


//=======================================================
//        Fading stuff
//=======================================================
  typedef struct {
	 Switch switcher;
	 int N0;     //the total number of oscillators in the Jakes model
	 int N;       //the total number of the simulated multiple paths
     double **coef;
	 double ***incr;
	 double ***pha;
	 double **fad;
  }  ChannelFad;


//=======================================================
//        Channel stuff
//=======================================================
   unsigned int Ch_update_remain;               //nanosecond

   typedef struct {
      ChannelProp *channelProp;
      ChannelAwgn *channelAwgn;
	  ChannelFad *channelFad;
      double channelbandwidth;
      WGNflag channelFadPrintSign;
	  int **linkmatrix;
   } ChannelMib;

   ChannelMib *Channel;

//=======================================================
//       Mobile node stuff
//=======================================================
  typedef int NodeId;

  typedef enum {
    AP,
    NORMAL
  } NodeType;

  typedef struct _wgn_config {
	int tot_node_no;
	int cli_flag;
	char *config_file;
	char *vs_directory;
 } wgn_config;

 typedef struct {
	MacMib *mac_info;
	PhyMib *phy_info;
	TimeMib *time_info;
	MapMib *map_info;
}   Sys_Info;

typedef struct {
    NodeId Id;
	NodeType nodeType;
	NodeLlc *nodeLlc;
	NodeMac *nodeMac;
	NodePhy *nodePhy;
	NodeStats *nodeStats;
} MobileNode;

extern wgn_config wgnconfig;
Sys_Info *SYS_INFO;
MobileNode *MobNodPtrAarry;
int TotNodeNo;

//=======================================================
//       Temperary variables (for initialization purpose)
//=======================================================
int X_Width;
int Y_Width;
int Z_Width;
double Ch_TimeUnit;                                   //time space to update channel status		  (sec)
double Mob_TimeUnit;                                //time space to update the mobility status   (sec)
int oscnum;
Modulation_type modulation;          //  store the modulation method
Prop_type prop_type;
MAC_type macType;
double pathlossexp;
double dev_db;
Switch RayleighSign;
Switch AwgnSign;
double dist0;
double noise;     //noise variance
double snr_Eb_N0;        //db
Awgn_Mode awgn_mode;                            // mode 1: set SNR directly    mode 2: add noise
double MaxSpdArray[MaxNodeNo];
double MinSpdArray[MaxNodeNo];
Mobility_type MobilityTypeArray[MaxNodeNo];              // store the mobility type for nodes
Antenna_type AntTypeArray[MaxNodeNo];                     // store the antenna type of the node
double AntHeightArray[MaxNodeNo];                             //  store the height of the node's antenna
double AntSysLosArray[MaxNodeNo];                           //  store the system loss the antenna of the node
Switch EnPowSwitchArray[MaxNodeNo];                       //  store the power switch status for the node
Switch EnPSMArray[MaxNodeNo];                                          //  store the PSM status for the node
double TotalEnergyArray[MaxNodeNo];                         //  store the source power preservation for the node
WCard_type WCardTypeArray[MaxNodeNo];               // store the wireless card type
Switch CapSignArray[MaxNodeNo];
double TxProbArray[MaxNodeNo];


//=======================================================
//                                                   Functions
//=======================================================
    void IniInput();
    void IniNetworkNode();
	void FinalizeNetworkNode();
	void IniMobNode();
	void FinalizeMobNode();
    void IniNodeID(NodeType type);
    void IniNodeType(NodeType type);
    void IniAddrToIDMap();
	void FinalizeAddrToIDMap();
    void IniSystem(int argc, char *argv[]);
	void FinalizeSystem();
	void IniProgram(int ac, char *av[]);
    void ReloadWGN();

  #endif

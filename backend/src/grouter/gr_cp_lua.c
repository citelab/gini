/*
 * gr_cp_lua.c — a CONTROL-PLANE Lua module: implement routing and forwarding protocols in Lua.
 *
 * The data-plane Lua module (gr_mod_lua.c) is reactive: process(pkt) runs per forwarded packet
 * and returns a verdict — good for filtering, useless for a protocol that must act on a clock,
 * originate packets, replicate them, or write the route table. This module hands Lua the
 * control-plane services (gr_control_plane.h): timers, send/emit primitives, and route-table writes.
 *
 * Loaded with:  gpipe cp add lua /scripts/x.lua  [tick_ms]
 *
 * The script defines callbacks and is handed a small API:
 *
 *     function init(ifaces)                       -- once at start; ifaces = {{iface=,ip=},..}
 *     function tick()                             -- every tick_ms; advertise, age
 *     function on_message(iface, src, payload, pkt) -- a matched packet arrived
 *          pkt = { dst=, proto=, ttl=, len=, raw=<the whole IP packet as a string> }
 *
 *     listen{ proto=, port=, group="a.b.c.d/p" }  -- what packets on_message should receive
 *     send(iface, data)                           -- broadcast a control message (UDP) on iface
 *     emit(iface, packet)                         -- send a raw IP packet out iface (multicast MAC)
 *     route_add(net, mask, nexthop, iface) / route_del(net, mask)
 *     interfaces()  -> { {iface=, ip=}, .. }
 *     log(msg)
 *
 * With `listen` + `emit` + the raw packet, a student can implement multicast (snoop IGMP for
 * membership, replicate datagrams to member interfaces) entirely in Lua; keeping the membership
 * table in Lua (not the C gr_mcast) means the built-in data-plane forwarder finds no members and
 * drops the original, so the Lua module is the sole forwarder. Built only with -DGR_LUA.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <lua.h>
#include <lualib.h>
#include <lauxlib.h>

#include "gr_control_plane.h"
#include "message.h"

#define GR_CP_LUA_PORT 5200      /* GINI control-plane teaching port (UDP) */

typedef struct { lua_State *L; const gr_cp_services_t *svc; gr_cp_module_t *self; } lua_cp_state;

/* every API function carries the module state as its first upvalue */
#define ST(L) ((lua_cp_state *)lua_touserdata((L), lua_upvalueindex(1)))

static int str2ip(const char *s, uchar ip[4])
{
    int a, b, c, d;
    if (!s || sscanf(s, "%d.%d.%d.%d", &a, &b, &c, &d) != 4) return -1;
    ip[0] = (uchar)a; ip[1] = (uchar)b; ip[2] = (uchar)c; ip[3] = (uchar)d;
    return 0;
}

/* ---- API callable from the script ---- */

static int l_route_add(lua_State *L)
{
    const gr_cp_services_t *svc = ST(L)->svc;
    uchar net[4], mask[4], nh[4];
    if (str2ip(luaL_checkstring(L, 1), net) || str2ip(luaL_checkstring(L, 2), mask)
        || str2ip(luaL_checkstring(L, 3), nh))
        return luaL_error(L, "route_add: expects dotted-quad net, mask, nexthop");
    svc->route_add(net, mask, nh, (int)luaL_checkinteger(L, 4));
    return 0;
}

static int l_route_del(lua_State *L)
{
    const gr_cp_services_t *svc = ST(L)->svc;
    uchar net[4], mask[4];
    if (str2ip(luaL_checkstring(L, 1), net) || str2ip(luaL_checkstring(L, 2), mask))
        return luaL_error(L, "route_del: expects dotted-quad net, mask");
    svc->route_del(net, mask);
    return 0;
}

/* send(iface, data): broadcast an opaque control message on one interface (UDP, control port). */
static int l_send(lua_State *L)
{
    const gr_cp_services_t *svc = ST(L)->svc;
    int iface = (int)luaL_checkinteger(L, 1);
    size_t len; const char *data = luaL_checklstring(L, 2, &len);
    uchar src[4];
    if (svc->iface_addr(iface, src) != 0) return 0;
    uchar bcast[4] = { src[0], src[1], src[2], 255 };
    uchar bmac[6]  = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
    svc->send_udp(iface, bmac, src, bcast, GR_CP_LUA_PORT, GR_CP_LUA_PORT, data, (int)len);
    return 0;
}

/* emit(iface, packet): send a raw IP packet out one interface as a multicast frame. The dst MAC is
 * derived from the packet's own destination group (01:00:5e + low 23 bits) — this is how a Lua
 * multicast forwarder replicates a datagram to a member interface. */
static int l_emit(lua_State *L)
{
    const gr_cp_services_t *svc = ST(L)->svc;
    int iface = (int)luaL_checkinteger(L, 1);
    size_t len; const char *p = luaL_checklstring(L, 2, &len);
    if (len < 20) return 0;
    const unsigned char *d = (const unsigned char *)p;     /* IP dst is bytes 16..19 */
    uchar mac[6] = { 0x01, 0x00, 0x5e, (uchar)(d[17] & 0x7f), d[18], d[19] };
    svc->send_raw(iface, mac, 0x0800 /* ETH_P_IP */, p, (int)len);
    return 0;
}

/* interfaces() -> array of { iface=<index>, ip="a.b.c.d" } */
static int l_interfaces(lua_State *L)
{
    const gr_cp_services_t *svc = ST(L)->svc;
    int n = svc->iface_count(), i, row = 0;
    lua_newtable(L);
    for (i = 0; i < n; i++)
    {
        uchar ip[4]; char s[16];
        if (svc->iface_addr(i, ip) != 0) continue;
        lua_newtable(L);
        lua_pushinteger(L, i);   lua_setfield(L, -2, "iface");
        snprintf(s, sizeof s, "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
        lua_pushstring(L, s);    lua_setfield(L, -2, "ip");
        lua_rawseti(L, -2, ++row);
    }
    return 1;
}

/* listen{ proto=N, port=P, group="a.b.c.d/p" }: declare which packets reach on_message. Fully
 * specifies the filter (call once, in init). A multicast module uses listen{group="224.0.0.0/4"}
 * to receive both IGMP membership messages and multicast datagrams. */
static int l_listen(lua_State *L)
{
    gr_cp_module_t *self = ST(L)->self;
    gr_cp_filter_t *f = &self->filter;
    luaL_checktype(L, 1, LUA_TTABLE);
    memset(f, 0, sizeof *f);
    lua_getfield(L, 1, "proto"); if (lua_isnumber(L, -1)) f->proto = (int)lua_tointeger(L, -1); lua_pop(L, 1);
    lua_getfield(L, 1, "port");  if (lua_isnumber(L, -1)) f->udp_dport = (int)lua_tointeger(L, -1); lua_pop(L, 1);
    lua_getfield(L, 1, "group");
    if (lua_isstring(L, -1))
    {
        int a = 0, b = 0, c = 0, e = 0, pfx = 32;
        sscanf(lua_tostring(L, -1), "%d.%d.%d.%d/%d", &a, &b, &c, &e, &pfx);
        f->dst_addr[0] = a; f->dst_addr[1] = b; f->dst_addr[2] = c; f->dst_addr[3] = e;
        unsigned m = (pfx <= 0) ? 0u : (pfx >= 32 ? 0xffffffffu : ~((1u << (32 - pfx)) - 1u));
        f->dst_mask[0] = (m >> 24) & 0xff; f->dst_mask[1] = (m >> 16) & 0xff;
        f->dst_mask[2] = (m >> 8) & 0xff;  f->dst_mask[3] = m & 0xff;
    }
    lua_pop(L, 1);
    return 0;
}

static int l_log(lua_State *L)
{
    ST(L)->svc->log("%s", luaL_checkstring(L, 1));
    return 0;
}

static void call_hook(lua_cp_state *s, const char *name, int nargs)
{
    if (lua_pcall(s->L, nargs, 0, 0) != LUA_OK)
    {
        s->svc->log("cp lua: %s error: %s", name, lua_tostring(s->L, -1));
        lua_pop(s->L, 1);
    }
}

static void lua_tick(gr_cp_module_t *self, void *arg)
{
    (void)arg;
    lua_cp_state *s = (lua_cp_state *)self->state;
    lua_getglobal(s->L, "tick");
    if (lua_isfunction(s->L, -1)) call_hook(s, "tick", 0);
    else lua_pop(s->L, 1);
}

/* a matched packet (a copy) -> on_message(iface, src, payload, pkt) */
static void lua_cp_on_packet(gr_cp_module_t *self, gpacket_t *pkt)
{
    lua_cp_state *s = (lua_cp_state *)self->state;
    lua_State *L = s->L;
    const unsigned char *d = (const unsigned char *)pkt->data.data;
    int ihl = (d[0] & 0x0f) * 4; if (ihl < 20) ihl = 20;
    int total = ((int)d[2] << 8) | d[3]; if (total < ihl) total = ihl;
    int proto = d[9];
    int off = (proto == 17) ? ihl + 8 : ihl;             /* UDP payload, else IP payload */
    int plen = total - off; if (plen < 0) plen = 0;
    char src[16], dst[16];
    snprintf(src, sizeof src, "%u.%u.%u.%u", d[12], d[13], d[14], d[15]);
    snprintf(dst, sizeof dst, "%u.%u.%u.%u", d[16], d[17], d[18], d[19]);

    lua_getglobal(L, "on_message");
    if (!lua_isfunction(L, -1)) { lua_pop(L, 1); return; }
    lua_pushinteger(L, pkt->frame.src_interface);
    lua_pushstring(L, src);
    lua_pushlstring(L, (const char *)(d + off), (size_t)plen);
    lua_newtable(L);                                     /* pkt = {dst,proto,ttl,len,raw} */
    lua_pushstring(L, dst);              lua_setfield(L, -2, "dst");
    lua_pushinteger(L, proto);           lua_setfield(L, -2, "proto");
    lua_pushinteger(L, d[8]);            lua_setfield(L, -2, "ttl");
    lua_pushinteger(L, total);           lua_setfield(L, -2, "len");
    lua_pushlstring(L, (const char *)d, (size_t)total); lua_setfield(L, -2, "raw");
    call_hook(s, "on_message", 4);
}

static int lua_cp_start(gr_cp_module_t *self, const gr_cp_services_t *svc, const char *args)
{
    char path[256] = ""; int tick_ms = 5000;
    if (args) sscanf(args, "%255s %d", path, &tick_ms);
    if (!path[0]) { svc->log("cp add lua: usage: cp add lua <script> [tick_ms]"); return -1; }
    if (tick_ms < 500) tick_ms = 500;

    lua_State *L = luaL_newstate();
    if (!L) return -1;
    luaL_openlibs(L);
    lua_cp_state *s = (lua_cp_state *)calloc(1, sizeof *s);
    if (!s) { lua_close(L); return -1; }
    s->L = L; s->svc = svc; s->self = self;
    self->state = s;

#define REG(nm, fn) do { lua_pushlightuserdata(L, s); lua_pushcclosure(L, (fn), 1); \
    lua_setglobal(L, (nm)); } while (0)
    REG("route_add",  l_route_add);
    REG("route_del",  l_route_del);
    REG("send",       l_send);
    REG("emit",       l_emit);
    REG("interfaces", l_interfaces);
    REG("listen",     l_listen);
    REG("log",        l_log);
#undef REG

    /* default filter (a routing module keeps this); a forwarding module overrides it with listen() */
    self->filter.proto = 17;
    self->filter.udp_dport = GR_CP_LUA_PORT;

    if (luaL_dofile(L, path) != LUA_OK)
    {
        svc->log("cp add lua: %s", lua_tostring(L, -1));
        lua_close(L); free(s); self->state = 0;
        return -1;
    }

    lua_getglobal(L, "init");                            /* optional: init(interfaces()) */
    if (lua_isfunction(L, -1))
    {
        lua_getglobal(L, "interfaces");
        lua_call(L, 0, 1);
        call_hook(s, "init", 1);
    }
    else lua_pop(L, 1);

    svc->timer_add(self, tick_ms, lua_tick, NULL);
    svc->log("cp lua: started %s (tick %dms)", path, tick_ms);
    return 0;
}

static void lua_cp_stop(gr_cp_module_t *self)
{
    lua_cp_state *s = (lua_cp_state *)self->state;
    if (!s) return;
    if (s->L) lua_close(s->L);
    free(s);
}

gr_cp_module_t *gr_cp_lua_create(void)
{
    gr_cp_module_t *m = (gr_cp_module_t *)calloc(1, sizeof *m);
    if (!m) return 0;
    m->name = "lua";
    m->start = lua_cp_start;
    m->on_packet = lua_cp_on_packet;
    m->stop = lua_cp_stop;
    return m;
}

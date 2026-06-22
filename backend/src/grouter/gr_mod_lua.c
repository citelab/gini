/*
 * gr_mod_lua.c  —  Z2 Lua scripting module (the friendly tier).
 *
 * A gr_module_t whose process() calls a student-written Lua `process(pkt, ctx)` hook.
 * Built only with -Dlua=true (links liblua); a router without it is unaffected.
 *
 * The student writes, e.g.:
 *     function process(pkt, ctx)
 *       if pkt.dst == "10.0.3.10" then return DROP end
 *       return CONTINUE
 *     end
 */
#include "gr_modules.h"
#include <stdio.h>
#include <stdlib.h>
#include <lua.h>
#include <lualib.h>
#include <lauxlib.h>

typedef struct { lua_State *L; } lua_mod_state;

/* verdict constants exposed to the script */
static const char *PRELUDE =
    "CONTINUE=0; DROP=1; CONSUMED=2; TO_HOST=3; FORWARD=4\n";

/* log(msg) — a simple print a script can call for visibility on the router console. */
static int l_log(lua_State *L)
{
    const char *msg = luaL_optstring(L, 1, "");
    printf("[lua] %s\n", msg);
    fflush(stdout);
    return 0;
}

static gr_verdict_t lua_process(gr_module_t *self, gpacket_t *pkt)
{
    lua_mod_state *s = (lua_mod_state *)self->state;
    lua_State *L = s->L;
    gr_verdict_t v = { GR_CONTINUE, -1 };

    lua_getglobal(L, "process");
    if (!lua_isfunction(L, -1)) { lua_pop(L, 1); return v; }

    /* pkt table: a friendly, language-neutral view of the packet (gr_pkt_* accessors). */
    {
        uint32_t sd = gr_pkt_ipdst(pkt), ss = gr_pkt_ipsrc(pkt);
        char dst[16], src[16];
        snprintf(dst, sizeof dst, "%u.%u.%u.%u",
                 (sd >> 24) & 255, (sd >> 16) & 255, (sd >> 8) & 255, sd & 255);
        snprintf(src, sizeof src, "%u.%u.%u.%u",
                 (ss >> 24) & 255, (ss >> 16) & 255, (ss >> 8) & 255, ss & 255);
        lua_newtable(L);
        lua_pushstring(L, dst);                 lua_setfield(L, -2, "dst");
        lua_pushstring(L, src);                 lua_setfield(L, -2, "src");
        lua_pushinteger(L, gr_pkt_proto(pkt));  lua_setfield(L, -2, "proto");
        lua_pushinteger(L, gr_pkt_ttl(pkt));    lua_setfield(L, -2, "ttl");
        lua_pushinteger(L, gr_pkt_len(pkt));    lua_setfield(L, -2, "len");
    }
    lua_newtable(L);           /* ctx: per-packet scratch (use Lua globals for state) */

    if (lua_pcall(L, 2, 1, 0) != LUA_OK) { lua_pop(L, 1); return v; }
    int action = (int)lua_tointeger(L, -1);
    lua_pop(L, 1);

    switch (action)
    {
        case 1: v.action = GR_DROP;     break;
        case 2: v.action = GR_CONSUMED; break;
        case 3: v.action = GR_TO_HOST;  break;
        case 4: v.action = GR_FORWARD;  break;
        default: v.action = GR_CONTINUE;
    }
    return v;
}

static void lua_destroy(gr_module_t *self)
{
    lua_mod_state *s = (lua_mod_state *)self->state;
    if (s->L) lua_close(s->L);
    free(s);
    free(self);
}

gr_module_t *gr_mod_lua(const char *script)
{
    lua_State *L = luaL_newstate();
    luaL_openlibs(L);
    lua_pushcfunction(L, l_log); lua_setglobal(L, "log");
    luaL_dostring(L, PRELUDE);
    if (script) luaL_dostring(L, script);

    lua_mod_state *s = (lua_mod_state *)malloc(sizeof(lua_mod_state));
    s->L = L;
    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    m->type = "lua"; m->state = s; m->init = 0;
    m->process = lua_process; m->destroy = lua_destroy;
    return m;
}

-- mcast_tree.lua — the multi-router multicast forwarder GINI provides for the
-- Multicast File Distribution capstone (Network Multicasting chapter).
--
-- Load on EVERY router in the topology:
--     gpipe cp add lua /scripts/mcast_tree.lua 1000
--
-- It is a dense-mode protocol, the family of DVMRP and PIM-DM: flood, then
-- prune back to a tree. Four ideas, each a few lines below:
--
-- 1. REVERSE-PATH FORWARDING. A datagram from source S is accepted only if it
--    arrived on the interface this router would use to send TO S — the unicast
--    route, from route_lookup(S). Any other copy took a longer way round and is
--    dropped. That one check is what makes a network with loops safe: every
--    router forwards each datagram at most once, so nothing circulates and no
--    host gets two copies. And because the unicast routes follow link costs
--    (static routes are cost-shortest paths, RIP adds the costs), the
--    multicast tree is the cheapest-path tree back to the source — when RIP
--    routes around a failed link, the tree moves with it.
--
-- 2. FLOOD AND PRUNE. An accepted datagram is copied to every interface with
--    a host member (learned from IGMP) and to every neighbouring router that
--    has not said "no thanks". A router that has nobody downstream sends a
--    PRUNE for (S, G) up its reverse path; a router that receives a copy off
--    its reverse path sends a PRUNE back on that link. Prunes are soft: after
--    PRUNE_HOLD ticks the branch is flooded again, and pruned again if still
--    nobody wants it. That is the price of dense mode — and its robustness.
--
-- 3. GRAFT. When a host joins behind a pruned branch, the router sends a JOIN
--    up its reverse path straight away instead of waiting for the next flood.
--    The same JOIN is sent when the reverse path MOVES (a link failed, a cost
--    changed, RIP picked a new route): the new upstream may have been told to
--    prune us while it was not on our path.
--
-- 4. SOFT STATE. Every router asks its links "who is a member?" (an IGMP
--    general query) every QUERY_EVERY ticks; a host membership that is not
--    re-reported within HOST_HOLD ticks expires, so a leave that was lost —
--    sent while a link was down — cannot keep a branch alive forever. A link
--    that fails (on_link_change) forgets its members and prunes at once.
--
-- Each forwarded copy has its TTL decremented, as any router hop must.
--
-- Two routers on one LAN with members on it would both forward onto it; when
-- a router sees another's copy arrive on a LAN it forwards onto, it sends an
-- ASSERT, and the router with the lower address on that LAN stops.
--
-- Receivers may speak IGMPv2 or v3: a Linux host answers our (v2) queries in v2,
-- and its first report, sent the moment it joins, may be v3 -- both are read.
--
-- Observability: `gpipe cp status` (which the Multicast HUD polls) reports
--     MCAST v1 <ngroups>
--     G 239.1.1.1 IF 1,2 CP 1:120,2:118        members and interfaces copied to
--                                              in the last 2 ticks, and counts
--     S 10.0.1.10 G 239.1.1.1 RPF 3 OIF 1,2 PRUNED 4 DROPS 7 COPIES 240
--                                              the tree for one source: where
--                                              it must arrive, where it goes,
--                                              who pruned, copies refused by
--                                              the reverse-path check, and
--                                              copies sent on

QUERY_EVERY = 2     -- ticks between IGMP general queries on every link
HOST_HOLD   = 6     -- a host membership lasts this long without a report
NBR_HOLD    = 5     -- a neighbouring router is forgotten after this long silent
PRUNE_HOLD  = 10    -- a prune holds this long, then the branch is flooded again
SG_HOLD     = 30    -- a source not heard from for this long is forgotten

ALL_HOSTS   = "224.0.0.1"
ALL_ROUTERS = "224.0.0.13"      -- where PRUNE / JOIN / ASSERT go (TTL 1)
PROTO_CTL   = 103               -- the IP protocol number PIM uses
PRUNE, JOIN, ASSERT = 1, 2, 3
KIND = { "PRUNE", "JOIN", "ASSERT" }

now     = 0     -- ticks since start
iflist  = {}    -- this router's interface numbers
ifip    = {}    -- iface -> our address on it
ifup    = {}    -- iface -> is the link up
hosts   = {}    -- group -> { [iface] = tick the membership expires }
routers = {}    -- iface -> tick the neighbouring router there is forgotten
sg      = {}    -- "S G" -> per-source tree state (see state())
copies  = {}    -- group -> { [iface] = datagrams copied there }

-- ---- packets -----------------------------------------------------------------

local function ip4(s)                      -- "a.b.c.d" -> 4 raw bytes
    local a, b, c, d = s:match("(%d+)%.(%d+)%.(%d+)%.(%d+)")
    return string.char(tonumber(a) or 0, tonumber(b) or 0,
                       tonumber(c) or 0, tonumber(d) or 0)
end

local function str4(raw, at)               -- 4 raw bytes at `at` -> "a.b.c.d"
    return string.format("%d.%d.%d.%d", raw:byte(at, at + 3))
end

local function num(ip)                     -- "a.b.c.d" -> a comparable number
    local a, b, c, d = ip:match("(%d+)%.(%d+)%.(%d+)%.(%d+)")
    return ((tonumber(a) or 0) << 24) | ((tonumber(b) or 0) << 16)
           | ((tonumber(c) or 0) << 8) | (tonumber(d) or 0)
end

local function cksum16(s)                  -- one's-complement 16-bit checksum
    local sum = 0
    for i = 1, #s - 1, 2 do sum = sum + (s:byte(i) << 8) + s:byte(i + 1) end
    if #s % 2 == 1 then sum = sum + (s:byte(#s) << 8) end
    while sum > 0xffff do sum = (sum & 0xffff) + (sum >> 16) end
    return (~sum) & 0xffff
end

local function ip_packet(proto, src, dst, body)      -- TTL 1: one link only
    local function hdr(c)
        return string.char(0x45, 0, 0, 20 + #body, 0, 0, 0, 0, 1, proto,
                           (c >> 8) & 0xff, c & 0xff) .. ip4(src) .. ip4(dst)
    end
    return hdr(cksum16(hdr(0))) .. body
end

local function igmp_query(iface)           -- "who is a member of anything?"
    local function body(c)                 -- max response time 1 s (10 tenths)
        return string.char(0x11, 10, (c >> 8) & 0xff, c & 0xff) .. ip4("0.0.0.0")
    end
    return ip_packet(2, ifip[iface] or "0.0.0.0", ALL_HOSTS, body(cksum16(body(0))))
end

local function control(iface, kind, s, g)  -- PRUNE / JOIN / ASSERT for (s, g)
    local me = ifip[iface] or "0.0.0.0"
    emit(iface, ip_packet(PROTO_CTL, me, ALL_ROUTERS,
                          string.char(kind) .. ip4(s) .. ip4(g) .. ip4(me)))
end

local function hop(raw)                    -- the same datagram, one TTL less
    local ihl = (raw:byte(1) & 0x0f) * 4
    local h = raw:sub(1, 8) .. string.char(raw:byte(9) - 1) .. raw:sub(10, 10)
              .. "\0\0" .. raw:sub(13, ihl)
    local c = cksum16(h)
    return h:sub(1, 10) .. string.char((c >> 8) & 0xff, c & 0xff) .. h:sub(13)
           .. raw:sub(ihl + 1)
end

-- ---- state -------------------------------------------------------------------

local function state(s, g)
    local k = s .. " " .. g
    local st = sg[k]
    if not st then
        st = { s = s, g = g, rpf = nil, seen = now,
               pruned = {},    -- iface -> tick our prune of that branch ends
               pending = {},   -- iface -> tick a received prune takes effect
               asserted = {},  -- iface -> tick we stop deferring to a LAN winner
               said = {},      -- iface -> tick we last sent a control there
               sent = {},      -- iface -> tick we last copied a datagram there
               up_pruned = false, drops = 0, copied = 0 }
        sg[k] = st
    end
    return st
end

local function live(t) return t ~= nil and t >= now end

local function member(g, i) return hosts[g] ~= nil and live(hosts[g][i]) end

-- Where a datagram of (S, G) goes from here: every working link except the one
-- it must arrive on, that has a host member (unless another router on that LAN
-- won the assert) or a neighbouring router that has not pruned it.
local function downstream(st)
    local out = {}
    for _, i in ipairs(iflist) do
        if i ~= st.rpf and ifup[i] then
            if member(st.g, i) then
                if not live(st.asserted[i]) then out[#out + 1] = i end
            elseif live(routers[i]) and not live(st.pruned[i]) then
                out[#out + 1] = i
            end
        end
    end
    return out
end

-- At most one control message per (S, G, link) per tick: a datagram stream
-- would otherwise send a prune for every packet.
local function say(st, iface, kind)
    if st.said[iface] == now then return end
    st.said[iface] = now
    control(iface, kind, st.s, st.g)
end

local function graft(st)                   -- put us back on the tree
    if st.rpf and #downstream(st) > 0 then
        st.said[st.rpf] = nil
        say(st, st.rpf, JOIN)
        st.up_pruned = false
    end
end

local function snapshot()                  -- publish live state for the HUD
    local groups = {}
    for g, m in pairs(hosts) do
        for i, t in pairs(m) do
            if live(t) then groups[g] = groups[g] or {}; groups[g][i] = true end
        end
    end
    local srcs = {}
    for _, st in pairs(sg) do
        local oifs = downstream(st)
        groups[st.g] = groups[st.g] or {}
        -- the HUD's row is where datagrams are going, not where they might: an
        -- unpruned neighbour that nothing has been sent to lately is not a branch
        for i, t in pairs(st.sent) do
            if now - t <= 2 then groups[st.g][i] = true end
        end
        local pr = {}
        for i, t in pairs(st.pruned) do if live(t) then pr[#pr + 1] = i end end
        table.sort(pr)
        srcs[#srcs + 1] = "S " .. st.s .. " G " .. st.g .. " RPF " .. tostring(st.rpf or "-")
            .. " OIF " .. (#oifs > 0 and table.concat(oifs, ",") or "-")
            .. " PRUNED " .. (#pr > 0 and table.concat(pr, ",") or "-")
            .. " DROPS " .. st.drops .. " COPIES " .. st.copied
    end
    local lines, n = {}, 0
    for g, set in pairs(groups) do
        local ifs, cps, c = {}, {}, copies[g] or {}
        for i in pairs(set) do ifs[#ifs + 1] = i end
        table.sort(ifs)
        for k, i in ipairs(ifs) do cps[k] = i .. ":" .. (c[i] or 0) end
        if #ifs > 0 then
            n = n + 1
            lines[#lines + 1] = "G " .. g .. " IF " .. table.concat(ifs, ",")
                                .. " CP " .. table.concat(cps, ",")
        end
    end
    table.sort(srcs)
    for _, l in ipairs(srcs) do lines[#lines + 1] = l end
    table.insert(lines, 1, "MCAST v1 " .. n)
    publish(table.concat(lines, "\n"))
end

local function refresh_links()
    iflist = {}
    for _, e in ipairs(interfaces()) do
        iflist[#iflist + 1] = e.iface
        ifip[e.iface] = e.ip
        ifup[e.iface] = (e.up ~= false)
    end
end

-- ---- what arrives ------------------------------------------------------------

local function host_report(iface, g)      -- a host on iface is a member of g
    if g == "0.0.0.0" then return end
    hosts[g] = hosts[g] or {}
    local new = not live(hosts[g][iface])
    hosts[g][iface] = now + HOST_HOLD
    if new then
        log("mcast_tree: join " .. g .. " on if" .. iface)
        for _, st in pairs(sg) do
            if st.g == g and st.up_pruned then graft(st) end
        end
        snapshot()
    end
end

local function host_leave(iface, g)        -- ask the link who is left
    if hosts[g] and hosts[g][iface] then
        hosts[g][iface] = nil
        log("mcast_tree: leave " .. g .. " on if" .. iface)
        emit(iface, igmp_query(iface))
        snapshot()
    end
end

local function on_igmp(iface, body)
    local kind = body:byte(1)
    if kind == 0x11 then                   -- a query: a router lives on this link
        routers[iface] = now + NBR_HOLD
    elseif kind == 0x16 or kind == 0x12 then
        host_report(iface, str4(body, 5))  -- IGMPv2 (or v1) membership report
    elseif kind == 0x17 then
        host_leave(iface, str4(body, 5))
    elseif kind == 0x22 and #body >= 8 then
        -- IGMPv3, what Linux sends until it hears our (v2) query: a list of group
        -- records. "Exclude nobody" (types 2, 4) is a plain join; "include nobody"
        -- (types 1, 3 with no sources) is a leave.
        local at = 9
        for _ = 1, (body:byte(7) << 8) | body:byte(8) do
            if #body < at + 7 then break end
            local rtype, aux = body:byte(at), body:byte(at + 1)
            local ns = (body:byte(at + 2) << 8) | body:byte(at + 3)
            local g = str4(body, at + 4)
            if rtype == 2 or rtype == 4 or (ns > 0 and (rtype == 1 or rtype == 5)) then
                host_report(iface, g)
            elseif ns == 0 and (rtype == 1 or rtype == 3) then
                host_leave(iface, g)
            end
            at = at + 8 + 4 * ns + 4 * aux
        end
    end
end

local function on_control(iface, body)
    if #body < 13 then return end
    local kind, s, g, from = body:byte(1), str4(body, 2), str4(body, 6), str4(body, 10)
    local st = state(s, g)
    routers[iface] = now + NBR_HOLD
    if kind == PRUNE then
        if iface == st.rpf then            -- another router on our upstream LAN
            if #downstream(st) > 0 then say(st, iface, JOIN) end    -- we still need it
        elseif not member(g, iface) then   -- hosts on that link still need it
            st.pending[iface] = st.pending[iface] or (now + 1)       -- room to override
        end
    elseif kind == JOIN then
        st.pending[iface], st.pruned[iface] = nil, nil
        if st.up_pruned then graft(st) end
    elseif kind == ASSERT then
        if num(from) > num(ifip[iface] or "0.0.0.0") then
            st.asserted[iface] = now + PRUNE_HOLD                    -- they win this LAN
        end
    end
end

local function on_data(iface, s, pkt)
    local g = pkt.dst
    local st = state(s, g)
    st.seen = now
    st.rpf = route_lookup(s)               -- nil: no route back to the source
    if iface ~= st.rpf then                -- off the reverse path: refuse it
        st.drops = st.drops + 1
        if member(g, iface) then say(st, iface, ASSERT)   -- two of us feed this LAN
        else say(st, iface, PRUNE) end                    -- stop sending it this way
        return
    end
    if pkt.ttl <= 1 then return end
    local oifs = downstream(st)
    if #oifs == 0 then                     -- nobody below us: prune upstream
        st.up_pruned = true
        say(st, iface, PRUNE)
        return
    end
    st.up_pruned = false
    local out, c = hop(pkt.raw), copies[g] or {}
    copies[g] = c
    for _, i in ipairs(oifs) do
        emit(i, out)
        c[i] = (c[i] or 0) + 1
        st.sent[i] = now
        st.copied = st.copied + 1
    end
end

-- ---- callbacks ---------------------------------------------------------------

function init(ifaces)
    if route_lookup == nil then
        error("mcast_tree: this router has no route_lookup(); update the GINI router image")
    end
    refresh_links()
    listen{ group = "224.0.0.0/4" }        -- IGMP, our control messages, and data
    for _, i in ipairs(iflist) do
        if ifup[i] then emit(i, igmp_query(i)) end
    end
    log("mcast_tree: up on " .. #iflist .. " interfaces")
    snapshot()
end

function on_message(iface, src, payload, pkt)
    if pkt.proto == 2 then
        on_igmp(iface, payload)
    elseif pkt.proto == PROTO_CTL and pkt.dst == ALL_ROUTERS then
        on_control(iface, payload)
    elseif not pkt.dst:match("^224%.0%.0%.") then    -- link-local: never forwarded
        on_data(iface, src, pkt)
    end
end

function on_link_change(iface, up, cost)
    ifup[iface] = up
    if not up then                         -- everything learned over it is gone
        routers[iface] = nil
        for g, m in pairs(hosts) do
            if m[iface] then m[iface] = nil; log("mcast_tree: if" .. iface .. " down, "
                                                 .. g .. " member lost") end
        end
        for _, st in pairs(sg) do
            st.pruned[iface], st.pending[iface], st.asserted[iface] = nil, nil, nil
        end
    else
        emit(iface, igmp_query(iface))     -- members and routers there, speak up
    end
    snapshot()
end

function tick()
    now = now + 1
    refresh_links()
    if now % QUERY_EVERY == 0 then
        for _, i in ipairs(iflist) do
            if ifup[i] then emit(i, igmp_query(i)) end
        end
    end
    for g, m in pairs(hosts) do            -- memberships nobody re-reported
        for i, t in pairs(m) do
            if t < now then m[i] = nil; log("mcast_tree: " .. g .. " on if" .. i .. " expired") end
        end
        if next(m) == nil then hosts[g] = nil end
    end
    for k, st in pairs(sg) do
        for i, t in pairs(st.pending) do   -- prunes nobody overrode take effect
            if now >= t then st.pruned[i] = now + PRUNE_HOLD; st.pending[i] = nil end
        end
        local rpf = route_lookup(st.s)
        if rpf ~= st.rpf then              -- the reverse path moved: rejoin there
            log("mcast_tree: " .. st.s .. " now reached via if" .. tostring(rpf or "-")
                .. " (was if" .. tostring(st.rpf or "-") .. ")")
            st.rpf = rpf
            graft(st)
        end
        if now - st.seen > SG_HOLD then sg[k] = nil end
    end
    snapshot()
end

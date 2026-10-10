-- mcast_tree.lua — the multi-router multicast forwarder GINI provides for the
-- Multicast File Distribution capstone (Network Multicasting chapter).
--
-- Load on EVERY router in the topology:
--     gpipe cp add lua /scripts/mcast_tree.lua 1000
--
-- The single-router half is the forwarder from the chapter's first experiment:
-- snoop IGMP membership reports and leaves per interface, and replicate each
-- multicast datagram to every member interface except the one it arrived on.
--
-- What "tree" adds is that membership TRAVELS. When a group gains its first
-- member interface on this router, the router announces an IGMP membership
-- report of its own out every interface that has no member — exactly as if it
-- were a host that had joined. The next router hears the report, records that
-- interface as a member, and (if the group is new to it) announces onward. Hop
-- by hop the join is grafted toward every sender. When the last member leaves,
-- the router sends a leave the same way, pruning the branch. On a loop-free
-- topology this builds the classic multicast distribution tree; membership is
-- re-announced each tick, so routers started late still learn it.
--
-- Observability: per-(group, interface) copy counters, published each tick so
-- `gpipe cp status` (which the Multicast HUD polls) reports live state:
--     MCAST v1 <ngroups>
--     G 239.1.1.1 IF 1,2 CP 1:120,2:118

members = {}    -- group -> { [iface] = true, ... }   who wants the group, per iface
copies  = {}    -- group -> { [iface] = count }        datagrams replicated per iface
iflist  = {}    -- array of this router's interface indices
ifip    = {}    -- iface -> our IP on that interface (source of proxy reports)

-- ---- helpers ---------------------------------------------------------------

local function ip4(s)                      -- "a.b.c.d" -> 4 raw bytes
    local a, b, c, d = s:match("(%d+)%.(%d+)%.(%d+)%.(%d+)")
    return string.char(tonumber(a) or 0, tonumber(b) or 0,
                       tonumber(c) or 0, tonumber(d) or 0)
end

local function cksum16(s)                  -- one's-complement 16-bit checksum
    local sum = 0
    for i = 1, #s - 1, 2 do sum = sum + (s:byte(i) << 8) + s:byte(i + 1) end
    if #s % 2 == 1 then sum = sum + (s:byte(#s) << 8) end
    while sum > 0xffff do sum = (sum & 0xffff) + (sum >> 16) end
    return (~sum) & 0xffff
end

-- Build a raw IGMPv2 packet: kind 0x16 = membership report, 0x17 = leave.
-- Reports are addressed to the group itself; leaves to all-routers (224.0.0.2).
-- TTL is 1: membership messages never travel beyond one link.
local function igmp_pkt(kind, grp, srcip)
    local g  = ip4(grp)
    local ig = string.char(kind, 0, 0, 0) .. g
    local ic = cksum16(ig)
    ig = string.char(kind, 0, (ic >> 8) & 0xff, ic & 0xff) .. g
    local dst = (kind == 0x17) and ip4("224.0.0.2") or g
    local hdr = string.char(0x45, 0, 0, 28, 0, 0, 0, 0, 1, 2, 0, 0)
                .. ip4(srcip) .. dst
    local hc = cksum16(hdr)
    hdr = string.char(0x45, 0, 0, 28, 0, 0, 0, 0, 1, 2, (hc >> 8) & 0xff, hc & 0xff)
          .. ip4(srcip) .. dst
    return hdr .. ig
end

-- Announce our interest in grp out every interface that has NO member for it
-- (the proxy join that grafts the branch); kind selects report or leave.
local function announce(grp, kind)
    local m = members[grp] or {}
    for _, i in ipairs(iflist) do
        if not m[i] then
            emit(i, igmp_pkt(kind, grp, ifip[i] or "0.0.0.0"))
        end
    end
end

local function snapshot()                  -- publish live state for the HUD
    local lines, n = {}, 0
    for _ in pairs(members) do n = n + 1 end
    lines[1] = "MCAST v1 " .. n
    for g, m in pairs(members) do
        local ifs = {}
        for i in pairs(m) do ifs[#ifs + 1] = i end
        table.sort(ifs)
        local c, cps = copies[g] or {}, {}
        for k, i in ipairs(ifs) do cps[k] = i .. ":" .. (c[i] or 0) end
        lines[#lines + 1] = "G " .. g .. " IF " .. table.concat(ifs, ",")
                            .. " CP " .. table.concat(cps, ",")
    end
    publish(table.concat(lines, "\n"))
end

local function join(grp, iface)
    local first = (members[grp] == nil)
    members[grp] = members[grp] or {}
    copies[grp]  = copies[grp]  or {}
    if not members[grp][iface] then
        members[grp][iface] = true
        log("mcast_tree: join " .. grp .. " on if" .. iface)
    end
    if first then announce(grp, 0x16) end  -- graft: tell the neighbours upstream
    snapshot()
end

local function leave(grp, iface)
    local m = members[grp]
    if not m or not m[iface] then return end
    m[iface] = nil
    log("mcast_tree: leave " .. grp .. " on if" .. iface)
    if next(m) == nil then                 -- last member gone: prune the branch
        members[grp] = nil
        copies[grp]  = nil
        announce(grp, 0x17)
    end
    snapshot()
end

-- ---- callbacks -------------------------------------------------------------

function init(ifaces)
    for _, e in ipairs(ifaces) do
        iflist[#iflist + 1] = e.iface
        ifip[e.iface] = e.ip
    end
    listen{ group = "224.0.0.0/4" }        -- IGMP messages AND multicast datagrams
    log("mcast_tree: up on " .. #iflist .. " interfaces")
    snapshot()
end

function on_message(iface, src, payload, pkt)
    local raw = pkt.raw
    if pkt.proto == 2 then                 -- IGMP membership message
        local ihl  = (raw:byte(1) & 0x0f) * 4
        local kind = raw:byte(ihl + 1)
        local grp  = string.format("%d.%d.%d.%d", raw:byte(ihl + 5),
                     raw:byte(ihl + 6), raw:byte(ihl + 7), raw:byte(ihl + 8))
        if grp == "0.0.0.0" then return end
        if kind == 0x16 or kind == 0x12 then join(grp, iface)
        elseif kind == 0x17 then leave(grp, iface) end
    else                                   -- a multicast datagram: replicate
        local g = pkt.dst
        if g:match("^224%.0%.0%.") then return end   -- link-local: never forwarded
        local m = members[g]
        if not m then return end           -- no members anywhere: drop
        local c = copies[g]
        for i in pairs(m) do
            if i ~= iface then             -- one copy per member iface, not the ingress
                emit(i, raw)
                c[i] = (c[i] or 0) + 1
            end
        end
    end
end

function tick()
    -- soft refresh: re-graft every active group (routers that started late catch
    -- up), and publish the counters so the HUD sees the carousel turning.
    for g in pairs(members) do announce(g, 0x16) end
    snapshot()
end

-- rip_reference.lua — the worked distance-vector module for the "A Distance-Vector
-- Routing Protocol" experiment (Building Simple Network Protocols chapter).
--
-- Load on EVERY router in the topology:
--     gpipe cp add lua /scripts/rip_reference.lua 5000
--
-- The chapter prints a base module and asks you to add split horizon yourself. This is
-- that finished version, for comparison AFTER you have written your own. It is the same
-- algorithm with three additions: poison reverse, a small amount of bookkeeping so the
-- route table is only rewritten when something actually changes, and a published status
-- snapshot so `gpipe cp status` shows what the router currently believes.
--
-- What this is NOT: RIP's wire format. Vectors travel as plain text on the control port,
-- because the point of the exercise is the ALGORITHM. RFC 2453 specifies the real thing,
-- with its own packet layout, timers, and authentication. Reading it after building this
-- is the fastest way to see what a specification adds to an idea.

INF = 16                 -- RIP's "unreachable"; keeps the count-to-infinity short
AGE_TICKS = 3            -- ticks without hearing a route before it is presumed gone

local dv       = {}      -- net ("a.b.c.0/24") -> {cost, nexthop, iface, connected, age}
local myifaces = {}

-- ---- helpers ---------------------------------------------------------------
local function encode(pairs_list)              -- "net cost,net cost,..."
    local p = {}
    for _, e in ipairs(pairs_list) do p[#p + 1] = e[1] .. " " .. e[2] end
    return table.concat(p, ",")
end

local function decode(s)
    local vec = {}
    for net, cost in s:gmatch("([%d%./]+) (%d+)") do vec[net] = tonumber(cost) end
    return vec
end

local function net_of(ip)  return (ip:gsub("%d+$", "0")) .. "/24" end
local function addr_of(nt) return (nt:gsub("/24$", "")) end

-- The vector we advertise OUT OF one interface. Split horizon with poison reverse: a
-- route learned on this interface is still mentioned, but at infinity, which says
-- explicitly "do not route through me for this" instead of staying silent. Silence
-- would work too (plain split horizon), but an explicit infinity travels faster,
-- because the neighbour does not have to wait for the route to age out.
local function vector_for(iface)
    local out = {}
    for net, r in pairs(dv) do
        local cost = r.cost
        if (not r.connected) and r.iface == iface then cost = INF end
        out[#out + 1] = {net, cost}
    end
    table.sort(out, function(a, b) return a[1] < b[1] end)   -- stable, easier to read
    return out
end

local function snapshot()                      -- what `gpipe cp status` reports
    local lines, n = {}, 0
    for _ in pairs(dv) do n = n + 1 end
    lines[1] = "RIP v1 " .. n
    local nets = {}
    for net in pairs(dv) do nets[#nets + 1] = net end
    table.sort(nets)
    for _, net in ipairs(nets) do
        local r = dv[net]
        lines[#lines + 1] = string.format("N %s COST %d %s", net, r.cost,
            r.connected and "connected" or ("via " .. (r.nexthop or "?")))
    end
    publish(table.concat(lines, "\n"))
end

-- ---- callbacks -------------------------------------------------------------
function init(list)
    myifaces = list
    for _, itf in ipairs(list) do                       -- our own LANs are free
        dv[net_of(itf.ip)] = {cost = 0, connected = true}
    end
    log("rip: up on " .. #list .. " interfaces")
    snapshot()
end

function tick()
    for _, itf in ipairs(myifaces) do                   -- advertise, per interface
        send(itf.iface, encode(vector_for(itf.iface)))
    end
    local changed = false                               -- then age what we learned
    for net, r in pairs(dv) do
        if not r.connected then
            r.age = (r.age or 0) + 1
            if r.age >= AGE_TICKS and r.cost < INF then
                r.cost = INF
                route_del(addr_of(net), "255.255.255.0")
                log("rip: " .. net .. " aged out")
                changed = true
            end
        end
    end
    if changed then snapshot() end
end

function on_message(iface, src, data)
    local changed = false
    for net, cost in pairs(decode(data)) do
        local r = dv[net]
        if not (r and r.connected) then          -- a connected LAN always wins
            local newc = math.min(cost + 1, INF)
            -- Take the route if it is better, OR if it comes from the neighbour we are
            -- already using, because that neighbour is the authority on its own cost
            -- (this is how bad news arrives at all).
            if (not r) or newc < r.cost or r.nexthop == src then
                local was = r and r.cost or nil
                dv[net] = {cost = newc, nexthop = src, iface = iface, age = 0}
                if newc < INF then
                    route_add(addr_of(net), "255.255.255.0", src, iface)
                elseif was and was < INF then
                    route_del(addr_of(net), "255.255.255.0")
                end
                if was ~= newc then
                    log(string.format("rip: %s cost %s -> %d via %s",
                        net, tostring(was), newc, src))
                    changed = true
                end
            end
        end
    end
    if changed then snapshot() end
end

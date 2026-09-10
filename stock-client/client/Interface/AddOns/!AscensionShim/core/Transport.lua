-- core/Transport.lua: the addon-message channel to mod-ascension-ca.
--
-- Client -> server:  SendAddonMessage("ASC", "CMD\t<verb>[\t<body>]", "WHISPER", me)
-- Server -> client:  CHAT_MSG_ADDON prefix "ASC", message "<verb>[\t<body>]" (the module
--                    whispers the player as themselves with LANG_ADDON).
-- Long bodies travel as numbered fragments in both directions:
--                    <verb>\t#\t<seq>\t<count>\t<chunk>
-- A chunk may contain tabs (STATE does), so a fragment is split into at most five fields.
-- One reassembly slot per verb per direction: the module answers a verb before it sends
-- the same verb again, and the client sends APPLY once at a time.
ASC.Transport = ASC.Transport or {}
local T = ASC.Transport
T.PREFIX = "ASC"
T.MAX = 200 -- characters per addon message body; the client caps prefix + body at 255
local handlers = {}
local inbound = {}
local playerName

local function me()
    playerName = playerName or UnitName("player")
    return playerName
end

function T.On(verb, fn) handlers[verb] = fn end

local function raw(body)
    SendAddonMessage(T.PREFIX, body, "WHISPER", me())
end

function T.Send(verb, body)
    body = body or ""
    local head = "CMD\t" .. verb
    if body == "" then raw(head) return end
    if #head + 1 + #body <= T.MAX then raw(head .. "\t" .. body) return end
    local room = T.MAX - #head - 16
    local chunks = {}
    for i = 1, #body, room do chunks[#chunks + 1] = string.sub(body, i, i + room - 1) end
    for i, chunk in ipairs(chunks) do
        raw(head .. "\t#\t" .. i .. "\t" .. #chunks .. "\t" .. chunk)
    end
end

-- "<verb>\t#\t<seq>\t<count>\t<chunk...>" -> verb, seq, count, chunk (chunk keeps its tabs)
local function parseFragment(msg)
    local verb, seq, count, chunk = string.match(msg, "^([^\t]*)\t#\t(%d+)\t(%d+)\t?(.*)$")
    if verb then return verb, tonumber(seq), tonumber(count), chunk end
end

local function dispatch(verb, body)
    local h = handlers[verb]
    if not h then
        if ASC.Stock and ASC.Stock.Log and verb ~= "PONG" then ASC.Stock.Log("transport: unhandled " .. tostring(verb)) end
        return
    end
    local ok, err = pcall(h, body, verb)
    if not ok and ASC.Stock and ASC.Stock.Log then ASC.Stock.Log("transport " .. verb .. ": " .. tostring(err)) end
end

local frame = CreateFrame("Frame")
frame:RegisterEvent("CHAT_MSG_ADDON")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:SetScript("OnEvent", function(_, event, prefix, msg, channel, sender)
    if event == "PLAYER_ENTERING_WORLD" then
        -- the module's own login HELLO can race the loading screen; ask again
        if C_Timer and C_Timer.After then C_Timer.After(1, function() T.Send("HELLO") end) else T.Send("HELLO") end
        return
    end
    if prefix ~= T.PREFIX or sender ~= me() then return end
    if string.sub(msg, 1, 4) == "CMD\t" then return end -- our own line echoed (never, the module eats it)
    local verb, seq, count, chunk = parseFragment(msg)
    if verb then
        local slot = inbound[verb] or { parts = {}, count = count }
        inbound[verb] = slot
        slot.parts[seq] = chunk
        slot.count = count
        for i = 1, count do if slot.parts[i] == nil then return end end
        inbound[verb] = nil
        dispatch(verb, table.concat(slot.parts))
        return
    end
    local v, body = string.match(msg, "^([^\t]*)\t?(.*)$")
    dispatch(v, body)
end)

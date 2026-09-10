-- C_Hook.lua (stock-client replacement for Ascension's SharedXML\C_Hook.lua)
--
-- Ascension's original routes every call made from insecure code through a frame
-- attribute trampoline whose handler runs the same call again; on a stock client all
-- addon code is insecure, so that recurses until "C stack overflow". This version keeps
-- the public contract (Register / RegisterBucket / RegisterAllEvents / Unregister /
-- SendEvent / SendBlizzardEvent / IsRegistered / profiling no-ops, plus the fields
-- refs / events / buckets / allListener) and dispatches directly.
--
-- Callback contract (unchanged): function -> callback(...); table -> callback[event](callback, ...);
-- ref table + string callback -> ref[callback](ref, event, ...); ref table alone -> ref[event](ref, ...).
-- Every real frame event is re-broadcast as a C_Hook event; an addon whisper to yourself is
-- re-broadcast as an event named by its prefix (Ascension's own convention).
C_Hook = {
    refs = {},
    events = {},
    buckets = {},
    allListener = {},
}
local playerName = UnitName and UnitName("player")
local HookHandler = CreateFrame("Frame")

local function GetEventsTable(events)
    if type(events) == "string" then
        if events:find(",") then
            local t = {}
            for name in events:gmatch("[^,%s]+") do t[#t + 1] = name end
            events = t
        else
            events = { events }
        end
    end
    return events
end

function C_Hook:Register(ref, events, callback)
    assert(ref ~= nil, "Ref cannot be nil for C_Hook:Register")
    events = GetEventsTable(events)
    assert(type(events) == "table", "Events must be a comma separated string, or table of events for C_Hook:Register")
    if not self.refs[ref] then self.refs[ref] = {} end
    for _, event in ipairs(events) do
        self.refs[ref][event] = callback or true
        if not self.events[event] then self.events[event] = {} end
        self.events[event][ref] = callback or true
    end
end

function C_Hook:RegisterBucket(ref, events, period, callback)
    events = GetEventsTable(events)
    C_Hook:Register(ref, events, callback)
    self.buckets[ref] = self.buckets[ref] or {}
    for _, event in ipairs(events) do
        self.buckets[ref][event] = {}
        self.buckets[ref]["DURATION_" .. event] = period or 0.1
    end
end

function C_Hook:RegisterAllEvents(ref, callback)
    self.allListener[ref] = callback
end

function C_Hook:Unregister(ref, events)
    if not ref then return end
    if self.allListener[ref] then self.allListener[ref] = nil end
    if not self.refs[ref] then return end
    if events then
        events = GetEventsTable(events)
        for _, event in ipairs(events) do
            self.refs[ref][event] = nil
            if not next(self.refs[ref]) then self.refs[ref] = nil end
            if self.events[event] then
                self.events[event][ref] = nil
                if not next(self.events[event]) then self.events[event] = nil end
            end
            if self.buckets[ref] and self.buckets[ref][event] then
                self.buckets[ref]["DURATION_" .. event] = nil
                local timer = self.buckets[ref]["TIMER_" .. event]
                if timer then timer:Cancel(); self.buckets[ref]["TIMER_" .. event] = nil end
                self.buckets[ref][event] = nil
            end
            if not self.refs[ref] then break end
        end
    else
        for event in pairs(self.refs[ref]) do
            if self.events[event] then
                self.events[event][ref] = nil
                if not next(self.events[event]) then self.events[event] = nil end
            end
        end
        if self.buckets[ref] then
            for _, data in pairs(self.buckets[ref]) do
                if type(data) == "table" and data.Cancel then data:Cancel() end
            end
            self.buckets[ref] = nil
        end
        self.refs[ref] = nil
    end
end

local function report(ref, event, err)
    if C_Logger and C_Logger.Error then
        C_Logger.Error("%s %s %s", tostring(ref), tostring(event), tostring(err))
    elseif ASC and ASC.Stock and ASC.Stock.Log then
        ASC.Stock.Log(tostring(ref) .. " " .. tostring(event) .. " " .. tostring(err))
    end
end

local function HandleCallback(ref, event, callback, ...)
    if type(callback) == "function" then
        local ok, err = pcall(callback, ...)
        if not ok then report(ref, event, err) end
        return
    end
    if type(callback) == "table" then
        if callback[event] then
            local ok, err = pcall(callback[event], callback, ...)
            if not ok then report(ref, event, err) end
        end
        return
    end
    if type(ref) == "table" then
        if type(callback) == "string" then
            if ref[callback] then
                local ok, err = pcall(ref[callback], ref, event, ...)
                if not ok then report(ref, event, err) end
            end
            return
        end
        if ref[event] then
            local ok, err = pcall(ref[event], ref, ...)
            if not ok then report(ref, event, err) end
        end
    end
end

local function tcopy(t)
    local c = {}
    for i, v in ipairs(t) do c[i] = v end
    return c
end

local function SendBucketEvent(ref, event, callback, ...)
    local bucket = C_Hook.buckets[ref]
    if not bucket or not bucket[event] then return end
    table.insert(bucket[event], { ... })
    if bucket["TIMER_" .. event] then return end
    local period = bucket["DURATION_" .. event] or 0.1
    bucket["TIMER_" .. event] = C_Timer.NewTimer(period, function()
        bucket["TIMER_" .. event] = nil
        local data = tcopy(bucket[event])
        wipe(bucket[event])
        HandleCallback(ref, event, callback, data)
    end)
end

function C_Hook:StartProfiling() self.EVENT_TIMES = {} end
function C_Hook:StopProfiling() self.EVENT_TIMES = nil end
function C_Hook:DumpProfiling() end

function C_Hook:SendEvent(event, ...)
    if next(self.allListener) then
        for ref, callback in pairs(self.allListener) do
            HandleCallback(ref, event, callback, ...)
        end
    end
    local listeners = self.events[event]
    if not listeners then return end
    -- snapshot: a callback may (un)register while we iterate
    local snapshot = {}
    for ref, callback in pairs(listeners) do snapshot[#snapshot + 1] = { ref, callback } end
    for _, pair in ipairs(snapshot) do
        local ref, callback = pair[1], pair[2]
        if self.buckets[ref] and self.buckets[ref][event] then
            SendBucketEvent(ref, event, callback, ...)
        else
            HandleCallback(ref, event, callback, ...)
        end
    end
end

local function SendBlizzardEvent(frame, event, ...)
    local handler = frame.GetScript and frame:GetScript("OnEvent")
    if handler then
        local ok, err = pcall(handler, frame, event, ...)
        if not ok then report(frame, event, err) end
    end
end

function C_Hook:SendBlizzardEvent(event, ...)
    if not GetFramesRegisteredForEvent then return end
    local listeners = { GetFramesRegisteredForEvent(event) }
    for _, frame in ipairs(listeners) do
        SendBlizzardEvent(frame, event, ...)
    end
end

function C_Hook:IsRegistered(ref, event)
    if not ref then return false end
    if self.refs[ref] then
        if not event and next(self.refs[ref]) then return true end
        if event and self.refs[ref][event] ~= nil then return true end
    end
    return false
end

-- Real frame events are re-broadcast; addon whispers to yourself become events named by
-- their prefix (Ascension's own decoupled event bus), other senders are ignored.
HookHandler:SetScript("OnEvent", function(_, event, ...)
    if event == "PLAYER_ENTERING_WORLD" then
        PLAYER_ENTERED_WORLD = true
        playerName = playerName or (UnitName and UnitName("player"))
    end
    if event == "CHAT_MSG_ADDON" then
        local prefix, _, channel, sender = ...
        if sender == playerName and channel == "WHISPER" then
            C_Hook:SendEvent(prefix, select(2, ...))
        end
    else
        C_Hook:SendEvent(event, ...)
    end
end)
HookHandler:RegisterAllEvents()

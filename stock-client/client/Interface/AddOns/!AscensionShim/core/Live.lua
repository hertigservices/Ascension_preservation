-- core/Live.lua: server-backed Character Advancement (P3).
--
-- Loads after the preview API. When the module answers HELLO with enabled=1 the STATE it
-- sends installs these functions over the read-only preview: the pending-build model the
-- original panel expects (AddByEntryID/RemoveByEntryID stage ranks locally, the panel
-- shows IsPending/remaining points, ApplyPendingBuild uploads the COMPLETE set and the
-- server answers RESULT + a fresh STATE). Rules mirror the module's (CoA model: one point
-- per rank, the Class tab spends AE, the active specialization's tab spends TE,
-- RequiredIDs are prerequisites, RequiredLevel and the Required*Investment columns gate).
ASC.Live = ASC.Live or {}
local Live = ASC.Live
local T = ASC.Transport
local CA = C_CharacterAdvancement
local Preview = ASC.Preview

local state = { connected = false, classByte = nil, level = 0, spec = 0, ae = 0, te = 0, known = {}, pending = {} }
Live.State = state
local preview = {} -- the preview functions we replace, kept for fallback

local function log(text) if ASC.Stock and ASC.Stock.Log then ASC.Stock.Log("live: " .. text) end end
local function fire(event, ...) ASC.Events.Fire(event, ...) end
local function entry(id) return ASC.Data.GetEntry(id) end
local function copy(t) local c = {} for k, v in pairs(t) do c[k] = v end return c end
local function identity() local c = Preview and Preview.Context return c and c.identity end
local function isClassTab(e) return e.Tab == "Class" end
local function specTab()
    local info = state.spec and state.spec ~= 0 and C_ClassInfo.GetSpecInfoByID(state.spec)
    return info and string.upper(info.Spec) or nil
end
local function inActiveSpec(e) local t = specTab() return t ~= nil and string.upper(e.Tab or "") == t end
local function maxRank(e) return e and math.max(1, #(e.Spells or {})) or 0 end

local function points(set, filter)
    local n = 0
    for id, r in pairs(set) do
        local e = entry(id)
        if e and r > 0 and (not filter or filter(e)) then n = n + r end
    end
    return n
end
local function spent(set)
    return points(set, isClassTab), points(set, function(e) return not isClassTab(e) end)
end

local function prereqsMet(e, set)
    for _, req in ipairs(e.RequiredIDs or {}) do
        if entry(req) and (set[req] or 0) < 1 then return false, req end
    end
    return true
end

local function investmentMet(id, e, set)
    local tab = points(set, function(x) return x.Tab == e.Tab and x.ID ~= id end)
    local class = points(set, function(x) return x.Class == e.Class and x.ID ~= id end)
    local function need(v) return type(v) == "number" and v > 0 end
    if need(e.RequiredTabAEInvestment) and tab < e.RequiredTabAEInvestment then return false end
    if need(e.RequiredTabTEInvestment) and tab < e.RequiredTabTEInvestment then return false end
    if need(e.RequiredClassAEInvestment) and class < e.RequiredClassAEInvestment then return false end
    if need(e.RequiredClassTEInvestment) and class < e.RequiredClassTEInvestment then return false end
    if need(e.RequiredClassPoints) and class < e.RequiredClassPoints then return false end
    return true
end

local function parseSet(list)
    local set = {}
    for id, rank in string.gmatch(list or "", "(%d+):(%d+)") do set[tonumber(id)] = tonumber(rank) end
    return set
end
local function encodeSet(set)
    local parts = {}
    for id, r in pairs(set) do if r > 0 then parts[#parts + 1] = id .. ":" .. r end end
    table.sort(parts)
    return table.concat(parts, ",")
end

----------------------------------------------------------------------------------------
-- The API the panel calls (contracts from analysis/*.json)
----------------------------------------------------------------------------------------
local api = {}
function api.IsKnownID(id) return (state.known[id] or 0) > 0 end
function api.GetPendingRankByEntryID(id) return state.pending[id] or 0, maxRank(entry(id)) end
function api.GetTalentRankByID(id) return state.known[id] or 0, maxRank(entry(id)) end
function api.GetTalentRankBySpellID(spell)
    local e = CA.GetEntryBySpellID(spell)
    if not e then return 0, 0 end
    return api.GetTalentRankByID(e.ID)
end
function api.IsPending()
    for id, r in pairs(state.pending) do if (state.known[id] or 0) ~= r then return true end end
    for id, r in pairs(state.known) do if (state.pending[id] or 0) ~= r then return true end end
    return false
end
function api.GetPendingRemainingAE() local a = spent(state.pending) return state.ae - a end
function api.GetPendingRemainingTE() local _, t = spent(state.pending) return state.te - t end
function api.GetRemainingAE() local a = spent(state.known) return state.ae - a end
function api.GetRemainingTE() local _, t = spent(state.known) return state.te - t end
function api.GetLearnedAE() local a = spent(state.known) return a end
function api.GetPendingTabAEInvestment(className, tabName)
    return points(state.pending, function(e) return e.Class == className and e.Tab == tabName end)
end
api.GetPendingTabTEInvestment = api.GetPendingTabAEInvestment
function api.GetActiveChrSpec() return state.spec ~= 0 and state.spec or nil end
function api.MeetsInvestmentForAddByEntryID(id)
    local e = entry(id)
    return e ~= nil and investmentMet(id, e, state.pending)
end
function api.IsConnectionAllowed(from, to)
    return (state.pending[from] or 0) > 0 and (state.pending[to] or 0) > 0
end
function api.CanAddByEntryID(id, n)
    n = n or 1
    local e = entry(id)
    if not e then return false, "unknown-entry" end
    if not state.connected then return false, "ASC_OFFLINE" end
    local ident = identity()
    if ident and e.Class ~= ident.CAClassName then return false, "other-class" end
    if not isClassTab(e) and not inActiveSpec(e) then return false, "other-specialization" end
    if (state.pending[id] or 0) + n > maxRank(e) then return false, "max-rank" end
    if (e.RequiredLevel or 0) > (state.level or 0) then return false, "level" end
    if not prereqsMet(e, state.pending) then return false, "prerequisite" end
    if not investmentMet(id, e, state.pending) then return false, "investment" end
    if isClassTab(e) then
        if api.GetPendingRemainingAE() < n then return false, "no-class-points" end
    elseif api.GetPendingRemainingTE() < n then return false, "no-spec-points" end
    return true
end
function api.AddByEntryID(id, n)
    local ok, why = api.CanAddByEntryID(id, n)
    if not ok then log("add " .. tostring(id) .. " refused: " .. tostring(why)) return false, why end
    state.pending[id] = (state.pending[id] or 0) + (n or 1)
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
    return true
end
function api.CanRemoveByEntryID(id)
    local r = state.pending[id] or 0
    if r < 1 then return false, "not-pending" end
    if r == 1 then
        for other, orank in pairs(state.pending) do
            local oe = orank > 0 and other ~= id and entry(other)
            if oe then
                for _, req in ipairs(oe.RequiredIDs or {}) do
                    if req == id then return false, "dependents" end
                end
            end
        end
    end
    return true
end
function api.RemoveByEntryID(id)
    local ok, why = api.CanRemoveByEntryID(id)
    if not ok then return false, why end
    local r = state.pending[id] - 1
    state.pending[id] = r > 0 and r or nil
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
    return true
end
function api.ClearPendingBuild()
    for id in pairs(state.pending) do state.pending[id] = nil end
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
end
function api.ClearPendingBuildByTab(className, tabName)
    for id in pairs(state.pending) do
        local e = entry(id)
        if e and e.Class == className and e.Tab == tabName then state.pending[id] = nil end
    end
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
end
function api.CancelPendingBuild()
    state.pending = copy(state.known)
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
end
-- canApply, reason, traversalError, entryID, entryRank, marksCost, goldCost
function api.CanApplyPendingBuild()
    if not state.connected then return false, "ASC_OFFLINE", nil, nil, nil, 0, 0 end
    if not api.IsPending() then return false, "CA_NO_PENDING_CHANGES", nil, nil, nil, 0, 0 end
    for id, r in pairs(state.pending) do
        local e = r > 0 and entry(id)
        if e then
            local ok, req = prereqsMet(e, state.pending)
            if not ok then return false, "CA_MISSING_PREREQUISITE", nil, req, 1, 0, 0 end
        end
    end
    return true, "", nil, nil, nil, 0, 0
end
function api.ApplyPendingBuild()
    local ok, reason = api.CanApplyPendingBuild()
    if not ok then fire("CHARACTER_ADVANCEMENT_UPDATE_ENTRIES_RESULT", false, reason) return false, reason end
    T.Send("APPLY", encodeSet(state.pending))
    return true
end
function api.LearnID(ids)
    if type(ids) ~= "table" then ids = { ids } end
    for _, id in ipairs(ids) do api.AddByEntryID(id, 1) end
    return api.ApplyPendingBuild()
end
function api.UnlearnID(id)
    if not api.RemoveByEntryID(id) then return false end
    return api.ApplyPendingBuild()
end
function api.UnlearnAllTalents() T.Send("RESET", "talents") end
function api.UnlearnAllSpells() T.Send("RESET", "all") end
function api.CanSwitchActiveChrSpec(id)
    local info = C_ClassInfo.GetSpecInfoByID(id)
    local ident = identity()
    return info ~= nil and ident ~= nil and info.Class == ident.Token
end
function api.SwitchActiveChrSpec(id)
    if not api.CanSwitchActiveChrSpec(id) then return false, "invalid-spec" end
    T.Send("SPEC", tostring(id))
    return true
end
function api.ShouldConfirmLearnID() return false end
function api.ShouldConfirmUnlearnID() return false end
function api.ShouldConfirmUnlearnAllTalents() return true end
function api.ShouldConfirmUnlearnAllSpells() return true end
function api.IsTalentID(id) local e = entry(id) return e ~= nil and e.Type == "Talent" end
function api.IsAbilityID(id) local e = entry(id) return e ~= nil and e.Type == "Ability" end
function api.IsTalentAbilityID() return false end
function api.GetAbilityEssenceCost(spell) local e = CA.GetEntryBySpellID(spell) return (e and isClassTab(e)) and 1 or 0 end
function api.GetTalentEssenceCost(spell) local e = CA.GetEntryBySpellID(spell) return (e and not isClassTab(e)) and 1 or 0 end

function Live.Install()
    if Live.installed then return end
    for name, fn in pairs(api) do
        preview[name] = CA[name]
        CA[name] = fn
    end
    Live.installed = true
    log("server-backed Character Advancement installed")
end

----------------------------------------------------------------------------------------
-- Wire handlers
----------------------------------------------------------------------------------------
T.On("HELLO", function(body)
    local protocol, mode, enabled = string.match(body, "^(%d+)\t([^\t]*)\t(%d)")
    Live.protocol, Live.mode = tonumber(protocol), mode
    if enabled == "1" then T.Send("STATE") else log("module disabled (mode " .. tostring(mode) .. ")") end
end)

-- The glue chooser cannot talk to the world server, and stock 3.3.5 Lua cannot register a
-- CVar; the one in-process mailbox from glue Lua to world Lua is a registered string CVar
-- nobody reads while voice chat is off. The chooser writes "ASC:<name>:<classByte>:<uuid>"
-- there; the first STATE with chosen=0 consumes it for the matching character.
local MAILBOX = "Sound_VoiceChatInputDriverName"
local function consumeMailbox()
    local ok, value = pcall(GetCVar, MAILBOX)
    if not ok or type(value) ~= "string" then return end
    local name, classByte, uuid = string.match(value, "^ASC:([^:]+):(%d+):?([%x%-]*)$")
    if not name then return end
    pcall(SetCVar, MAILBOX, "System Default")
    if name ~= UnitName("player") then log("mailbox is for " .. name .. ", ignored") return end
    T.Send("CLASS", classByte .. (uuid ~= "" and ("\t" .. uuid) or ""))
    log("sent CLASS " .. classByte .. " " .. uuid)
    return true
end
Live.ConsumeMailbox = consumeMailbox

-- STATE\t<classByte>\t<specId>\t<ae>\t<te>\t<level>\t<chosen>\t<id:rank,...>
T.On("STATE", function(body)
    local classByte, spec, ae, te, level, chosen, list = string.match(body, "^(%d+)\t(%d+)\t(%d+)\t(%d+)\t(%d+)\t(%d)\t?(.*)$")
    if not classByte then log("bad STATE: " .. string.sub(body, 1, 60)) return end
    classByte, spec, ae, te, level = tonumber(classByte), tonumber(spec), tonumber(ae), tonumber(te), tonumber(level)
    state.chosen = chosen == "1"
    if not state.chosen and not state.mailboxTried then
        state.mailboxTried = true
        if consumeMailbox() then return end -- the CLASS reply brings a fresh STATE
    end
    local config = ASC.Config
    local ctx = Preview.Context
    if not ctx or ctx.identity.ID ~= classByte or ctx.level ~= level then
        -- Preview.Begin asserts on a withheld essence row (family 28 at level 1 is 0/0), and
        -- the identity it selects is what the rest of the API filters by; retry at levels
        -- that do have a budget, the server's own budget below is authoritative anyway.
        local ok = pcall(Preview.Begin, config.dataset, classByte, level)
        if not ok then
            for _, l in ipairs({ 80, 70, 60, 50, 40, 30, 20, 10 }) do
                if pcall(Preview.Begin, config.dataset, classByte, l) then ok = true break end
            end
        end
        if not ok then log("no class identity for byte " .. classByte) return end
        ctx = Preview.Context
    end
    ctx.level, ctx.ae, ctx.te = level, ae, te
    local specChanged = state.spec ~= spec
    state.classByte, state.spec, state.ae, state.te, state.level = classByte, spec, ae, te, level
    state.known = parseSet(list)
    state.pending = copy(state.known)
    state.connected = true
    Live.Install()
    if specChanged then fire("ASCENSION_CA_SPECIALIZATION_ACTIVE_ID_CHANGED", spec) end
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
    local n = 0
    for _ in pairs(state.known) do n = n + 1 end
    log("state: class " .. classByte .. " spec " .. spec .. " level " .. level .. " AE " .. ae .. " TE " .. te .. " known " .. n)
end)

-- RESULT\t<verb>\t<OK|ERR>[\t<reason>[\t<entry>]]
T.On("RESULT", function(body)
    local verb, status, reason, id = string.match(body, "^([^\t]*)\t([^\t]*)\t?([^\t]*)\t?(.*)$")
    local ok = status == "OK"
    if verb == "APPLY" then
        fire("CHARACTER_ADVANCEMENT_UPDATE_ENTRIES_RESULT", ok, reason, tonumber(id))
    elseif verb == "RESET" then
        fire("CHARACTER_ADVANCEMENT_PURGE_TALENTS_RESULT", ok, reason)
    end
    if not ok then
        log(verb .. " refused: " .. tostring(reason) .. " " .. tostring(id))
        if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage("|cffff4040Character Advancement:|r " .. verb .. " refused (" .. tostring(reason) .. ")") end
    end
end)

T.On("ERROR", function(body) log("server error: " .. tostring(body)) end)
T.On("PONG", function() end)

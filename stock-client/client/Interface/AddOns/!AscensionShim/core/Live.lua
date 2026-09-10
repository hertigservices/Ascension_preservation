-- core/Live.lua: server-backed Character Advancement (P3 CoA, P6 Free-Pick).
--
-- Every C_CharacterAdvancement function the two original panels call is defined here, at load,
-- over one state table: the server's known set (STATE), the client-side pending build the panels
-- edit, and the budgets the server sends. Until the module's first STATE arrives the state is
-- empty and every mutation is refused with ASC_OFFLINE.
--
-- Two rule sets, chosen by the mode the module announces in HELLO (mirrored from the module,
-- server/mod-ascension-ca/src/AscensionCA.cpp, which stays authoritative):
--   coa       the character is a CoA class; one point per rank; the "Class" tab spends AE, the
--             active specialization's tab spends TE; RequiredIDs, RequiredLevel and the
--             Required*Investment columns gate.
--   freepick  the character is a Hero (class byte 10) picking from CA class types 1..12 (the ten
--             stock classes, General, None); abilities cost AECost, talents TECost per rank;
--             talent-tree parents (ConnectedNodes) must be known; masteries and implicit traits
--             are granted free once the class has enough points (AE + TE spent in that class).
-- Reason strings are Ascension's Enum.CALearnResult / CAUnlearnResult keys.
ASC.Live = ASC.Live or {}
local Live = ASC.Live
local T = ASC.Transport
local CA = C_CharacterAdvancement
local Preview = ASC.Preview
local Data = ASC.Data

local CLASSIC_TYPE_MAX = 12
local FLAG_HIDDEN = 0x800
local FLAG_MASTERY = 0x1000
local FLAG_TRAIT = 0x400000

local state = { connected = false, mode = nil, classByte = nil, level = 0, spec = 0, ae = nil, te = nil,
                known = {}, pending = {}, auto = {}, filtered = {}, chosen = false }
Live.State = state
local inspectCallbacks = {}

local function log(text) if ASC.Stock and ASC.Stock.Log then ASC.Stock.Log("live: " .. text) end end
local function fire(event, ...) ASC.Events.Fire(event, ...) end
local function entry(id) return id and Data.GetEntry(id) or nil end
local function copy(t) local c = {} for k, v in pairs(t) do c[k] = v end return c end
local function identity() local c = Preview and Preview.Context return c and c.identity end
local function hero() return state.mode == "freepick" end
local function flag(e, f) return bit.band(e.Flags or 0, f) ~= 0 end
local function isTalent(e) return e.Type == "Talent" end
local function isTalentLike(e) return e.Type == "Talent" or e.Type == "TalentAbility" end
local function isClassTab(e) return e.Tab == "Class" end
local function maxRank(e) return e and math.max(1, #(e.Spells or {})) or 0 end
local function previewMode() return C_CVar and C_CVar.GetBool and C_CVar.GetBool("previewCharacterAdvancementChanges") end
local function upper(s) return string.upper(s or "") end

local function specTab()
    local info = state.spec and state.spec ~= 0 and C_ClassInfo.GetSpecInfoByID(state.spec)
    return info and upper(info.Spec) or nil
end
local function inActiveSpec(e) local t = specTab() return t ~= nil and upper(e.Tab) == t end

local function inPool(e)
    if hero() then return (e.ClassTypeID or 0) >= 1 and e.ClassTypeID <= CLASSIC_TYPE_MAX end
    local ident = identity()
    return ident == nil or e.Class == ident.CAClassName
end
local function aeCost(e)
    if hero() then return isTalent(e) and 0 or (e.AECost or 0) end
    return isClassTab(e) and 1 or 0
end
local function teCost(e)
    if hero() then return isTalent(e) and (e.TECost or 0) or 0 end
    return isClassTab(e) and 0 or 1
end
local function budgetAE() if state.ae then return state.ae end local c = Preview.Context return c and c.ae or 0 end
local function budgetTE() if state.te then return state.te end local c = Preview.Context return c and c.te or 0 end

----------------------------------------------------------------------------------------
-- Totals and free (auto-granted) entries -- the same arithmetic as the module
----------------------------------------------------------------------------------------
local function classPointsOf(set, className, freeIds)
    local n = 0
    for id, r in pairs(set) do
        local e = entry(id)
        if e and r > 0 and e.Class == className and not (freeIds and freeIds[id]) then n = n + (aeCost(e) + teCost(e)) * r end
    end
    return n
end
local function isAutoGranted(e, set, freeIds)
    if not hero() then return false end
    if (e.RequiredLevel or 0) > (state.level or 0) then return false end
    local points = classPointsOf(set, e.Class, freeIds)
    if flag(e, FLAG_TRAIT) then return points >= (e.RequiredClassPoints or 0) end
    for _, m in ipairs(e.Masteries or {}) do
        if (set[m] or 0) >= 1 then return points >= (e.RequiredClassPoints or 0) end
    end
    return false
end
local function freeIdsOf(set)
    if not hero() then return {} end
    local first = {}
    for id, r in pairs(set) do local e = entry(id) if e and r > 0 and isAutoGranted(e, set, nil) then first[id] = true end end
    local again = {}
    for id, r in pairs(set) do local e = entry(id) if e and r > 0 and isAutoGranted(e, set, first) then again[id] = true end end
    return again
end
local function totals(set)
    local free = freeIdsOf(set)
    local t = { ae = 0, te = 0, classPts = {}, classAe = {}, classTe = {}, tabAe = {}, tabTe = {}, free = free }
    for id, r in pairs(set) do
        local e = entry(id)
        if e and r > 0 and not free[id] then
            local a, b = aeCost(e) * r, teCost(e) * r
            t.ae, t.te = t.ae + a, t.te + b
            local c, k = e.Class, e.Class .. "|" .. upper(e.Tab)
            t.classPts[c] = (t.classPts[c] or 0) + a + b
            t.classAe[c] = (t.classAe[c] or 0) + a
            t.classTe[c] = (t.classTe[c] or 0) + b
            t.tabAe[k] = (t.tabAe[k] or 0) + a
            t.tabTe[k] = (t.tabTe[k] or 0) + b
        end
    end
    return t
end
-- Free-Pick: everything the picks unlock for free joins the pending set at rank 1, and the
-- auto entries that no longer qualify leave it, so the panel previews what the server will grant.
local function refreshAuto()
    if not hero() then return end
    for id in pairs(state.auto) do
        if not (state.known[id] and state.known[id] > 0) then state.pending[id] = nil end
    end
    state.auto = {}
    local t = totals(state.pending)
    local d = Data.Datasets[Data.ActiveKey]
    for id, e in pairs(d and d.entries or {}) do
        if inPool(e) and not (state.pending[id] and state.pending[id] > 0) and isAutoGranted(e, state.pending, t.free) then
            state.pending[id] = 1
            state.auto[id] = true
        end
    end
end
local function isFreeIn(set, id)
    if not hero() then return false end
    return freeIdsOf(set)[id] == true
end

local function prereqsMet(e, set)
    for _, req in ipairs(e.RequiredIDs or {}) do
        if entry(req) and (set[req] or 0) < 1 then return false, req end
    end
    return true
end
local function parentsMet(e, set)
    if not hero() or not isTalentLike(e) then return true end
    for _, parent in ipairs(e.ConnectedNodes or {}) do
        if entry(parent) and (set[parent] or 0) < 1 then return false, parent end
    end
    return true
end
-- investment gates for entry e at `rank` inside `set`, excluding e's own spend
local function investmentMet(e, rank, set)
    local t = totals(set)
    local a, b = aeCost(e) * rank, teCost(e) * rank
    local tab = e.Class .. "|" .. upper(e.Tab)
    local function need(v) return type(v) == "number" and v > 0 end
    local function have(m, k) return (m[k] or 0) end
    if need(e.RequiredAEInvestment) and t.ae - a < e.RequiredAEInvestment then return false, "CA_LEARN_NOT_ENOUGH_INVESTED_AE" end
    if need(e.RequiredTEInvestment) and t.te - b < e.RequiredTEInvestment then return false, "CA_LEARN_NOT_ENOUGH_INVESTED_TE" end
    if need(e.RequiredTabAEInvestment) and have(t.tabAe, tab) - a < e.RequiredTabAEInvestment then return false, "CA_LEARN_NOT_ENOUGH_INVESTED_AE" end
    if need(e.RequiredTabTEInvestment) and have(t.tabTe, tab) - b < e.RequiredTabTEInvestment then return false, "CA_LEARN_NOT_ENOUGH_INVESTED_TE" end
    if need(e.RequiredClassAEInvestment) and have(t.classAe, e.Class) - a < e.RequiredClassAEInvestment then return false, "CA_LEARN_NOT_ENOUGH_INVESTED_AE" end
    if need(e.RequiredClassTEInvestment) and have(t.classTe, e.Class) - b < e.RequiredClassTEInvestment then return false, "CA_LEARN_NOT_ENOUGH_INVESTED_TE" end
    if need(e.RequiredClassPoints) and have(t.classPts, e.Class) - a - b < e.RequiredClassPoints then return false, "CA_LEARN_CONDITIONS_FAILED" end
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
-- Entry lists the panels draw
----------------------------------------------------------------------------------------
local function byLevelName(a, b)
    if (a.RequiredLevel or 0) ~= (b.RequiredLevel or 0) then return (a.RequiredLevel or 0) < (b.RequiredLevel or 0) end
    return (a.Name or "") < (b.Name or "")
end
local function byRowColumn(a, b)
    if (a.Row or 0) ~= (b.Row or 0) then return (a.Row or 0) < (b.Row or 0) end
    return (a.Column or 0) < (b.Column or 0)
end
local function inTab(e, classDBC, specDBC)
    if e.Class ~= classDBC then return false end
    if specDBC == nil or specDBC == "None" or specDBC == "" then return true end
    return upper(e.Tab) == upper(specDBC)
end
local function collect(classDBC, specDBC, includeHidden, pred, sorter)
    local out = {}
    for _, e in ipairs(Data.GetEntries(classDBC)) do
        if inTab(e, classDBC, specDBC) and (includeHidden or not flag(e, FLAG_HIDDEN)) and pred(e) then out[#out + 1] = e end
    end
    table.sort(out, sorter)
    return out
end

----------------------------------------------------------------------------------------
-- The API
----------------------------------------------------------------------------------------
local api = {}
-- identity / entries
function api.GetAllEntries() return Data.GetEntries() end
function api.GetEntryByInternalID(id) return entry(id) end
function api.GetEntriesByClass(classDBC, tab) return Data.GetEntries(classDBC, tab) end
function api.GetEntryBySpellID(spell)
    local candidates = Data.GetEntriesForSpell(spell)
    for _, e in ipairs(candidates) do if inPool(e) then return e end end
    return candidates[1]
end
function api.GetSpellsByClass(classDBC, specDBC, includeHidden)
    return collect(classDBC, specDBC, includeHidden, function(e) return e.Type == "Ability" and not flag(e, FLAG_MASTERY) and not flag(e, FLAG_TRAIT) end, byLevelName)
end
function api.GetTalentsByClass(classDBC, specDBC, includeHidden)
    return collect(classDBC, specDBC, includeHidden, function(e) return isTalentLike(e) and not flag(e, FLAG_TRAIT) end, byRowColumn)
end
function api.GetMasteriesByClass(classDBC, specDBC)
    return collect(classDBC, specDBC, false, function(e) return flag(e, FLAG_MASTERY) end, byLevelName)
end
function api.GetImplicitByClass(classDBC, specDBC)
    return collect(classDBC, specDBC, false, function(e) return flag(e, FLAG_TRAIT) end, byLevelName)
end
function api.GetKnownSpellEntriesForClass(classDBC)
    local out = {}
    for id, r in pairs(state.known) do local e = entry(id) if e and r > 0 and e.Class == classDBC and not isTalentLike(e) then out[#out + 1] = e end end
    table.sort(out, byLevelName)
    return out
end
-- Every spell the character knows through Character Advancement, at its current rank
-- (Hero Architect's "Import current build" walks this list).
function api.GetKnownSpells()
    local out = {}
    for id, r in pairs(state.known) do
        local e = entry(id)
        if e and r > 0 and e.Spells then
            local spell = e.Spells[r] or e.Spells[1]
            if spell then out[#out + 1] = spell end
        end
    end
    table.sort(out)
    return out
end
function api.GetKnownTalentEntriesForClass(classDBC)
    local out = {}
    for id, r in pairs(state.known) do local e = entry(id) if e and r > 0 and e.Class == classDBC and isTalentLike(e) then out[#out + 1] = e end end
    table.sort(out, byLevelName)
    return out
end
function api.GetClassInfo(spellID)
    local e = api.GetEntryBySpellID(spellID)
    if not e or not CharacterAdvancementUtil then return nil end
    return CharacterAdvancementUtil.GetClassFileForEntry(e)
end
-- type predicates
function api.IsTalentID(id) local e = entry(id) return e ~= nil and e.Type == "Talent" end
function api.IsAbilityID(id) local e = entry(id) return e ~= nil and e.Type == "Ability" end
function api.IsTalentAbilityID(id) local e = entry(id) return e ~= nil and e.Type == "TalentAbility" end
function api.IsTalentSpellID(spell) local e = api.GetEntryBySpellID(spell) return e ~= nil and e.Type == "Talent" end
function api.IsTalentAbilitySpellID(spell) local e = api.GetEntryBySpellID(spell) return e ~= nil and e.Type == "TalentAbility" end
function api.IsMastery(spell) local e = api.GetEntryBySpellID(spell) return e ~= nil and flag(e, FLAG_MASTERY) end
-- quality
-- The native returned the quality NAME and Ascension's GlobalOverwrites wrapper mapped it to
-- Enum.SpellQuality; the wrapper is not in the pack, so the number is returned here directly
-- (SpellIconTemplateMixin:SetQuality compares it numerically).
function api.GetQualityInfo(spell)
    local e = api.GetEntryBySpellID(spell)
    if not e then return nil, 0 end
    local n = 0
    for id, r in pairs(state.known) do local k = entry(id) if k and r > 0 and k.Quality == e.Quality then n = n + 1 end end
    local quality = Enum and Enum.SpellQuality and Enum.SpellQuality[e.Quality]
    return quality or 1, n
end
function api.GetQualityCount(quality)
    local name = type(quality) == "number" and Enum and Enum.QualityToCAQuality and Enum.QualityToCAQuality[quality] or quality
    local n = 0
    for id, r in pairs(state.known) do local k = entry(id) if k and r > 0 and k.Quality == name then n = n + 1 end end
    return n
end
function api.GetQualityLimit() return nil end
-- known / pending
function api.IsKnownID(id) return (state.known[id] or 0) > 0 end
function api.IsKnownSpellID(spell)
    local e = api.GetEntryBySpellID(spell)
    if not e then return false end
    for i = 1, (state.known[e.ID] or 0) do if e.Spells[i] == spell then return true end end
    return false
end
function api.IsPendingEntryID(id) return (state.pending[id] or 0) > 0 and (state.pending[id] or 0) ~= (state.known[id] or 0) end
function api.IsPendingBuildAvailable() return state.connected end
function api.IsPending()
    for id, r in pairs(state.pending) do if (state.known[id] or 0) ~= r then return true end end
    for id, r in pairs(state.known) do if (state.pending[id] or 0) ~= r then return true end end
    return false
end
function api.GetPendingRankByEntryID(id) return state.pending[id] or 0, maxRank(entry(id)) end
function api.GetTalentRankByID(id) return state.known[id] or 0, maxRank(entry(id)) end
function api.GetTalentRankBySpellID(spell)
    local e = api.GetEntryBySpellID(spell)
    if not e then return 0, 0 end
    return api.GetTalentRankByID(e.ID)
end
function api.KnowsConnectedNodesFor(id) local e = entry(id) return e ~= nil and parentsMet(e, state.pending) end
function api.IsConnectionAllowed(from, to) return (state.pending[from] or 0) > 0 and (state.pending[to] or 0) > 0 end
-- budgets and investment (pending twins are what the panels read while previewCharacterAdvancementChanges is on)
function api.GetPendingRemainingAE() return budgetAE() - totals(state.pending).ae end
function api.GetPendingRemainingTE() return budgetTE() - totals(state.pending).te end
function api.GetRemainingAE() return budgetAE() - totals(state.known).ae end
function api.GetRemainingTE() return budgetTE() - totals(state.known).te end
function api.GetExpectedAE(level)
    local ae = Data.GetBudget(state.classByte or (identity() and identity().ID) or 10, level or state.level or 1)
    return ae or 0
end
local function learned(set, classDBC, specDBC, what)
    if type(classDBC) == "number" then return 0 end -- DBC ids (enchant requirements) are not part of the recovered data
    local t = totals(set)
    if not classDBC then return what == "ae" and t.ae or t.te end
    if not specDBC or specDBC == "None" then return (what == "ae" and t.classAe or t.classTe)[classDBC] or 0 end
    return (what == "ae" and t.tabAe or t.tabTe)[classDBC .. "|" .. upper(specDBC)] or 0
end
function api.GetLearnedAE(classDBC, specDBC) return learned(state.known, classDBC, specDBC, "ae") end
function api.GetLearnedTE(classDBC, specDBC) return learned(state.known, classDBC, specDBC, "te") end
function api.GetPendingGlobalAEInvestment() return totals(state.pending).ae end
function api.GetPendingGlobalTEInvestment() return totals(state.pending).te end
function api.GetGlobalAEInvestment() return previewMode() and api.GetPendingGlobalAEInvestment() or totals(state.known).ae end
function api.GetGlobalTEInvestment() return previewMode() and api.GetPendingGlobalTEInvestment() or totals(state.known).te end
function api.GetPendingTabTEInvestment(classDBC, specDBC) return learned(state.pending, classDBC, specDBC, "te") end
function api.GetPendingTabAEInvestment(classDBC, specDBC) return learned(state.pending, classDBC, specDBC, "ae") end
function api.GetTabTEInvestment(classDBC, specDBC) return previewMode() and api.GetPendingTabTEInvestment(classDBC, specDBC) or learned(state.known, classDBC, specDBC, "te") end
function api.GetTabAEInvestment(classDBC, specDBC) return previewMode() and api.GetPendingTabAEInvestment(classDBC, specDBC) or learned(state.known, classDBC, specDBC, "ae") end
function api.GetPendingClassPointInvestment(classDBC) return totals(state.pending).classPts[classDBC] or 0 end
function api.GetClassPointInvestment(classDBC) return previewMode() and api.GetPendingClassPointInvestment(classDBC) or (totals(state.known).classPts[classDBC] or 0) end
function api.GetAbilityEssenceCost(spell) local e = api.GetEntryBySpellID(spell) return e and aeCost(e) or 0 end
function api.GetTalentEssenceCost(spell) local e = api.GetEntryBySpellID(spell) return e and teCost(e) or 0 end
function api.MeetsInvestmentForAddByEntryID(id)
    local e = entry(id)
    return e ~= nil and investmentMet(e, (state.pending[id] or 0) + 1, state.pending)
end
-- learning rules
function api.CanAddByEntryID(id, n)
    n = n or 1
    local e = entry(id)
    if not e then return false, "CA_LEARN_UNKNOWN" end
    if not state.connected then return false, "ASC_OFFLINE" end
    if not inPool(e) then return false, "CA_LEARN_WRONG_CLASS" end
    if not hero() and not isClassTab(e) and not inActiveSpec(e) then return false, "CA_LEARN_WRONG_CLASS" end
    if flag(e, FLAG_TRAIT) then return false, "CA_LEARN_DISPLAY_ENTRY" end
    if (state.pending[id] or 0) + n > maxRank(e) then return false, "CA_LEARN_ALREADY_KNOWN" end
    if (e.RequiredLevel or 0) > (state.level or 0) then return false, "CA_LEARN_LOW_LEVEL" end
    if not prereqsMet(e, state.pending) then return false, "CA_LEARN_MISSING_REQUIRED_ID" end
    if not parentsMet(e, state.pending) then return false, "CA_LEARN_MISSING_CONNECTED_ENTRIES" end
    local trial = copy(state.pending)
    trial[id] = (trial[id] or 0) + n
    if isFreeIn(trial, id) then return true end
    local ok, why = investmentMet(e, trial[id], trial)
    if not ok then return false, why end
    local t = totals(trial)
    if t.ae > budgetAE() then return false, "CA_LEARN_MISSING_AE" end
    if t.te > budgetTE() then return false, "CA_LEARN_MISSING_TE" end
    return true
end
function api.CanLearnID(id) return api.CanAddByEntryID(id, 1) end
function api.AddByEntryID(id, n)
    local ok, why = api.CanAddByEntryID(id, n)
    if not ok then log("add " .. tostring(id) .. " refused: " .. tostring(why)) return false, why end
    state.pending[id] = (state.pending[id] or 0) + (n or 1)
    state.auto[id] = nil
    refreshAuto()
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
    return true
end
function api.CanRemoveByEntryID(id)
    local r = state.pending[id] or 0
    if r < 1 then return false, "CA_UNLEARN_NOT_KNOWN" end
    if state.auto[id] or isFreeIn(state.pending, id) then return false, "CA_UNLEARN_MASTERY_ID" end
    if r == 1 then
        for other, orank in pairs(state.pending) do
            local oe = orank > 0 and other ~= id and entry(other)
            if oe then
                for _, req in ipairs(oe.RequiredIDs or {}) do if req == id then return false, "CA_UNLEARN_MISSING_REQUIRED_ID" end end
                if hero() and isTalentLike(oe) then
                    for _, parent in ipairs(oe.ConnectedNodes or {}) do if parent == id then return false, "CA_UNLEARN_MISSING_CONNECTED_ENTRIES" end end
                end
            end
        end
    end
    return true
end
function api.CanUnlearnID(id) return api.CanRemoveByEntryID(id) end
function api.RemoveByEntryID(id)
    local ok, why = api.CanRemoveByEntryID(id)
    if not ok then return false, why end
    local r = state.pending[id] - 1
    state.pending[id] = r > 0 and r or nil
    refreshAuto()
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
    return true
end
function api.ClearPendingBuild()
    for id in pairs(state.pending) do state.pending[id] = nil end
    state.auto = {}
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
end
function api.ClearPendingBuildByTab(className, tabName)
    for id in pairs(state.pending) do
        local e = entry(id)
        if e and e.Class == className and e.Tab == tabName then state.pending[id] = nil end
    end
    refreshAuto()
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
end
function api.CancelPendingBuild()
    state.pending = copy(state.known)
    state.auto = {}
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
function api.CanClearPendingBuild() return true, "", nil, nil, nil end
function api.CanUnlearnAllTalents() return true, "" end
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
function api.ShouldConfirmLearnID() return false, {} end
function api.ShouldConfirmUnlearnID() return false, {} end
function api.ShouldConfirmUnlearnAllTalents() return true, {} end
function api.ShouldConfirmUnlearnAllSpells() return true, {} end
-- specializations (CoA)
function api.GetActiveChrSpec() return state.spec ~= 0 and state.spec or nil end
function api.GetActiveSpecID() return 0 end
function api.CanSwitchActiveChrSpec(id)
    if hero() then return false end
    local info = C_ClassInfo.GetSpecInfoByID(id)
    local ident = identity()
    return info ~= nil and ident ~= nil and info.Class == ident.Token
end
function api.SwitchActiveChrSpec(id)
    if not api.CanSwitchActiveChrSpec(id) then return false, "invalid-spec" end
    if not state.connected then Preview.SelectSpec(id) return true end
    T.Send("SPEC", tostring(id))
    return true
end
-- the sidebar "My Spells" list
local FILTER_CLASS = { FILTER_CLASS_DEATH_KNIGHT = "DeathKnight", FILTER_CLASS_GENERAL = "General", FILTER_CLASS_REBORN_GENERAL = "General" }
local function classFilter(key)
    if FILTER_CLASS[key] then return FILTER_CLASS[key] end
    local token = string.match(key, "^FILTER_CLASS_REBORN_(%u+)$") or string.match(key, "^FILTER_CLASS_(%u+)$")
    if token and CharacterAdvancementUtil then return CharacterAdvancementUtil.GetClassDBCByFile(token) end
end
function api.SetFilteredEntries(text, filters)
    text = type(text) == "string" and string.lower(text) or ""
    filters = type(filters) == "table" and filters or {}
    local wantKnown, wantUnknown = filters.FILTER_KNOWN or filters.FILTER_KNOWN_IN_BUILD, filters.FILTER_UNKNOWN or filters.FILTER_UNKNOWN_IN_BUILD
    local wantLearn, wantUnlearn = filters.FILTER_CAN_LEARN or filters.FILTER_CAN_ADD, filters.FILTER_CAN_UNLEARN or filters.FILTER_CAN_REMOVE
    local types, qualities, classes = {}, {}, {}
    for key, on in pairs(filters) do
        if on and type(key) == "string" then
            local t = string.match(key, "^FILTER_TYPE_(%u+)$")
            if t then types[t] = true end
            local q = string.match(key, "^FILTER_QUALITY_(%u+)$")
            if q then qualities[string.upper(q)] = true end
            local c = classFilter(key)
            if c then classes[c] = true end
        end
    end
    local out = {}
    for _, e in ipairs(Data.GetEntries()) do
        local ok = inPool(e) and not flag(e, FLAG_HIDDEN)
        if ok and text ~= "" and not string.find(string.lower(e.Name or ""), text, 1, true) then ok = false end
        if ok and (wantKnown or wantUnknown) then
            local k = (state.pending[e.ID] or 0) > 0 or (state.known[e.ID] or 0) > 0
            if wantKnown and not wantUnknown and not k then ok = false end
            if wantUnknown and not wantKnown and k then ok = false end
        end
        if ok and wantLearn and not api.CanAddByEntryID(e.ID, 1) then ok = false end
        if ok and wantUnlearn and not api.CanRemoveByEntryID(e.ID) then ok = false end
        if ok and next(types) then
            local t = e.Type == "Ability" and "ABILITY" or (isTalentLike(e) and "TALENT" or "TRAIT")
            if flag(e, FLAG_TRAIT) then t = "TRAIT" end
            if not types[t] then ok = false end
        end
        if ok and next(qualities) and not qualities[upper(e.Quality)] then ok = false end
        if ok and next(classes) and not classes[e.Class] then ok = false end
        if ok then out[#out + 1] = e end
    end
    table.sort(out, function(a, b)
        if a.Class ~= b.Class then return a.Class < b.Class end
        return byLevelName(a, b)
    end)
    state.filtered = out
    return true
end
function api.GetNumFilteredEntries() return #state.filtered end
function api.GetFilteredEntryAtIndex(i) return state.filtered[i], false end
function api.IsFiltered(id) for _, e in ipairs(state.filtered) do if e.ID == id then return true end end return false end
-- the ability browser is not enabled on this port (its categories were captured, its membership was not)
function api.GetCategories() return {} end
function api.GetCategoryDisplayInfo() return 0, nil, nil, nil, false end
function api.SetFilteredEntriesByCategory() end
function api.GetNumFilteredEntriesByCategory() return 0 end
function api.GetFilteredEntryAtIndexByCategory() return nil, false end
function api.GetRootSpellTagTypes() return {} end
function api.GetSpellTagTypes() return {} end
function api.GetSpellTagTypeDisplayInfo() return nil end
-- WildCard locks, suggestions and swaps do not exist here
function api.IsLockedID() return false end
function api.LockID() end
function api.UnlockID() end
function api.IsSuggestionContextOverride() return false end
function api.HasAnySuggestionContextOverrides() return false end
function api.AddSuggestionContextOverride() end
function api.RemoveSuggestionContextOverride() end
function api.ClearSuggestionContextOverrides() end
function api.GetEntriesAvailableForTrade() return {} end
function api.GetEntriesAvailableForSwapInClass() return {} end
function api.CanSwapEntriesByID() return false end
function api.SwapEntriesByID() end
function api.ClearRecentlyLearnedEntries() end
function api.ExportBuild() return nil end
-- action bar
function api.PickupSpell(id)
    local e = entry(id)
    if not e then return end
    local rank = math.max(1, state.known[id] or 0)
    local spell = e.Spells[rank] or e.Spells[1]
    if spell and _G.PickupSpell then _G.PickupSpell(spell) end
end
-- Hero Architect import: stage a recovered build (its Spells list) as the pending set; the
-- panel's Save Changes then uploads it like any other pending build.
local function importSpells(spells)
    if not state.connected then return false, "ASC_OFFLINE" end
    local staged, skipped = {}, 0
    for _, s in ipairs(spells or {}) do
        local spellID = type(s) == "table" and (s.Spell or s.spell or s.SpellID) or s
        local e = spellID and api.GetEntryBySpellID(spellID)
        if e then
            local rank = 0
            for i, id in ipairs(e.Spells) do if id == spellID then rank = i end end
            if rank > (staged[e.ID] or 0) then staged[e.ID] = rank end
        else
            skipped = skipped + 1
        end
    end
    state.pending = copy(state.known)
    state.auto = {}
    local added = 0
    for id, rank in pairs(staged) do
        local e = entry(id)
        if e and inPool(e) then
            local cur = state.pending[id] or 0
            while cur < rank and api.CanAddByEntryID(id, 1) do
                state.pending[id] = cur + 1
                cur = cur + 1
                added = added + 1
            end
        end
    end
    refreshAuto()
    log("import: " .. added .. " rank(s) staged, " .. skipped .. " spell(s) outside this character's trees")
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
    return true, added
end
function api.ImportPendingBuild(build)
    if type(build) ~= "table" then return false, "no-build" end
    return importSpells(build.Spells or build.spells or build.entries)
end
function api.ImportPendingBuildID(id)
    local build = C_BuildCreator and C_BuildCreator.GetBuild and C_BuildCreator.GetBuild(id)
    if not build then return false, "unknown-build" end
    return api.ImportPendingBuild(build)
end

for name, fn in pairs(api) do CA[name] = fn end
Live.installed = true

----------------------------------------------------------------------------------------
-- Wire handlers
----------------------------------------------------------------------------------------
T.On("HELLO", function(body)
    local protocol, mode, enabled = string.match(body, "^(%d+)\t([^\t]*)\t(%d)")
    Live.protocol, Live.mode = tonumber(protocol), mode
    state.mode = mode
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
    if hero() then log("mailbox ignored on a Free-Pick realm") return end
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
    state.auto = {}
    state.connected = true
    if specChanged then fire("ASCENSION_CA_SPECIALIZATION_ACTIVE_ID_CHANGED", spec) end
    fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
    local n = 0
    for _ in pairs(state.known) do n = n + 1 end
    log("state: mode " .. tostring(state.mode) .. " class " .. classByte .. " spec " .. spec .. " level " .. level .. " AE " .. ae .. " TE " .. te .. " known " .. n)
end)

-- RESULT\t<verb>\t<OK|ERR>[\t<reason>[\t<entry>]]
T.On("RESULT", function(body)
    local verb, status, reason, id = string.match(body, "^([^\t]*)\t([^\t]*)\t?([^\t]*)\t?(.*)$")
    local ok = status == "OK"
    if verb == "APPLY" then
        fire("CHARACTER_ADVANCEMENT_UPDATE_ENTRIES_RESULT", ok, reason, nil, tonumber(id))
    elseif verb == "RESET" then
        fire("CHARACTER_ADVANCEMENT_PURGE_TALENTS_RESULT", ok, reason)
    elseif verb == "INSPECT" and not ok then
        local cb = table.remove(inspectCallbacks, 1)
        if cb then cb(nil, reason) end
    end
    if not ok then
        log(verb .. " refused: " .. tostring(reason) .. " " .. tostring(id))
        if DEFAULT_CHAT_FRAME then
            local text = (_G[reason] and type(_G[reason]) == "string") and _G[reason] or tostring(reason)
            local e = entry(tonumber(id))
            DEFAULT_CHAT_FRAME:AddMessage("|cffff4040Character Advancement:|r " .. verb .. " refused: " .. text .. (e and (" (" .. e.Name .. ")") or ""))
        end
    end
end)

-- INSPECT\t<name>\t<classByte>\t<specId>\t<level>\t<entry:rank,...>: another character's
-- advancement. Live.Inspect(name, callback) delivers { name, classByte, class, spec, level,
-- known } to the callback; the CoA panel has no inspect view, so /ca inspect <name> prints it.
function Live.Inspect(name, callback)
    if not state.connected then return false, "ASC_OFFLINE" end
    inspectCallbacks[#inspectCallbacks + 1] = callback
    T.Send("INSPECT", name)
    return true
end
T.On("INSPECT", function(body)
    local name, classByte, spec, level, list = string.match(body, "^([^\t]*)\t(%d+)\t(%d+)\t(%d+)\t?(.*)$")
    if not name then log("bad INSPECT: " .. string.sub(body, 1, 60)) return end
    local info = { name = name, classByte = tonumber(classByte), spec = tonumber(spec), level = tonumber(level), known = parseSet(list) }
    local dataset = ASC.Config and Data.Datasets[ASC.Config.dataset]
    for _, row in ipairs(dataset and dataset.tables and dataset.tables.classIdentities or {}) do
        if row.ID == info.classByte then info.class = row.Name end
    end
    local cb = table.remove(inspectCallbacks, 1)
    if cb then cb(info) end
    if ASC.Events.IsCustom("CHARACTER_ADVANCEMENT_INSPECT_RESULT") then fire("CHARACTER_ADVANCEMENT_INSPECT_RESULT", info) end
end)

T.On("ERROR", function(body) log("server error: " .. tostring(body)) end)
T.On("PONG", function() end)

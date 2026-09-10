-- api/C_MysticEnchant.lua: Ascension's C_MysticEnchant / C_MysticEnchantPreset natives for a
-- stock client, over the recovered catalogue (core/Enchants.lua).
--
-- What works: the Collections -> Mystic Enchants panel opens, its Collection tab pages through
-- the 3,822 recovered enchants (name, icon, quality, stacks, worldforged) with search and
-- quality filters, and tooltips resolve. What does not exist on this port, and is reported the
-- way Ascension's own UI reports "not at an altar": applied enchants (every slot is empty),
-- scrolls in bags, reforging, disenchanting, purchases and presets. Every Can* answers
-- (false, "RE_..._NO_MYSTIC_ALTAR") and HasNearbyMysticAltar() is false, so the panel shows its
-- own NoAltarFrame overlay and disables the cost buttons. No server round trip exists yet.
local ME = ASC.Namespace("C_MysticEnchant")
local MP = ASC.Namespace("C_MysticEnchantPreset")
local Enchants = ASC.Enchants
local NO_ALTAR = "RE_PURCHASE_NO_MYSTIC_ALTAR"

local function known(spellID)
    return IsSpellKnown and IsSpellKnown(spellID) or false
end

local function info(record)
    if not record then return nil end
    return {
        SpellID = record.SpellID,
        SpellName = record.SpellName,
        ClientName = record.ClientName or record.SpellName,
        Icon = record.Icon,
        Quality = record.Quality,
        MaxStacks = record.MaxStacks or 1,
        IsWorldforged = record.IsWorldforged and true or false,
        Known = known(record.SpellID),
        RequiredLevel = 0,                 -- not captured
        IsAvailableForCurrentClass = true, -- class requirements were not captured
        Spec = nil,
        ClassRequirements = {},
    }
end

function ME.GetEnchantInfoBySpell(spellID) return info(Enchants.Get(spellID)) end
function ME.GetEnchantInfoByItem() return nil end -- scroll items were not captured

local QUALITY_ORDER = { RE_QUALITY_POOR = 0, RE_QUALITY_NORMAL = 1, RE_QUALITY_UNCOMMON = 2, RE_QUALITY_RARE = 3,
                        RE_QUALITY_EPIC = 4, RE_QUALITY_LEGENDARY = 5, RE_QUALITY_ARTIFACT = 6, RE_QUALITY_HEIRLOOM = 7 }
local QUALITY_FILTER = { UNCOMMON = "RE_QUALITY_UNCOMMON", RARE = "RE_QUALITY_RARE", EPIC = "RE_QUALITY_EPIC",
                         LEGENDARY = "RE_QUALITY_LEGENDARY", ARTIFACT = "RE_QUALITY_ARTIFACT", NORMAL = "RE_QUALITY_NORMAL",
                         COMMON = "RE_QUALITY_NORMAL", POOR = "RE_QUALITY_POOR", HEIRLOOM = "RE_QUALITY_HEIRLOOM" }

-- filters arrive either as { KEY = true } or as an array of keys; only the qualities, the
-- known/unknown pair and the worldforged flag mean anything for a catalogue without costs.
local function parseFilters(filters)
    local wantQuality, wantKnown, wantUnknown, wantWorldforged = nil, false, false, false
    if type(filters) ~= "table" then return nil, false, false, false end
    for k, v in pairs(filters) do
        local key = type(k) == "string" and k or (type(v) == "string" and v or nil)
        if key and v ~= false then
            local up = string.upper(key)
            for token, quality in pairs(QUALITY_FILTER) do
                if string.find(up, "QUALITY_" .. token, 1, true) then
                    wantQuality = wantQuality or {}
                    wantQuality[quality] = true
                end
            end
            if string.find(up, "NOT_COLLECTED", 1, true) or string.find(up, "UNKNOWN", 1, true) then wantUnknown = true
            elseif string.find(up, "COLLECTED", 1, true) or string.find(up, "KNOWN", 1, true) then wantKnown = true end
            if string.find(up, "WORLDFORGED", 1, true) then wantWorldforged = true end
        end
    end
    return wantQuality, wantKnown, wantUnknown, wantWorldforged
end

local sorted
local function ordered()
    if sorted then return sorted end
    sorted = {}
    for _, r in ipairs(Enchants.All()) do sorted[#sorted + 1] = r end
    table.sort(sorted, function(a, b)
        local qa, qb = QUALITY_ORDER[a.Quality] or 0, QUALITY_ORDER[b.Quality] or 0
        if qa ~= qb then return qa > qb end
        return (a.SpellName or "") < (b.SpellName or "")
    end)
    return sorted
end

-- QueryEnchants(entriesPerPage, page, searchText, filters) -> results (page slice), maxPage
function ME.QueryEnchants(perPage, page, search, filters)
    perPage = tonumber(perPage) or 18
    page = math.max(1, tonumber(page) or 1)
    search = type(search) == "string" and string.lower(string.trim and string.trim(search) or search) or ""
    local wantQuality, wantKnown, wantUnknown, wantWorldforged = parseFilters(filters)
    local hits = {}
    for _, r in ipairs(ordered()) do
        local ok = true
        if search ~= "" and not string.find(string.lower(r.SpellName or ""), search, 1, true) then ok = false end
        if ok and wantQuality and not wantQuality[r.Quality] then ok = false end
        if ok and wantWorldforged and not r.IsWorldforged then ok = false end
        if ok and (wantKnown or wantUnknown) and wantKnown ~= wantUnknown then
            local k = known(r.SpellID)
            if wantKnown and not k then ok = false end
            if wantUnknown and k then ok = false end
        end
        if ok then hits[#hits + 1] = r end
    end
    local maxPage = math.max(1, math.ceil(#hits / perPage))
    local results = {}
    for i = (page - 1) * perPage + 1, math.min(#hits, page * perPage) do results[#results + 1] = info(hits[i]) end
    return results, maxPage
end

-- applied enchants / staging: nothing is applied and nothing can be staged without an altar
function ME.GetAppliedEnchant() return 0 end
function ME.GetApplyChanges() return {} end
function ME.GetCollectionReforgeChanges() return {} end
function ME.GetProgress() return 0, 1 end
function ME.HasNearbyMysticAltar() return false end
function ME.CanEquipSlot() return true end
for _, name in ipairs({ "CanApplySlot", "CanCollectionReforgeItem", "CanCollectionReforgeSlot", "CanDestroy", "CanDisenchantItem",
                        "CanDisenchantSlot", "CanPurchaseMysticScroll", "CanReforgeItem", "CanSaveApply", "CanSaveCollectionReforge" }) do
    ME[name] = function() return false, NO_ALTAR end
end
function ME.CanPurchaseMysticExtract() return false end
for _, name in ipairs({ "ApplySlot", "CollectionReforgeItem", "CollectionReforgeSlot", "Destroy", "DisenchantItem", "DisenchantSlot",
                        "PurchaseMysticExtract", "PurchaseMysticScroll", "ReforgeItem", "SaveApply", "SaveCollectionReforge",
                        "UndoApply", "UndoCollectionReforge", "SetMysticScrollFilter" }) do
    ME[name] = function() end
end
function ME.GetCollectionReforgeItemCost() return 0, 0 end
function ME.GetCollectionReforgeSlotCost() return 0, 0 end
function ME.GetDisenchantCost() return 0, 0 end
function ME.GetReforgeCost() return 0 end
function ME.GetMysticScrollCost() return 0 end
function ME.GetNumFilteredMysticScrolls() return 0 end
function ME.GetFilteredMysticScrollAtIndex() return nil end

-- presets (server-held on Ascension; none exist here)
function MP.GetNumPresets() return 0 end
function MP.GetPresetData() return {}, false end
function MP.CanActivate() return false, { NO_ALTAR } end
function MP.CanUnlock() return false, { NO_ALTAR } end
function MP.Activate() end
function MP.Unlock() end

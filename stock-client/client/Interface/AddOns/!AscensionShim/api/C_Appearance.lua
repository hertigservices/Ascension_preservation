-- api/C_Appearance.lua: Ascension's C_Appearance / C_AppearanceCollection / C_AppearanceOutfit
-- natives for a stock client, over the recovered catalogue (core/Collections.lua: 41,783
-- appearances in 64 categories, 202,498 item -> appearance mappings, from the CoA repack's
-- Appearances.dbc and ItemAppearances.dbc).
--
-- What works: Collections -> Wardrobe opens Ascension's own transmogrification panel, its
-- type tabs and slot categories page through the catalogue with search, models try the items
-- on, tooltips name the appearance and the items that share it. What does not exist on this
-- port, and is reported instead of faked: applying an appearance (no transmogrifier or server
-- verb; Apply answers false), outfits, item-set appearances, illusion previews, the Ethereal
-- Bazaar and webstore links, and account-wide ownership (an appearance counts as collected
-- when the character carries one of its items).
local A = ASC.Namespace("C_Appearance")
local AC = ASC.Namespace("C_AppearanceCollection")
local AO = ASC.Namespace("C_AppearanceOutfit")
local Collections = ASC.Collections

local CREATURE_CATEGORIES = { [33] = true, [34] = true, [35] = true, [36] = true, [37] = true, [62] = true, [63] = true }
local pending = {}
local filter = { category = nil, search = "", list = {}, collected = 0 }

local function log(msg)
    local S = ASC.Stock
    if S and S.Log then S.Log(msg) end
end

local function fire(event, ...)
    if ASC.Events and ASC.Events.IsCustom and ASC.Events.IsCustom(event) then ASC.Events.Fire(event, ...) end
end

local function carries(itemID)
    if not itemID or itemID == 0 or not GetItemCount then return false end
    return (GetItemCount(itemID, true) or 0) > 0
end

local function collected(record)
    if not record then return false end
    if carries(record.item) then return true end
    for _, item in ipairs(Collections.ItemsForAppearance(record.id)) do
        if carries(item) then return true end
    end
    return false
end

local function displayTypeOf(record)
    if CREATURE_CATEGORIES[record.category] and record.display then
        return "APPEARANCE_DISPLAY_TYPE_CREATURE", record.display
    end
    return "APPEARANCE_DISPLAY_TYPE_ITEM", record.item
end

local function pageSize()
    local wardrobe = AppearanceWardrobeFrame
    local models = wardrobe and wardrobe.Collection and wardrobe.Collection.Models
    return models and #models > 0 and #models or 8
end

-- C_AppearanceCollection --------------------------------------------------------------------
function AC.GetAppearanceTypes()
    local out = {}
    for i, t in ipairs(Collections.Types()) do out[i] = t end
    return out
end
function AC.GetCategoriesForType(appearanceType) return Collections.CategoriesForType(appearanceType) end
function AC.GetCategoryInfo(categoryID)
    local c = Collections.Category(tonumber(categoryID))
    if not c then
        -- the player model's slot buttons also ask about categories the catalogue never filled
        -- (an off-hand illusion slot, for one); answer with a placeholder rather than nil
        return "Category " .. tostring(categoryID), "INV_Misc_QuestionMark", "APPEARANCE_TYPE_NONE"
    end
    local name = c.nameKey and _G[c.nameKey] or c.name or ("Category " .. c.id)
    return name, c.icon or "INV_Misc_QuestionMark", c.type
end
function AC.GetAvailableChapters() return {} end
function AC.GetAvailableSeasons() return {} end
function AC.GetSeasonalItems() return {} end
function AC.CollectItemAppearance() return false end
function AC.IsAppearanceCollected(appearanceID) return collected(Collections.Appearance(tonumber(appearanceID))) end

-- ApplyCategoryFilter(categoryID, searchText, ...) selects the category the page queries read from.
function AC.ApplyCategoryFilter(categoryID, search)
    categoryID = tonumber(categoryID)
    search = type(search) == "string" and string.lower(search) or ""
    local list, count = {}, 0
    for _, r in ipairs(Collections.AppearancesInCategory(categoryID)) do
        if search == "" or string.find(string.lower(r.name or ""), search, 1, true) then
            list[#list + 1] = r.id
            if collected(r) then count = count + 1 end
        end
    end
    filter.category, filter.search, filter.list, filter.collected = categoryID, search, list, count
    return true
end
function AC.GetCollectedCount(categoryID)
    categoryID = tonumber(categoryID)
    if filter.category ~= categoryID then AC.ApplyCategoryFilter(categoryID, "") end
    return filter.collected, #Collections.AppearancesInCategory(categoryID)
end
function AC.GetCategoryMaxPages() return math.max(1, math.ceil(#filter.list / pageSize())) end
function AC.GetCategoryAppearances(page)
    page = math.max(1, tonumber(page) or 1)
    local size = pageSize()
    local out = {}
    for i = (page - 1) * size + 1, math.min(#filter.list, page * size) do out[#out + 1] = filter.list[i] end
    return out
end

-- C_Appearance ------------------------------------------------------------------------------
-- -> displayType, displayID, uiCamera, displayName, entry, spellVisualID
function A.GetAppearanceDisplayInfo(appearanceID)
    local r = Collections.Appearance(tonumber(appearanceID))
    if not r then return nil end
    local displayType, displayID = displayTypeOf(r)
    return displayType, displayID, nil, r.name, r.item, nil
end
function A.GetAppearanceForCategory() return nil end -- nothing is applied: there is no transmogrifier on this port
function A.GetPendingAppearance(categoryID) return pending[tonumber(categoryID)] end
function A.SetPendingAppearance(categoryID, appearanceID)
    categoryID, appearanceID = tonumber(categoryID), tonumber(appearanceID)
    if not categoryID then return false end
    pending[categoryID] = appearanceID ~= 0 and appearanceID or nil
    fire("PENDING_APPEARANCE_CHANGED", categoryID, pending[categoryID])
    return true
end
function A.ClearPendingAppearance(categoryID)
    categoryID = tonumber(categoryID)
    pending[categoryID] = nil
    fire("PENDING_APPEARANCE_CHANGED", categoryID, nil)
end
function A.ClearPendingAppearances()
    for k in pairs(pending) do pending[k] = nil end
    fire("PENDING_APPEARANCE_CHANGED")
end
function A.ClearInvalidPendingAppearances() end
function A.HasPendingAppearances() return next(pending) ~= nil end
function A.CanApplyPendingAppearances() return false, "APPEARANCE_NO_TRANSMOGRIFIER" end
function A.ApplyPendingAppearances()
    log("C_Appearance.ApplyPendingAppearances refused: no transmogrifier or server verb on this port")
    fire("APPLY_PENDING_APPEARANCE_RESULT", false)
    return false
end
function A.CanSetAppearance(categoryID, appearanceID)
    local r = Collections.Appearance(tonumber(appearanceID))
    if not r then return false, "APPEARANCE_UNKNOWN" end
    return true
end
function A.CanSeeAppearances() return true, true end
function A.SetCanSeeAppearances() end
function A.IsTransmogable() return true end
function A.GetAppearanceDetails() return nil end
function A.GetAlternativeIDs(appearanceID, entry)
    local out = {}
    for _, item in ipairs(Collections.ItemsForAppearance(tonumber(appearanceID))) do
        if item ~= entry then out[#out + 1] = item end
    end
    return out
end
function A.GetActiveDiscount() return nil end
function A.GetAppearanceWebURL() return nil end
function A.GetEtherealBazaarWebURL() return nil end
function A.IsEtherealBazaarAppearance() return false end
function A.GetItemAppearanceID(itemID) return Collections.AppearanceForItem(tonumber(itemID)) end
function A.GetItemSetAppearanceID() return nil end
function A.GetAppearanceItemSet() return nil end
function A.GetCreatureDisplayItems() return {} end

-- C_AppearanceOutfit -------------------------------------------------------------------------
function AO.GetAppearanceOutfits() return {} end
function AO.GetCurrentOutfitName() return nil end
function AO.GetOutfitInfo() return nil end
function AO.SaveOutfit() log("C_AppearanceOutfit.SaveOutfit refused: outfits need the transmog server") return false end
function AO.DeleteOutfit() return false end
function AO.SetPendingOutfit() return false end

-- C_ItemSet (only what the Wardrobe touches; Ascension's own namespace is native) -----------
C_ItemSet = C_ItemSet or {}
if not C_ItemSet.GetItemSetName then function C_ItemSet.GetItemSetName() return nil end end
if not C_ItemSet.GetItemSetNumCollected then function C_ItemSet.GetItemSetNumCollected() return 0, 0 end end

-- api/C_VanityCollection.lua: Ascension's C_VanityCollection natives for a stock client, over the
-- recovered catalogue (core/Collections.lua, 10,678 items from the CoA repack's VanityCollection.dbc).
--
-- What works: Collections -> Vanity opens Ascension's own store panel, lists and searches the
-- catalogue by category, shows descriptions, webstore prices and creature / item previews.
-- What does not exist on this port, and is reported instead of faked: ownership (an item counts
-- as owned only when its learned spell is a spell the character knows, e.g. a mount already
-- collected), seasonal points, bazaar tokens, purchases (no web shop) and delivery (no server
-- verb yet). Every purchase answers false and logs why.
local VC = ASC.Namespace("C_VanityCollection")
local Collections = ASC.Collections
local band = bit.band

local FILTER_OWNED, FILTER_UNOWNED, FILTER_PURCHASABLE, FILTER_DELIVERABLE, FILTER_HEIRLOOM, FILTER_MANASTORM = 1, 2, 4, 8, 16, 32
local FLAG_SEASONAL_SHOWCASE = 16

local function owned(record)
    if not record or not record.learnedSpell or record.learnedSpell == 0 then return false end
    return IsSpellKnown and IsSpellKnown(record.learnedSpell) or false
end

local function copy(record)
    if not record then return nil end
    local t = {}
    for k, v in pairs(record) do t[k] = v end
    t.itemID = record.itemid
    t.owned = owned(record)
    t.known = t.owned
    return t
end

function VC.GetItem(itemID) return copy(Collections.VanityGet(tonumber(itemID))) end
function VC.GetAllItems()
    local out = {}
    for i, r in ipairs(Collections.VanityAll()) do out[i] = copy(r) end
    return out
end
function VC.GetItemByLearnedSpell(spellID) return copy(Collections.VanityBySpell(tonumber(spellID))) end
function VC.GetOwnerByContentItem(itemID) return copy(Collections.VanityByContent(tonumber(itemID))) end
function VC.IsCollectionItemOwned(itemID) return owned(Collections.VanityGet(tonumber(itemID))) end
function VC.IsOwned(itemID) return owned(Collections.VanityGet(tonumber(itemID))) end
function VC.IsConsolidatedVanityBuff() return false end
function VC.GetDPPrice(itemID)
    local r = Collections.VanityGet(tonumber(itemID))
    return r and r.dpCost or 0
end
function VC.GetVPPrice() return 0 end
function VC.IsPurchaseInProgress() return false end

function VC.GetSeasonalShowcaseItems()
    local out = {}
    for _, r in ipairs(Collections.VanityAll()) do
        if band(r.flags or 0, FLAG_SEASONAL_SHOWCASE) ~= 0 then out[#out + 1] = copy(r) end
    end
    return out
end

function VC.GetRandomItem()
    local all = Collections.VanityAll()
    if #all == 0 then return nil end
    -- prefer something the banner can preview
    for _ = 1, 8 do
        local r = all[math.random(#all)]
        if (r.creaturePreview or 0) > 0 or #(r.contentsPreview or {}) > 0 then return copy(r) end
    end
    return copy(all[math.random(#all)])
end

local function matchesCategory(record, flags)
    if not flags or flags == 0 then return true end
    local cat = record.category or 0
    return band(cat, flags) == cat
end

-- QueryItems(search, categoryFlags, filterFlags, sortMode, offset, count) -> { total, items }
function VC.QueryItems(search, categoryFlags, filterFlags, _, offset, count)
    search = type(search) == "string" and string.lower(search) or ""
    categoryFlags = tonumber(categoryFlags) or 0
    filterFlags = tonumber(filterFlags) or 0
    offset = math.max(0, tonumber(offset) or 0)
    count = math.max(1, tonumber(count) or 12)
    -- The store's "Known" checkbox (on by default) sets the Owned flag; read as an inclusion
    -- filter (owned items shown alongside the rest), since a shop whose default view hid every
    -- unowned item would be empty for a new character. Unowned items are always listed.
    local wantOwned, wantUnowned = band(filterFlags, FILTER_OWNED) ~= 0, true
    local hits = {}
    for _, r in ipairs(Collections.VanityAll()) do
        local ok = matchesCategory(r, categoryFlags)
        if ok and search ~= "" and not string.find(string.lower(r.name or ""), search, 1, true)
            and not string.find(string.lower(r.description or ""), search, 1, true) then ok = false end
        if ok and band(filterFlags, FILTER_HEIRLOOM) ~= 0 and r.quality ~= 7 then ok = false end
        if ok and band(filterFlags, FILTER_MANASTORM) ~= 0 then ok = false end -- no Manastorm data was recovered
        if ok and band(filterFlags, FILTER_PURCHASABLE) ~= 0 and (r.dpCost or 0) == 0 then ok = false end
        if ok and wantOwned ~= wantUnowned then
            local o = owned(r)
            if wantOwned and not o then ok = false end
            if wantUnowned and o then ok = false end
        end
        if ok then hits[#hits + 1] = r end
    end
    table.sort(hits, function(a, b) return (a.name or "") < (b.name or "") end)
    local items = {}
    for i = offset + 1, math.min(#hits, offset + count) do items[#items + 1] = copy(hits[i]) end
    return { total = #hits, items = items }
end

local function log(msg)
    local S = ASC.Stock
    if S and S.Log then S.Log(msg) end
end

local function refuse(what)
    return function(itemID)
        log("C_VanityCollection." .. what .. "(" .. tostring(itemID) .. ") refused: no web shop or vanity server on this port")
        return false
    end
end
VC.Purchase = refuse("Purchase")
VC.PurchaseCollectionItem = refuse("PurchaseCollectionItem")
VC.PurchaseWebShopItem = refuse("PurchaseWebShopItem")
VC.RequestDelivery = refuse("RequestDelivery")

-- core/Collections.lua: the recovered Vanity and Wardrobe catalogues (tools/gen_collection_data.py),
-- read from the CoA repack's VanityCollection / Appearances / ItemAppearances DBCs.
-- Read-only records. Vanity: itemid, name, quality, icon, category (Enum.VanityCategory mask),
-- group (Store.GroupIcons id, ours), flags, description, dpCost, creaturePreview, contentsPreview,
-- learnedSpell. Appearance: id, item, category, typeCode, secondary, name, quality, optional
-- display / sourceItem / scale. Nothing about ownership, prices beyond the webstore column,
-- artwork or Ascension's own category labels was recovered, and nothing here pretends otherwise.
ASC.Collections = ASC.Collections or {}
local Collections = ASC.Collections
local catalogue
local loading

local function fresh(metadata)
    return {
        metadata = metadata,
        categories = {}, categoryOrder = {}, typeOrder = {},
        vanity = {}, vanityOrdered = {}, vanityBySpell = {}, vanityByContent = {},
        appearances = {}, appearanceOrdered = {}, byCategory = {},
        itemToAppearance = {}, appearanceItems = {}, mappedPairs = 0,
    }
end

function Collections.Begin(metadata)
    assert(not loading, "collection catalogue already loading")
    assert(type(metadata) == "table" and metadata.format == 1, "unsupported collection catalogue metadata")
    loading = fresh(metadata)
end

function Collections.AddCategory(category)
    assert(loading, "Collections.Begin must precede AddCategory")
    assert(type(category) == "table" and type(category.id) == "number" and type(category.type) == "string", "invalid category record")
    assert(not loading.categories[category.id], "duplicate category " .. category.id)
    loading.categories[category.id] = category
    loading.categoryOrder[#loading.categoryOrder + 1] = category.id
    local seen = false
    for _, t in ipairs(loading.typeOrder) do if t == category.type then seen = true end end
    if not seen then loading.typeOrder[#loading.typeOrder + 1] = category.type end
    loading.byCategory[category.id] = loading.byCategory[category.id] or {}
end

function Collections.AddVanity(record)
    assert(loading, "Collections.Begin must precede AddVanity")
    assert(type(record) == "table" and type(record.itemid) == "number", "invalid vanity record")
    assert(not loading.vanity[record.itemid], "duplicate vanity item " .. record.itemid)
    loading.vanity[record.itemid] = record
    loading.vanityOrdered[#loading.vanityOrdered + 1] = record
    if record.learnedSpell and record.learnedSpell ~= 0 and not loading.vanityBySpell[record.learnedSpell] then
        loading.vanityBySpell[record.learnedSpell] = record
    end
    for _, item in ipairs(record.contentsPreview or {}) do
        if not loading.vanityByContent[item] then loading.vanityByContent[item] = record end
    end
end

function Collections.AddAppearance(record)
    assert(loading, "Collections.Begin must precede AddAppearance")
    assert(type(record) == "table" and type(record.id) == "number" and type(record.category) == "number", "invalid appearance record")
    assert(not loading.appearances[record.id], "duplicate appearance " .. record.id)
    loading.appearances[record.id] = record
    loading.appearanceOrdered[#loading.appearanceOrdered + 1] = record
    local list = loading.byCategory[record.category]
    if not list then list = {}; loading.byCategory[record.category] = list end
    list[#list + 1] = record
    if record.item and record.item ~= 0 and not loading.itemToAppearance[record.item] then
        loading.itemToAppearance[record.item] = record.id
    end
end

-- flat {item, appearance, item, appearance, ...}
function Collections.MapItems(flat)
    assert(loading, "Collections.Begin must precede MapItems")
    local itemTo, items = loading.itemToAppearance, loading.appearanceItems
    for i = 1, #flat, 2 do
        local item, app = flat[i], flat[i + 1]
        if not itemTo[item] then itemTo[item] = app end
        local list = items[app]
        if not list then list = {}; items[app] = list end
        list[#list + 1] = item
        loading.mappedPairs = loading.mappedPairs + 1
    end
end

function Collections.Finalize()
    assert(loading, "no collection catalogue to finalize")
    local m = loading.metadata
    assert(#loading.vanityOrdered == m.vanityCount, "vanity count differs from generation manifest")
    assert(#loading.appearanceOrdered == m.appearanceCount, "appearance count differs from generation manifest")
    assert(loading.mappedPairs == m.itemAppearanceCount, "item appearance count differs from generation manifest")
    assert(#loading.categoryOrder == m.categoryCount, "category count differs from generation manifest")
    catalogue = loading
    loading = nil
    catalogue.complete = true
end

function Collections.Current() return catalogue end
function Collections.HasVanity() return catalogue ~= nil and #catalogue.vanityOrdered > 0 end
function Collections.HasAppearances() return catalogue ~= nil and #catalogue.appearanceOrdered > 0 end

-- vanity
function Collections.VanityGet(itemID) return catalogue and catalogue.vanity[itemID] or nil end
function Collections.VanityAll() return catalogue and catalogue.vanityOrdered or {} end
function Collections.VanityBySpell(spellID) return catalogue and catalogue.vanityBySpell[spellID] or nil end
function Collections.VanityByContent(itemID) return catalogue and catalogue.vanityByContent[itemID] or nil end

-- wardrobe
function Collections.Types() return catalogue and catalogue.typeOrder or {} end
function Collections.Category(id) return catalogue and catalogue.categories[id] or nil end
function Collections.CategoriesForType(appearanceType)
    local out = {}
    if not catalogue then return out end
    for _, id in ipairs(catalogue.categoryOrder) do
        if catalogue.categories[id].type == appearanceType then out[#out + 1] = id end
    end
    return out
end
function Collections.Appearance(id) return catalogue and catalogue.appearances[id] or nil end
function Collections.AppearancesInCategory(id) return catalogue and catalogue.byCategory[id] or {} end
function Collections.AppearanceForItem(itemID) return catalogue and catalogue.itemToAppearance[itemID] or nil end
function Collections.ItemsForAppearance(id) return catalogue and catalogue.appearanceItems[id] or {} end
-- the appearance record whose source item is this item (inventory type / icon before the client caches it)
function Collections.AppearanceRecordForItem(itemID)
    if not catalogue then return nil end
    local id = catalogue.itemToAppearance[itemID]
    local r = id and catalogue.appearances[id]
    return r and r.item == itemID and r or nil
end

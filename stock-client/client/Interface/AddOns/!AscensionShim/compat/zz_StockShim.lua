-- zz_StockShim.lua: what Extensions.dll / Ascension's modified FrameXML provided and a
-- STOCK 3.3.5a client lacks, for the Collections + Character Advancement cluster.
-- Loads after the verbatim Ascension SharedXML/FrameXML compat files and before Bootstrap.
ASC = ASC or {}
ASC.Stock = ASC.Stock or {}
local Stock = ASC.Stock

----------------------------------------------------------------------------------------
-- 1. Widget metatable getters (Ascension.exe natives used by SharedXML\TypeExtensions)
----------------------------------------------------------------------------------------
local mtCache = {}
local function frameMT(kind)
    if not mtCache[kind] then
        -- A parentless frame is visible by default, and an EditBox is keyboard-enabled by
        -- default: left alone it swallows every keystroke (chat included). Hide the probe.
        local ok, f = pcall(CreateFrame, kind, nil, UIParent)
        if ok and f then
            mtCache[kind] = getmetatable(f)
            if f.EnableKeyboard then f:EnableKeyboard(false) end
            if f.EnableMouse then f:EnableMouse(false) end
            f:Hide()
        end
    end
    return mtCache[kind]
end
local getters = {
    Frame = function() return frameMT("Frame") end,
    Button = function() return frameMT("Button") end,
    CheckButton = function() return frameMT("CheckButton") end,
    EditBox = function() return frameMT("EditBox") end,
    Cooldown = function() return frameMT("Cooldown") end,
    Model = function() return frameMT("Model") end,
    PlayerModel = function() return frameMT("PlayerModel") end,
    DressUpModel = function() return frameMT("DressUpModel") end,
    TabardModel = function() return frameMT("TabardModel") end,
    ScrollFrame = function() return frameMT("ScrollFrame") end,
    Slider = function() return frameMT("Slider") end,
    StatusBar = function() return frameMT("StatusBar") end,
    SimpleHTML = function() return frameMT("SimpleHTML") end,
    MessageFrame = function() return frameMT("MessageFrame") end,
    ScrollingMessageFrame = function() return frameMT("ScrollingMessageFrame") end,
    GameTooltip = function() return getmetatable(GameTooltip) end,
    Texture = function() if not mtCache.Texture then mtCache.Texture = getmetatable(UIParent:CreateTexture()) end return mtCache.Texture end,
    FontString = function() if not mtCache.FontString then mtCache.FontString = getmetatable(UIParent:CreateFontString()) end return mtCache.FontString end,
}
for kind, fn in pairs(getters) do
    if not _G["Get" .. kind .. "Metatable"] then _G["Get" .. kind .. "Metatable"] = fn end
end
-- Ascension native: SetMaskTexture(size, maskPath) selects the alpha mask that the next
-- SetPortraitToTexture applies (TypeExtensions\Texture.lua SetMaskedTexture). Stock has
-- no mask selection, so SetPortraitToTexture keeps its built-in round portrait mask.
if not SetMaskTexture then
    function SetMaskTexture() end
end
-- Ascension native namespace C_VanityCollection (no vanity catalogue was recovered, see
-- DESIGN.md): every lookup answers "nothing", which is what C_Spell's auto-place-on-bar
-- path and VanityCollectionUtil expect for a spell that is not a vanity item.
C_VanityCollection = C_VanityCollection or {}
C_VanityCollection.GetItem = C_VanityCollection.GetItem or function() return nil end
C_VanityCollection.GetItemByLearnedSpell = C_VanityCollection.GetItemByLearnedSpell or function() return nil end
C_VanityCollection.GetOwnerByContentItem = C_VanityCollection.GetOwnerByContentItem or function() return nil end
C_VanityCollection.GetSeasonalShowcaseItems = C_VanityCollection.GetSeasonalShowcaseItems or function() return {} end
-- Hero Architect (Ascension_BuildCreator) natives that are out of scope for the P5 browse /
-- import path: the build EDITOR (C_BuildEditor publishes builds to Ascension's service),
-- WildCard mode and Mystic Enchant lookups. Every call answers "no" / nothing so the addon
-- loads and its read-only paths work; the editor UI stays visibly inert.
local function inertNamespace(name, overrides)
    local ns = _G[name] or {}
    _G[name] = ns
    setmetatable(ns, { __index = function(_, key)
        local fn = function() return nil end
        rawset(ns, key, fn)
        return fn
    end })
    for k, v in pairs(overrides or {}) do ns[k] = v end
    return ns
end
-- The build EDITOR keeps its pending build in memory. On Ascension the native held it
-- server-side and PublishBuild sent it to their build service; here "Create build" and
-- "Import current build" fill this table so the editor view renders and edits (the same
-- record shape the catalogue's builds use), and publishing answers read-only.
local pendingBuild
local function newPendingBuild()
    return { Name = "", Icon = "INV_Misc_QuestionMark", Category = Enum.BuildCategory.None,
             Description = "", Roles = "PLAYER_ROLE_NONE", PrimaryStat = "", Comment = "",
             DifficultyRating = Enum.BuildDifficulty.Standard, Flags = 0,
             Spells = {}, RandomEnchants = {}, ArmorTypes = {}, WeaponTypes = {} }
end
pendingBuild = newPendingBuild()
local function copyRecord(t)
    local c = {}
    for k, v in pairs(t) do
        if type(v) == "table" then
            local list = {}
            for i, x in ipairs(v) do list[i] = type(x) == "table" and copyRecord(x) or x end
            c[k] = list
        else
            c[k] = v
        end
    end
    return c
end
local function loadPendingBuild(record)
    pendingBuild = newPendingBuild()
    if type(record) ~= "table" then return end
    for k, v in pairs(copyRecord(record)) do pendingBuild[k] = v end
    pendingBuild.Spells = pendingBuild.Spells or {}
    pendingBuild.RandomEnchants = pendingBuild.RandomEnchants or {}
    pendingBuild.ArmorTypes = pendingBuild.ArmorTypes or {}
    pendingBuild.WeaponTypes = pendingBuild.WeaponTypes or {}
end
local function findSpell(spellID)
    for i, s in ipairs(pendingBuild.Spells) do if s.Spell == spellID then return i, s end end
end
local function findEnchant(enchantID)
    for i, e in ipairs(pendingBuild.RandomEnchants) do if e.Enchant == enchantID then return i, e end end
end
local function findType(list, value)
    for i, x in ipairs(list) do if x == value or (type(x) == "table" and x.Type == value) then return i end end
end
local function setSpellField(spellID, key, value)
    local _, s = findSpell(spellID)
    if s then s[key] = value end
end
local function setEnchantField(enchantID, key, value)
    local _, e = findEnchant(enchantID)
    if e then e[key] = value end
end
inertNamespace("C_BuildEditor", {
    GetPendingBuild = function() return pendingBuild end,
    DiscardPendingBuild = function() pendingBuild = newPendingBuild() end,
    EditBuild = function(buildID)
        loadPendingBuild(C_BuildCreator and C_BuildCreator.GetBuild and C_BuildCreator.GetBuild(buildID))
    end,
    ImportBuild = function(record) loadPendingBuild(record) end,
    -- (canPublish, failReasons[]): the editor prints _G[reason] or reason per entry.
    CanPublishBuild = function() return false, { "ASC_PREVIEW_READ_ONLY" } end,
    PublishBuild = function() return false end,
    GetEssenceForLevel = function(level)
        local ae, te = ASC.Data.GetBudget(ASC.Config and ASC.Config.classByte or 28, level or 80)
        return ae or 0, te or 0
    end,
    GetSpellByID = function(spellID) local _, s = findSpell(spellID) return s end,
    DoesBuildHaveSpellID = function(spellID) return findSpell(spellID) ~= nil end,
    CanAddSpell = function() return true end,
    CanRemoveSpell = function() return true end,
    AddSpell = function(spell)
        if type(spell) ~= "table" or not spell.Spell then return end
        if not findSpell(spell.Spell) then table.insert(pendingBuild.Spells, copyRecord(spell)) end
    end,
    RemoveSpell = function(spell)
        local i = findSpell(type(spell) == "table" and spell.Spell or spell)
        if i then table.remove(pendingBuild.Spells, i) end
    end,
    CanSetSpellLevel = function() return true end,
    SetSpellLevel = function(spellID, level) setSpellField(spellID, "Level", level) end,
    CanSetSpellFlags = function() return true end,
    SetSpellFlags = function(spellID, flags) setSpellField(spellID, "Flags", flags) end,
    CanSetIsCoreAbility = function() return true end,
    SetIsCoreAbility = function(spellID, v) setSpellField(spellID, "IsCoreAbility", v and true or false) end,
    CanSetIsOptimalAbility = function() return true end,
    SetIsOptimalAbility = function(spellID, v) setSpellField(spellID, "IsOptimalAbility", v and true or false) end,
    CanSetIsEmpoweringAbility = function() return true end,
    SetIsEmpoweringAbility = function(spellID, v) setSpellField(spellID, "IsEmpoweringAbility", v and true or false) end,
    CanSetIsSynergisticAbility = function() return true end,
    SetIsSynergisticAbility = function(spellID, v) setSpellField(spellID, "IsSynergisticAbility", v and true or false) end,
    DoesBuildHaveEnchant = function(enchantID) return findEnchant(enchantID) ~= nil end,
    AddRandomEnchant = function(enchant)
        if type(enchant) ~= "table" or not enchant.Enchant then return end
        if not findEnchant(enchant.Enchant) then table.insert(pendingBuild.RandomEnchants, copyRecord(enchant)) end
    end,
    RemoveRandomEnchant = function(enchant)
        local i = findEnchant(type(enchant) == "table" and enchant.Enchant or enchant)
        if i then table.remove(pendingBuild.RandomEnchants, i) end
    end,
    SetEnchantStacks = function(enchantID, stacks) setEnchantField(enchantID, "Stacks", math.max(1, stacks or 1)) end,
    SetEnchantLevel = function(enchantID, level) setEnchantField(enchantID, "Level", level) end,
    CanSetEnchantFlags = function() return true end,
    SetEnchantFlags = function(enchantID, flags) setEnchantField(enchantID, "Flags", flags) end,
    CanAddArmorType = function() return true end,
    AddArmorType = function(armor)
        local value = type(armor) == "table" and armor.Type or armor
        if value and not findType(pendingBuild.ArmorTypes, value) then
            table.insert(pendingBuild.ArmorTypes, type(armor) == "table" and copyRecord(armor) or { Type = armor, Comment = "" })
        end
    end,
    RemoveArmorType = function(armor)
        local i = findType(pendingBuild.ArmorTypes, type(armor) == "table" and armor.Type or armor)
        if i then table.remove(pendingBuild.ArmorTypes, i) end
    end,
    CanAddWeaponType = function() return true end,
    AddWeaponType = function(weapon)
        local value = type(weapon) == "table" and weapon.Type or weapon
        if value and not findType(pendingBuild.WeaponTypes, value) then
            table.insert(pendingBuild.WeaponTypes, type(weapon) == "table" and copyRecord(weapon) or { Type = weapon, Comment = "" })
        end
    end,
    RemoveWeaponType = function(weapon)
        local i = findType(pendingBuild.WeaponTypes, type(weapon) == "table" and weapon.Type or weapon)
        if i then table.remove(pendingBuild.WeaponTypes, i) end
    end,
    SetName = function(v) pendingBuild.Name = v or "" end,
    SetIcon = function(v) pendingBuild.Icon = v or "INV_Misc_QuestionMark" end,
    SetRoles = function(v) pendingBuild.Roles = v or "PLAYER_ROLE_NONE" end,
    SetPrimaryStat = function(v) pendingBuild.PrimaryStat = v or "" end,
    SetDifficultyRating = function(v) pendingBuild.DifficultyRating = v or Enum.BuildDifficulty.Standard end,
    SetCategory = function(v) pendingBuild.Category = v or Enum.BuildCategory.None end,
    SetDescription = function(v) pendingBuild.Description = v or "" end,
    SetComment = function(v) pendingBuild.Comment = v or "" end,
    -- The editor's spell picker runs through the Character Advancement filtered list; the
    -- editor never opens it here (no filtered set), so the count is honest at zero.
    SetFilteredEntries = function() end,
    GetNumFilteredEntries = function() return 0 end,
    GetFilteredEntryAtIndex = function() return nil end,
})
inertNamespace("C_Wildcard", { CanUseRapidRolling = function() return false end })
inertNamespace("C_MysticEnchant", {})        -- api/C_MysticEnchant.lua defines the real surface; this only catches strays
inertNamespace("C_MysticEnchantPreset", {})
----------------------------------------------------------------------------------------
-- 1b. Natives and glue the classic (Hero / Free-Pick) Character Advancement panel and the
--     Mystic Enchant panel reach on a stock client.
----------------------------------------------------------------------------------------
-- The saved CVars Ascension registers natively (FrameXML\GameCVars.lua); on stock they live in
-- the shim's saved variables through ab_CVar.lua. previewCharacterAdvancementChanges = "1" is
-- the pending-build editing mode both panels are verified against.
if C_CVar and C_CVar.RegisterSavedCVar then
    C_CVar.RegisterSavedCVar("caLastClass", "")
    C_CVar.RegisterSavedCVar("caLastSpec", "")
    C_CVar.RegisterSavedCVar("previewCharacterAdvancementChanges", "1")
    C_CVar.RegisterSavedCVar("allowMysticEnchantingUI", "1")
    C_CVar.RegisterSavedCVar("showTooltipID", "0")
end
if not IsSpellIDKnown then function IsSpellIDKnown(spellID) return IsSpellKnown(spellID) end end
-- Ascension's money natives (FrameXML\Util\CostUtil.lua formats every enchant / reforge
-- cost through them); copper amounts, as everywhere else in the client.
-- GetGoldForMoney(copper) -> gold, silver, copper (CostUtil:FormatCost unpacks all three).
if not GetGoldForMoney then
    function GetGoldForMoney(money)
        money = tonumber(money) or 0
        return math.floor(money / 10000), math.floor((money % 10000) / 100), money % 100
    end
end
-- C_Spell natives the panels poll per known ability: the trainer rank-up indicator. The port has
-- no trainer ranks for Character Advancement spells, so the highest learnable rank is the spell
-- itself and nothing is a trainer spell (FrameXML\Util\C_Spell.lua aliases IsTrainerSpell).
C_Spell = C_Spell or {}
if not C_Spell.GetMaxLearnableRank then function C_Spell.GetMaxLearnableRank(spellID) return spellID end end
if not C_Spell.IsTrainerSpell then function C_Spell.IsTrainerSpell() return false end end
if not IsTrainerSpell then function IsTrainerSpell() return false end end
-- On Ascension the Free-Pick essences were bag ITEMS (ItemData.ABILITY_ESSENCE 383080 /
-- TALENT_ESSENCE 383081) and the panels read them with GetItemCount; on the port the budgets
-- are numbers the module serves, so those two item counts answer with the remaining essence.
do
    local stockGetItemCount = GetItemCount
    function GetItemCount(item, ...)
        local id = tonumber(item) or (type(item) == "string" and tonumber(string.match(item, "item:(%d+)")))
        local CA = C_CharacterAdvancement
        if id == 383080 and CA and CA.GetRemainingAE then return math.max(0, CA.GetRemainingAE()) end
        if id == 383081 and CA and CA.GetRemainingTE then return math.max(0, CA.GetRemainingTE()) end
        return stockGetItemCount(item, ...)
    end
end
-- Ascension's item natives (FrameXML\Objects\Item.lua, CurrencyBar.lua) over stock GetItemInfo,
-- which answers only for items already in the client's cache; the fallbacks keep the currency
-- bars drawing while the essence items load.
local function itemInfo(item) if item == nil then return end return GetItemInfo(item) end
-- Ascension-only items the panels name before the client has ever seen them (FrameXML\Data\Items.lua).
local KNOWN_ITEM_NAMES = { [98463] = "Mystic Extract", [375250] = "Mark of Ascension", [383080] = "Ability Essence",
                           [383081] = "Talent Essence", [98570] = "Mystic Orb", [98462] = "Mystic Rune",
                           [992720] = "Untarnished Mystic Scroll" }
if not GetItemName then
    function GetItemName(item)
        local name = itemInfo(item)
        local id = tonumber(item) or (type(item) == "string" and tonumber(string.match(item, "item:(%d+)")))
        return name or KNOWN_ITEM_NAMES[id] or ("Item " .. tostring(item))
    end
end
if not GetItemQuality then function GetItemQuality(item) local _, _, quality = itemInfo(item) return quality or 1 end end
if not GetItemIcon then function GetItemIcon(item) local _, _, _, _, _, _, _, _, _, texture = itemInfo(item) return texture end end
if not GetItemIconInstant then function GetItemIconInstant(item) local texture = GetItemIcon(item) return texture and string.match(texture, "([^\\]+)$") or nil end end
if not GetItemLink then function GetItemLink(item) local _, link = itemInfo(item) return link end end
if not GetItemClassID then function GetItemClassID() return nil end end
if not GetItemSubClassID then function GetItemSubClassID() return nil end end
if not GetItemFlavorText then function GetItemFlavorText() return nil end end
if not GetItemInfoInstant then
    -- retail shape: itemID, itemType, itemSubType, itemEquipLoc, icon, classID, subclassID
    function GetItemInfoInstant(item)
        local name, link, _, _, _, itemType, itemSubType, _, equipLoc, texture = itemInfo(item)
        local id = tonumber(item) or (link and tonumber(string.match(link, "item:(%d+)")))
        if not name then return id end
        return id, itemType, itemSubType, equipLoc, texture, nil, nil
    end
end
if not GetRealmMaxLevel then function GetRealmMaxLevel() return MAX_PLAYER_LEVEL or 80 end end
if not SendSystemMessage then
    function SendSystemMessage(text)
        if UIErrorsFrame then UIErrorsFrame:AddMessage(tostring(text), 1.0, 0.1, 0.1, 1.0) end
        if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage(tostring(text), 1.0, 1.0, 0.0) end
    end
end
if not ShowForcedPrimaryStat then function ShowForcedPrimaryStat() return false end end
if not WildCard_LoadUI then function WildCard_LoadUI() return false end end
if not GetPrestigeLevel then function GetPrestigeLevel() return 0 end end
-- account / quest natives the Hero Architect touches when shown
C_AccountInfo = C_AccountInfo or {}
if not C_AccountInfo.GetGMLevel then function C_AccountInfo.GetGMLevel() return 0 end end
if not C_AccountInfo.GetCharacterAtIndex then function C_AccountInfo.GetCharacterAtIndex() return nil end end
C_Quest = C_Quest or {}
if not C_Quest.SendPathToAscensionEvent then function C_Quest.SendPathToAscensionEvent() end end
-- Draft / Hand of Fate / Skill Card picks (FrameXML\Util\DraftUtil.lua polls these natives on
-- PLAYER_ENTERING_WORLD); no draft realm exists on the port, so every pick query answers "none".
for _, name in ipairs({ "HasDraftModePick", "HasHandOfFatePick", "HasHandOfFateSacrificePick", "IsBuildDraftModeEnabled",
                        "IsCardSwapSacrifice", "IsLuckySkillCardDraft", "IsLuckySkillCardPick", "IsSkillCardDraft", "IsSkillCardPick" }) do
    if not _G[name] then _G[name] = function() return false end end
end
for _, name in ipairs({ "GetDraftModePickCount", "GetHandOfFatePickCount", "GetHandOfFateSacrificePickCount" }) do
    if not _G[name] then _G[name] = function() return 0 end end
end
for _, name in ipairs({ "GetDraftModePickSpellAtIndex", "GetHandOfFatePickSpellAtIndex", "GetHandOfFateSacrificePickSpellAtIndex", "GetSelectedHandOfFateSpell" }) do
    if not _G[name] then _G[name] = function() return nil end end
end
if not HasPrestigedOnce then function HasPrestigedOnce() return false end end
-- P7 (2026-09-10): natives the Skill Cards, Vanity and Wardrobe panels read. None of these
-- systems has a server on the port, so the answers are "nothing" rather than invented data.
C_ExtraActionButton = C_ExtraActionButton or {}
if not C_ExtraActionButton.GetNumExtraActionButtons then function C_ExtraActionButton.GetNumExtraActionButtons() return 0 end end
if not C_ExtraActionButton.GetExtraActionButtonAtIndex then function C_ExtraActionButton.GetExtraActionButtonAtIndex() return nil end end
if not C_ExtraActionButton.GetExtraActionButtonInfo then function C_ExtraActionButton.GetExtraActionButtonInfo() return nil end end
C_Gossip = C_Gossip or {}
if not C_Gossip.RedirectNPC then function C_Gossip.RedirectNPC() end end -- SkillCards.lua redirects an NPC's gossip to its frame
dprint = dprint or function() end                                        -- Ascension's debug print (GlueParent.lua: dprint = nop)
if not GetAscensionDonationPoints then function GetAscensionDonationPoints() return 0 end end
-- Item natives Ascension added (the Wardrobe, the Vanity store and the Skill Cards booster scan
-- call them); each is answered from the stock item cache, then from the collection catalogue.
if not GetItemInfoFromHyperlink then
    function GetItemInfoFromHyperlink(link) return tonumber(string.match(tostring(link or ""), "item:(%d+)")) end
end
if not GetInstantItemLink then
    function GetInstantItemLink(itemID)
        local _, link = GetItemInfo(itemID)
        return link or ("item:" .. tostring(itemID))
    end
end
if not TryCacheItem then function TryCacheItem(itemID) return GetItemInfo(itemID) ~= nil end end
if not GetInventoryItemTrueID then function GetInventoryItemTrueID(unit, slot) return GetInventoryItemID(unit, slot) end end
if not SetModelApplyComponents then function SetModelApplyComponents() end end
if not GetScaledCursorPosition then
    function GetScaledCursorPosition(frame)
        local x, y = GetCursorPosition()
        local scale = frame and frame.GetEffectiveScale and frame:GetEffectiveScale() or UIParent:GetEffectiveScale()
        return x / scale, y / scale
    end
end
do
    -- GetItemInfo's equip-location string -> Ascension's numeric inventory type (ItemTemplate.InventoryType)
    local INV_INDEX = { INVTYPE_HEAD = 1, INVTYPE_NECK = 2, INVTYPE_SHOULDER = 3, INVTYPE_BODY = 4, INVTYPE_CHEST = 5, INVTYPE_WAIST = 6,
        INVTYPE_LEGS = 7, INVTYPE_FEET = 8, INVTYPE_WRIST = 9, INVTYPE_HAND = 10, INVTYPE_FINGER = 11, INVTYPE_TRINKET = 12,
        INVTYPE_WEAPON = 13, INVTYPE_SHIELD = 14, INVTYPE_RANGED = 15, INVTYPE_CLOAK = 16, INVTYPE_2HWEAPON = 17, INVTYPE_BAG = 18,
        INVTYPE_TABARD = 19, INVTYPE_ROBE = 20, INVTYPE_WEAPONMAINHAND = 21, INVTYPE_WEAPONOFFHAND = 22, INVTYPE_HOLDABLE = 23,
        INVTYPE_AMMO = 24, INVTYPE_THROWN = 25, INVTYPE_RANGEDRIGHT = 26, INVTYPE_QUIVER = 27, INVTYPE_RELIC = 28 }
    local function record(itemID)
        local C = ASC.Collections
        return C and C.AppearanceRecordForItem and C.AppearanceRecordForItem(itemID) or nil
    end
    if not GetItemInventoryType then
        function GetItemInventoryType(itemID)
            local equipLoc = select(9, GetItemInfo(itemID))
            if equipLoc and equipLoc ~= "" then return INV_INDEX[equipLoc] or 0 end
            local r = record(tonumber(itemID))
            return r and r.inv or 0
        end
    end
    if not GetItemIconInstant then
        function GetItemIconInstant(itemID)
            local texture = select(10, GetItemInfo(itemID))
            if texture then return (string.gsub(texture, "^[Ii]nterface\\[Ii]cons\\", "")) end
            local r = record(tonumber(itemID))
            return r and r.icon or nil
        end
    end
    if not GetInventorySlotInfoByID then
        local names = { "HeadSlot", "NeckSlot", "ShoulderSlot", "ShirtSlot", "ChestSlot", "WaistSlot", "LegsSlot", "FeetSlot", "WristSlot",
                        "HandsSlot", "Finger0Slot", "Finger1Slot", "Trinket0Slot", "Trinket1Slot", "BackSlot", "MainHandSlot",
                        "SecondaryHandSlot", "RangedSlot", "TabardSlot", "AmmoSlot" }
        local byID
        function GetInventorySlotInfoByID(slotID)
            if not byID then
                byID = {}
                for _, name in ipairs(names) do
                    local id, texture = GetInventorySlotInfo(name)
                    if id then byID[id] = { name, texture } end
                end
            end
            local info = byID[slotID]
            if not info then return nil end
            return info[1], info[2]
        end
    end
end
-- debug-only item natives (the Wardrobe's ID tooltip lines, ItemMixin extras): nothing to answer on 3.3.5
if not GetItemClassID then function GetItemClassID() return nil end end
if not GetItemSubClassID then function GetItemSubClassID() return nil end end
if not GetItemTemplate then function GetItemTemplate() return nil end end
if not GetItemFlavorText then function GetItemFlavorText() return nil end end
if not GetItemPvEPower then function GetItemPvEPower() return 0 end end
if not GetItemPvPPower then function GetItemPvPPower() return 0 end end
if not IsSeasonalCollectionUnlocked then function IsSeasonalCollectionUnlocked() return false end end
if not OpenAscensionURL then function OpenAscensionURL(path) Stock.Log("OpenAscensionURL(" .. tostring(path) .. "): no web shop on this port") end end
-- Ascension's asynchronous item/quest/creature cache service (FrameXML\Objects\AsyncCallbackHandler.lua):
-- TryCache* asks the server for a record and a *_CACHE_REQUEST_SUCCESS event follows. On 3.3.5
-- GetItemInfo() on an unknown id is itself the request and no event exists, so the shim asks,
-- then polls the item cache for a few seconds and fires the event through the custom event bus.
ASC.Events.Define({ "ITEM_CACHE_REQUEST_SUCCESS", "QUEST_CACHE_REQUEST_SUCCESS", "CREATURE_CACHE_REQUEST_SUCCESS" })
C_AssetQueryService = C_AssetQueryService or {}
if not C_AssetQueryService.TryCacheItem then
    local pending = {}
    local function fire(itemID)
        pending[itemID] = nil
        ASC.Events.Fire("ITEM_CACHE_REQUEST_SUCCESS", itemID)
        -- and straight into the item listener (FrameXML\Objects\AsyncCallbackHandler.lua), the way
        -- its own ITEM_CACHE_REQUEST_SUCCESS handler would: the Wardrobe's model cells wait on it
        local listener = ItemQueryListener
        if listener and listener.FireCallbacks then pcall(listener.FireCallbacks, listener, itemID) end
    end
    function C_AssetQueryService.TryCacheItem(itemID)
        itemID = tonumber(itemID)
        if not itemID then return false end
        if GetItemInfo(itemID) then
            C_Timer.After(0, function() fire(itemID) end)
            return true
        end
        if not pending[itemID] then
            pending[itemID] = 0
            local function poll()
                if not pending[itemID] then return end
                if GetItemInfo(itemID) then return fire(itemID) end
                pending[itemID] = pending[itemID] + 1
                if pending[itemID] < 20 then C_Timer.After(0.5, poll) else pending[itemID] = nil end
            end
            C_Timer.After(0.5, poll)
        end
        return true
    end
end
if not C_AssetQueryService.TryCacheQuest then function C_AssetQueryService.TryCacheQuest() return false end end
if not C_AssetQueryService.TryCacheCreature then function C_AssetQueryService.TryCacheCreature() return false end end
if not C_UICamera then
    C_UICamera = setmetatable({}, { __index = function(t, k)
        local f = function() return nil end
        rawset(t, k, f)
        return f
    end })
end
-- Ascension extended the DressUpModel widget (the Wardrobe's models): SetDisplayInfo takes a
-- "loaded" callback, and a dozen camera / sequence / drag methods exist only there. The stock
-- widget shares one method table per widget type, so the extensions are added to it once:
-- the callback runs immediately, the rest are no-ops (the model simply keeps its default view).
do
    local ok, probe = pcall(CreateFrame, "DressUpModel", nil, UIParent)
    local mt = ok and probe and getmetatable(probe)
    local methods = mt and rawget(mt, "__index")
    if type(methods) == "table" then
        for _, name in ipairs({ "FreezeSequence", "PlaySequence", "StopSequence", "ApplyUICamera", "TryOnTransmogGear",
                                "SetSpellVisual", "SetCreatureDisplay", "SetEnableDragRotation", "SetEnableScrollZoom",
                                "SetEnableDragMove", "SetDragScale", "SetMinMaxDistance", "SetMaxPosOffset", "ShowRanged",
                                "ShowMelee", "SetSpell" }) do
            if not methods[name] then methods[name] = function() end end
        end
        if methods.SetDisplayInfo and not methods.ASC_SetDisplayInfoWrapped then
            local original = methods.SetDisplayInfo
            methods.SetDisplayInfo = function(self, displayID, callback, ...)
                local result = original(self, displayID, ...)
                if type(callback) == "function" then callback() end
                return result
            end
            methods.ASC_SetDisplayInfoWrapped = true
        end
    else
        Stock.Log("DressUpModel method table not reachable; the Wardrobe models will lack Ascension's extensions")
    end
    if probe then probe:Hide() end
end
C_Aura = C_Aura or {}
if not C_Aura.UnitHasAura then
    function C_Aura.UnitHasAura(unit, spellID)
        local name = GetSpellInfo(spellID)
        if not name then return false end
        for i = 1, 40 do
            local buff = UnitBuff(unit, i)
            if not buff then break end
            if buff == name then return true end
        end
        return false
    end
end
-- Free-Pick's "General" entries (1,803 class-agnostic abilities and talents) have no class
-- button in Ascension's panel because its ability browser (server-fed categories, not
-- recovered) listed them. Give them a class button instead: a pseudo-class GENERAL with one
-- tab, resolved through the same conversion functions the panel already uses.
do
    local util = CharacterAdvancementUtil
    if util then
        local toDBC, toFile, specToDBC, specToFile = util.GetClassDBCByFile, util.GetClassFileByDBC, util.GetSpecDBCByFile, util.GetSpecFileByDBC
        util.GetClassDBCByFile = function(f) if f == "GENERAL" then return "General" end return toDBC(f) end
        util.GetClassFileByDBC = function(d) if d == "General" then return "GENERAL" end return toFile(d) end
        util.GetSpecDBCByFile = function(f) if f == "GENERAL1" then return "General1" end return specToDBC(f) end
        util.GetSpecFileByDBC = function(d) if d == "General1" then return "GENERAL1" end return specToFile(d) end
    end
    if CHARACTER_ADVANCEMENT_CLASS_SPEC_ORDER and not CHARACTER_ADVANCEMENT_CLASS_SPEC_ORDER.GENERAL then
        CHARACTER_ADVANCEMENT_CLASS_SPEC_ORDER.GENERAL = { "GENERAL1" }
        table.insert(CHARACTER_ADVANCEMENT_CLASS_ORDER, 1, "GENERAL")
    end
    LOCALIZED_CLASS_NAMES_MALE = LOCALIZED_CLASS_NAMES_MALE or {}
    LOCALIZED_CLASS_NAMES_FEMALE = LOCALIZED_CLASS_NAMES_FEMALE or {}
    LOCALIZED_CLASS_NAMES_MALE.GENERAL = LOCALIZED_CLASS_NAMES_MALE.GENERAL or "General"
    LOCALIZED_CLASS_NAMES_FEMALE.GENERAL = LOCALIZED_CLASS_NAMES_FEMALE.GENERAL or "General"
    if RAID_CLASS_COLORS and not RAID_CLASS_COLORS.GENERAL then RAID_CLASS_COLORS.GENERAL = RAID_CLASS_COLORS.HERO or RAID_CLASS_COLORS.PRIEST end
    -- the class button asserts on a missing "class-round-<token>" atlas; General wears the Hero one
    if AtlasInfo and not AtlasInfo["class-round-general"] then AtlasInfo["class-round-general"] = AtlasInfo["class-round-hero"] end
    if C_ClassInfo and C_ClassInfo.GetSpecInfo then
        local getSpecInfo = C_ClassInfo.GetSpecInfo
        function C_ClassInfo.GetSpecInfo(classToken, specToken)
            local info = getSpecInfo(classToken, specToken)
            if info then return info end
            if string.upper(tostring(classToken)) == "GENERAL" then
                return { ID = 0, Class = "GENERAL", Spec = "GENERAL1", Name = "General", SpecFilename = "INV_Misc_Book_09", Description = "Abilities and talents any Hero may pick." }
            end
            -- the classic panel indexes the result without a nil check (CharacterAdvancement.lua:850, CAGate.lua:167)
            return { ID = 0, Class = string.upper(tostring(classToken)), Spec = string.upper(tostring(specToken)), Name = tostring(specToken), SpecFilename = "INV_Misc_QuestionMark" }
        end
    end
end
-- Ascension native: GetMaxLevel() (the realm's level cap; Hero Architect sizes its level slider by it)
if not GetMaxLevel then
    function GetMaxLevel() return MAX_PLAYER_LEVEL or 80 end
end
-- Ascension native: IsPassiveSpellID(spellID); stock IsPassiveSpell accepts a spell id too.
if not IsPassiveSpellID then
    function IsPassiveSpellID(spellID) return IsPassiveSpell(spellID) end
end
-- Ascension's PlaySound takes a retail SOUNDKIT id (SharedXML\Util\SoundKit.lua); stock takes
-- a SoundEntries name. Map the id back to its SOUNDKIT key and spell that the 3.3.5 way
-- (IG_MAINMENU_OPTION_CHECKBOX_ON -> igMainmenuOptionCheckboxOn, matched case-insensitively);
-- an unknown id is silent rather than an error that aborts the caller's OnClick.
do
    local native = PlaySound
    local names
    function PlaySound(sound, ...)
        if type(sound) == "number" then
            if not names then
                names = {}
                for key, id in pairs(SOUNDKIT or {}) do if type(id) == "number" then names[id] = key end end
            end
            local key = names[sound]
            if not key then return end
            local parts = {}
            for tok in string.gmatch(key, "[^_]+") do
                parts[#parts + 1] = #parts == 0 and string.lower(tok) or (string.upper(string.sub(tok, 1, 1)) .. string.lower(string.sub(tok, 2)))
            end
            sound = table.concat(parts)
        end
        local ok, a, b = pcall(native, sound, ...)
        if ok then return a, b end
    end
end

----------------------------------------------------------------------------------------
-- 2. Frame:GetAllAttributes() - native in Ascension; stock can only GetAttribute(name).
--    Returns { name = value } (that is what SharedXML\Util\Util.lua's AttributesToKeyValues
--    iterates with pairs). ASC_ATTRIBUTE_NAMES is generated at assembly time from every
--    <Attribute name=...> in the pack, so the probe covers exactly the names this pack's
--    XML can set.
----------------------------------------------------------------------------------------
local function GetAllAttributes(self)
    local out = {}
    for _, name in ipairs(ASC_ATTRIBUTE_NAMES or {}) do
        local value = self:GetAttribute(name)
        if value ~= nil then out[name] = value end
    end
    return out
end
for _, kind in ipairs({"Frame", "Button", "CheckButton", "ScrollFrame", "EditBox", "Slider", "StatusBar", "Model", "PlayerModel", "DressUpModel"}) do
    local mt = frameMT(kind)
    if mt and mt.__index and not mt.__index.GetAllAttributes then mt.__index.GetAllAttributes = GetAllAttributes end
end

----------------------------------------------------------------------------------------
-- 5. Panel plumbing from Ascension's UIParent.lua (LoadUI helpers, panel registration)
----------------------------------------------------------------------------------------
UIPanelWindows["Collections"] = { area = "center", pushable = 0, whileDead = 1 }
-- Ascension's UIParent.lua
if not GetUIScale then
    function GetUIScale() return UIParent:GetEffectiveScale() end
end

----------------------------------------------------------------------------------------
-- 6. Class name tables. SharedConstants fills LOCALIZED_CLASS_NAMES_* through the native
--    FillLocalizedClassList, which on this client reads the stock ChrClasses (patch-Y), so
--    the CA classes are missing; add every class identity the recovered dataset knows.
----------------------------------------------------------------------------------------
do
    local dataset = ASC.Config and ASC.Config.dataset
    local d = dataset and ASC.Data and ASC.Data.Datasets and ASC.Data.Datasets[dataset]
    local added = 0
    for _, row in ipairs(d and d.tables and d.tables.classIdentities or {}) do
        if row.Token and row.Name then
            LOCALIZED_CLASS_NAMES_MALE = LOCALIZED_CLASS_NAMES_MALE or {}
            LOCALIZED_CLASS_NAMES_FEMALE = LOCALIZED_CLASS_NAMES_FEMALE or {}
            if not LOCALIZED_CLASS_NAMES_MALE[row.Token] then
                LOCALIZED_CLASS_NAMES_MALE[row.Token] = row.Name
                LOCALIZED_CLASS_NAMES_FEMALE[row.Token] = row.Name
                added = added + 1
            end
        end
    end
    if UNLOCALIZED_CLASS_NAMES and LOCALIZED_CLASS_NAMES_MALE then
        for token, name in pairs(LOCALIZED_CLASS_NAMES_MALE) do UNLOCALIZED_CLASS_NAMES[name] = token end
    end
    Stock.Log("class names added: " .. added)
end
local function loader(addon)
    return function()
        local loaded, reason = LoadAddOn(addon)
        if not loaded then Stock.Log("LoadAddOn " .. addon .. " failed: " .. tostring(reason)) end
        return loaded
    end
end
if not CharacterAdvancement_LoadUI then
    function CharacterAdvancement_LoadUI()
        if IsCustomClass() then return loader("Ascension_CoATalents")() end
        return loader("Ascension_CharacterAdvancement")()
    end
end
BuildCreator_LoadUI = BuildCreator_LoadUI or loader("Ascension_BuildCreator")
SkillCards_LoadUI = SkillCards_LoadUI or loader("Ascension_SkillCards")
MysticEnchant_LoadUI = MysticEnchant_LoadUI or loader("Ascension_EnchantCollection")
-- Collections tabs whose data was never recovered (the vanity and transmog catalogues were
-- 0-byte JSON in every capture, see DESIGN.md §4): the tab opens an honest placeholder
-- panel under the frame name Collections expects, instead of loading an addon whose every
-- list would be empty. The original addons stay in the repository for when data appears.
-- Every Collections panel draws its own chrome (RaisedPortraitFrameTemplate, the template
-- CoATalentFrame uses) and Collections resizes itself to the panel, so the placeholder
-- inherits the same template and carries the tab's title and icon.
local function placeholderPanel(name, title, icon, body)
    return function()
        if _G[name] then return true end
        local ok, f = pcall(CreateFrame, "Frame", name, Collections, "RaisedPortraitFrameTemplate")
        if not ok or not f then f = CreateFrame("Frame", name, Collections) end
        -- same geometry as CoATalentFrame (CoATalentFrame.xml): the panel IS the visible window
        f:SetWidth(1294)
        f:SetHeight(666)
        f:SetPoint("BOTTOM", Collections, "BOTTOM", 0, 8)
        f:SetFrameStrata("DIALOG")
        f:Hide()
        if PortraitFrame_SetTitle then pcall(PortraitFrame_SetTitle, f, title) end
        if PortraitFrame_SetIcon then pcall(PortraitFrame_SetIcon, f, icon) end
        local h = f:CreateFontString(nil, "OVERLAY", "GameFontNormalHuge")
        h:SetPoint("TOP", f, "TOP", 0, -150)
        h:SetText(title)
        local t = f:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
        t:SetPoint("TOP", h, "BOTTOM", 0, -24)
        t:SetWidth(560)
        t:SetJustifyH("CENTER")
        t:SetText(body)
        return true
    end
end
-- P7 (2026-09-10): the catalogues came from the CoA repack's VanityCollection / Appearances /
-- ItemAppearances DBCs (tools/gen_collection_data.py, packed with --collections). When the pack
-- carries them the ORIGINAL addons load over the shim's read-only C_VanityCollection /
-- C_Appearance* (api/); a pack built without them keeps the honest placeholder.
local hasVanity = ASC.Collections and ASC.Collections.HasVanity and ASC.Collections.HasVanity()
local hasAppearances = ASC.Collections and ASC.Collections.HasAppearances and ASC.Collections.HasAppearances()
VanityCollection_LoadUI = VanityCollection_LoadUI or (hasVanity and loader("Ascension_VanityCollection"))
    or placeholderPanel("StoreCollectionFrame", VANITY or "Vanity", "Interface\\icons\\INV_Chest_Awakening",
    "This pack was built without the vanity catalogue (tools/gen_collection_data.py, build_addon_pack.py --collections).\n\n"
    .. "The original Ascension_VanityCollection addon loads here when the catalogue is present.")
AppearanceUI_LoadUI = AppearanceUI_LoadUI or (hasAppearances and loader("Ascension_AppearanceUI"))
    or placeholderPanel("AppearanceWardrobeFrame", WARDROBE or "Wardrobe", "Interface\\Icons\\inv_arcane_orb",
    "This pack was built without the appearance catalogue (tools/gen_collection_data.py, build_addon_pack.py --collections).\n\n"
    .. "The original Ascension_AppearanceUI addon loads here when the catalogue is present.")
-- Ascension_Collections is LoadOnDemand; Ascension's UIParent loads it on first use.
Collections_LoadUI = Collections_LoadUI or loader("Ascension_Collections")
-- Collections decides its tab set (CoA talent panel vs the classic Hero panel, Mystic Enchants
-- or not) from the class identity at the moment it is built, and that identity is the server's
-- STATE. Anything that builds Collections therefore waits for the first STATE; before it the
-- shim only knows the pack's default identity, which is wrong on every other realm mode.
local function whenConnected(fn, tries)
    if ASC.Live and ASC.Live.State and ASC.Live.State.connected then return fn() end
    tries = tries or 0
    if tries >= 40 then
        Stock.Log("no STATE from mod-ascension-ca after 20 s; opening with the pack's default identity")
        return fn()
    end
    C_Timer.After(0.5, function() whenConnected(fn, tries + 1) end)
end
Stock.WhenConnected = whenConnected
local function OpenCharacterAdvancement()
    whenConnected(function()
        if not Collections then Collections_LoadUI() end
        if not Collections then Stock.Log("Collections frame is not loaded") return false end
        local ok, err = pcall(Collections.GoToTab, Collections, Collections.Tabs.CharacterAdvancement)
        if not ok then Stock.Log("GoToTab failed: " .. tostring(err)) end
        return ok
    end)
    return true
end
Stock.OpenCharacterAdvancement = OpenCharacterAdvancement
local function openTab(tabName)
    whenConnected(function()
        if not Collections then Collections_LoadUI() end
        if not Collections then Stock.Log("Collections frame is not loaded") return false end
        local id = Collections.Tabs[tabName]
        if not id then Stock.Log("no " .. tabName .. " tab for this character") return false end
        local ok, err = pcall(Collections.GoToTab, Collections, id)
        if not ok then Stock.Log("GoToTab(" .. tabName .. ") failed: " .. tostring(err)) end
        return ok
    end)
    return true
end
function Stock.OpenHeroArchitect() return openTab("HeroArchitect") end
function Stock.OpenMysticEnchants() return openTab("MysticEnchants") end
function Stock.OpenVanity() return openTab("Vanity") end
function Stock.OpenWardrobe() return openTab("Wardrobe") end
-- Ascension builds the Skill Cards tab for Hero characters and shows it only in the Draft and
-- WildCard game modes (Collections.lua OnShow); the port has neither mode, so the tab is shown
-- on request. The panel is Ascension's own over an empty collection (api/C_SkillCard.lua).
function Stock.OpenSkillCards()
    whenConnected(function()
        if not Collections then Collections_LoadUI() end
        if not Collections then Stock.Log("Collections frame is not loaded") return false end
        local id = Collections.Tabs.SkillCards
        if not id then Stock.Log("no Skill Cards tab: Ascension builds it for Hero (Free-Pick) characters only") return false end
        local ok, err = pcall(Collections.GoToTab, Collections, id)
        if not ok then Stock.Log("GoToTab(SkillCards) failed: " .. tostring(err)) return false end
        C_Timer.After(0, function()
            if Collections.ShowTabID then pcall(Collections.ShowTabID, Collections, id) end
            pcall(Collections.GoToTab, Collections, id)
        end)
        return true
    end)
    return true
end
if not ToggleCollections then
    function ToggleCollections()
        if Collections and Collections:IsShown() then HideUIPanel(Collections) return end
        OpenCharacterAdvancement()
    end
end

----------------------------------------------------------------------------------------
-- 6b. UI scale. Ascension's UIParent.lua (UIParent_OnEvent, PLAYER_ENTERING_WORLD and
--     DISPLAY_SIZE_CHANGED) scales UIParent to 0.9 whenever the useUiScale cvar is off and
--     no addon has called UIParent:SetScale itself; the Collections panel is laid out for
--     that space (it is wider than 1024 at scale 1, so it hangs off a 1024x768 screen).
----------------------------------------------------------------------------------------
do
    local scaler = CreateFrame("Frame")
    scaler:RegisterEvent("PLAYER_ENTERING_WORLD")
    scaler:RegisterEvent("DISPLAY_SIZE_CHANGED")
    scaler:SetScript("OnEvent", function()
        if GetCVar("useUiScale") ~= "1" and UIParent.useScaledUI ~= false then
            C_Timer.After(0, function()
                if GetCVar("useUiScale") ~= "1" and UIParent.useScaledUI ~= false then
                    UIParent:SetScale(0.9)
                    UIParent.rescaled = true
                end
            end)
        end
    end)
end

----------------------------------------------------------------------------------------
-- 7. Slash commands (error capture itself lives in aa_StockPrelude.lua)
----------------------------------------------------------------------------------------
SLASH_ASCERRORS1 = "/ascerrors"
SlashCmdList.ASCERRORS = function()
    for i, e in ipairs(AscensionShimDB.errors) do DEFAULT_CHAT_FRAME:AddMessage(i .. ": " .. e) end
    DEFAULT_CHAT_FRAME:AddMessage(#AscensionShimDB.errors .. " error(s) recorded")
end
SLASH_ASCCA1 = "/ca"
SlashCmdList.ASCCA = function(msg)
    local verb, arg = string.match(msg or "", "^%s*(%S*)%s*(.-)%s*$")
    if verb == "enchants" then return Stock.OpenMysticEnchants() end
    if verb == "architect" or verb == "builds" then return Stock.OpenHeroArchitect() end
    if verb == "vanity" then return Stock.OpenVanity() end
    if verb == "wardrobe" then return Stock.OpenWardrobe() end
    if verb == "skillcards" or verb == "cards" then return Stock.OpenSkillCards() end
    if verb == "inspect" and arg ~= "" and ASC.Live and ASC.Live.Inspect then
        local ok, why = ASC.Live.Inspect(arg, function(info, reason)
            if not info then DEFAULT_CHAT_FRAME:AddMessage("|cffff4040Ascension:|r inspect " .. arg .. " failed (" .. tostring(reason) .. ")") return end
            local n = 0
            for _ in pairs(info.known) do n = n + 1 end
            local spec = info.spec ~= 0 and C_ClassInfo.GetSpecInfoByID(info.spec)
            DEFAULT_CHAT_FRAME:AddMessage(string.format("|cff66ccffAscension:|r %s is a level %d %s (%s), %d advancement entries known",
                info.name, info.level, tostring(info.class), spec and spec.Name or "no specialization", n))
        end)
        if not ok then DEFAULT_CHAT_FRAME:AddMessage("|cffff4040Ascension:|r " .. tostring(why)) end
        return
    end
    OpenCharacterAdvancement()
end
----------------------------------------------------------------------------------------
-- 8. Development probe: a few seconds after entering the world, report any visible frame
--    that has the keyboard enabled (such a frame swallows every key, chat included), then
--    auto-open the CA tab when Stock.autoOpenPanel is set, so tests need no chat input.
----------------------------------------------------------------------------------------
Stock.autoOpenPanel = false   -- dev probe: open the CA tab 6 s after entering the world
Stock.debugProbes = false     -- dev probe: log every visible keyboard-enabled frame at that point
-- Test helpers (the harness drives the UI through /run lines that must stay short).
function Stock.FindNode(entryID)
    local f = EnumerateFrames()
    while f do
        if f.entry and f.IsVisible and f:IsVisible() and (f.entry.ID == entryID or f.entryID == entryID) then return f end
        f = EnumerateFrames(f)
    end
end
function Stock.FindButton(text)
    local f = EnumerateFrames()
    while f do
        if f.GetObjectType and f:GetObjectType() == "Button" and f:IsVisible() and f.GetText and f:GetText() == text then return f end
        f = EnumerateFrames(f)
    end
end
local probe = CreateFrame("Frame")
probe:RegisterEvent("PLAYER_ENTERING_WORLD")
probe:SetScript("OnEvent", function()
    C_Timer.After(6, function()
        if Stock.debugProbes then
            -- A shown keyboard-enabled frame eats every key (see the empty-keybindings trap);
            -- this census names them so a regression is visible in the server log.
            local f, n = EnumerateFrames(), 0
            while f do
                if f.IsKeyboardEnabled and f:IsKeyboardEnabled() and f:IsVisible() then
                    n = n + 1
                    local parent = f:GetParent()
                    Stock.Log("keyboard frame: " .. tostring(f:GetName()) .. " parent=" .. tostring(parent and parent:GetName()))
                end
                f = EnumerateFrames(f)
            end
            Stock.Log("keyboard-enabled visible frames: " .. n)
        end
        if Stock.autoOpenPanel then
            Stock.Log("auto-opening the Character Advancement tab")
            OpenCharacterAdvancement()
            Stock.Log("log lines after open: " .. #AscensionShimDB.errors)
        end
    end)
end)
Stock.Log("stock shim ready: class " .. tostring((Stock.Identity() or {}).Name))

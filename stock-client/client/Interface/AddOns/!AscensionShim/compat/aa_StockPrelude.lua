-- aa_StockPrelude.lua: what must exist BEFORE Ascension's SharedXML/FrameXML compat files load
-- on a stock 3.3.5a client: error capture, retail-era Lua globals, fallback strings, the
-- class-identity wrappers (SharedConstants calls IsCustomClass() at load time), and stubs
-- for natives the compat files touch while loading. Every definition is guarded so a real
-- native wins.
ASC = ASC or {}
ASC.Stock = ASC.Stock or {}
local Stock = ASC.Stock

----------------------------------------------------------------------------------------
-- Error capture: buffer at load time, then chat + the ASC addon channel (server log)
----------------------------------------------------------------------------------------
AscensionShimDB = AscensionShimDB or {}
AscensionShimDB.errors = {}
Stock.inWorld = false
local queue = {}
local CR = string.char(13)
local LF = string.char(10)
local function oneline(text)
    text = tostring(text)
    text = string.gsub(text, CR, " ")
    text = string.gsub(text, LF, " | ")
    return text
end
local function send(text)
    local me = UnitName("player")
    if not (me and SendAddonMessage) then return end
    local n = 0
    for i = 1, #text, 200 do
        n = n + 1
        if n > 3 then break end
        local prefix = (n > 1) and ("+" .. n .. " ") or ""
        pcall(SendAddonMessage, "ASC", "CMD\tLOG\t" .. prefix .. string.sub(text, i, i + 199), "WHISPER", me)
    end
end
function Stock.Log(text)
    text = oneline(text)
    table.insert(AscensionShimDB.errors, text)
    if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage("|cffff6060ASC:|r " .. text) end
    if Stock.inWorld then send(text) else queue[#queue + 1] = text end
end
local previous = geterrorhandler()
seterrorhandler(function(msg)
    local stack = debugstack and debugstack(2, 4, 0) or ""
    local text = oneline(msg) .. " @@ " .. oneline(stack)
    Stock.Log(string.sub(text, 1, 600))
    if previous and previous ~= Stock.Log then pcall(previous, msg) end
end)
local flusher = CreateFrame("Frame")
flusher:RegisterEvent("PLAYER_ENTERING_WORLD")
flusher:SetScript("OnEvent", function()
    Stock.inWorld = true
    for _, t in ipairs(queue) do send(t) end
    queue = {}
    send("prelude: " .. #AscensionShimDB.errors .. " error(s) so far")
end)

----------------------------------------------------------------------------------------
-- Retail-era globals
----------------------------------------------------------------------------------------
if not securecallfunction then function securecallfunction(func, ...) return func(...) end end
if not secureexecuterange then
    function secureexecuterange(tbl, func, ...) for k, v in pairs(tbl) do func(k, v, ...) end end
end
if not issecretvalue then function issecretvalue() return false end end
if not issecretable then function issecretable() return false end end
if not forceinsecure then function forceinsecure() end end
if not canaccessvalue then function canaccessvalue() return true end end
if not canaccesstable then function canaccesstable() return true end end
if not nop then function nop() end end
if not GetTimePreciseSec then function GetTimePreciseSec() return GetTime() end end
if not debugprofilestop then function debugprofilestop() return GetTime() * 1000 end end
if not InGlue then function InGlue() return false end end
if not C_Timer then
    C_Timer = {}
    local driver = CreateFrame("Frame")
    local pending = {}
    driver:SetScript("OnUpdate", function()
        local now = GetTime()
        for i = #pending, 1, -1 do
            local t = pending[i]
            if t.cancelled then
                table.remove(pending, i)
            elseif now >= t.at then
                if t.ticker then
                    t.at = now + t.period
                    if t.iterations then t.iterations = t.iterations - 1; if t.iterations <= 0 then t.cancelled = true end end
                else
                    table.remove(pending, i)
                end
                local ok, err = pcall(t.callback, t)
                if not ok then Stock.Log(err) end
            end
        end
    end)
    local function newTimer(seconds, callback, ticker, iterations)
        local t = { at = GetTime() + seconds, period = seconds, callback = callback, ticker = ticker, iterations = iterations }
        t.Cancel = function(self) self.cancelled = true end
        t.IsCancelled = function(self) return self.cancelled == true end
        pending[#pending + 1] = t
        return t
    end
    function C_Timer.After(seconds, callback) newTimer(seconds, callback, false) end
    function C_Timer.NewTimer(seconds, callback) return newTimer(seconds, callback, false) end
    function C_Timer.NewTicker(seconds, callback, iterations) return newTimer(seconds, callback, true, iterations) end
end

----------------------------------------------------------------------------------------
-- Natives touched while the compat files load
----------------------------------------------------------------------------------------
if not ReadCustomWTF then function ReadCustomWTF() return nil end end
if not WriteCustomWTF then function WriteCustomWTF() return false end end
if not LoadAscensionContentJSON then function LoadAscensionContentJSON() return nil end end
if not DecodeJSON then function DecodeJSON() return nil end end
if not GetCustomGameMode then function GetCustomGameMode() return 0 end end
if not Internal_CopyToClipboard then function Internal_CopyToClipboard(text) Stock.Log("clipboard: " .. tostring(text)) end end
local function stubNamespace(name, members)
    local t = _G[name] or {}
    _G[name] = t
    for k, v in pairs(members) do if t[k] == nil then t[k] = v end end
    setmetatable(t, { __index = function(_, k)
        if type(k) == "string" and string.match(k, "^%u") then
            Stock.Log(name .. "." .. k .. " is not implemented (stub returned false)")
            local f = function() return false end
            rawset(t, k, f)
            return f
        end
    end })
end
stubNamespace("C_Realm", {
    IsLive = function() return true end, IsSeasonal = function() return false end, IsPTR = function() return false end,
    IsDevelopment = function() return false end, IsLeague = function() return false end, IsCustom = function() return false end,
    GetRealmType = function() return 2 end, GetMaxLevel = function() return 80 end, GetRealmName = function() return GetRealmName() end,
    GetRealmID = function() return 1 end, GetExpansionLevel = function() return 2 end,
})
stubNamespace("C_Config", {
    GetBoolConfig = function() return false end, GetIntConfig = function() return 0 end,
    GetStringConfig = function() return "" end, GetFloatConfig = function() return 0 end,
})
C_Logger = C_Logger or {}
for _, level in ipairs({"Error", "Warning", "Info", "Debug"}) do
    if not C_Logger[level] then
        C_Logger[level] = function(fmt, ...)
            local ok, msg = pcall(string.format, tostring(fmt), ...)
            Stock.Log(level .. ": " .. (ok and msg or tostring(fmt)))
        end
    end
end

----------------------------------------------------------------------------------------
-- Global strings Ascension ships natively (fallback English; stock values win)
----------------------------------------------------------------------------------------
local strings = {
    COA_CA_TITLE = "Character Advancement", CHARACTER_ADVANCEMENT = "Character Advancement",
    CHARACTER_ADVANCEMENT_TOOLTIP = "Abilities, talents and masteries", COA_CA_AVAILABLE_SPECIALIZATIONS = "Available Specializations",
    HERO_ARCHITECT = "Hero Architect", HERO_ARCHITECT_TOOLTIP = "Community builds", TALENTS_IMPORT_HERO_ARCHITECT = "Import from Hero Architect",
    UNLOCK_SKILL_CARDS_TITLE = "Skill Cards", BOOSTER_TAB_SUBTEXT = "Booster", VANITY = "Vanity", VANITY_TOOLTIP = "Vanity collection",
    MYSTIC_ENCHANT = "Mystic Enchants", MYSTIC_ENCHANT_TOOLTIP = "Mystic Enchant collection", WARDROBE = "Wardrobe", WARDROBE_TOOLTIP = "Appearances",
    FEATURE_BECOMES_AVAILABLE_AT_LEVEL = "Unlocks at level %d", MYSTIC_ENCHANTING_ALTAR_UNLOCK = "Unlocks at level %d",
    TIP_OPEN_WARDROBE_TO_CHANGE_TRANSMOG = "Open the Wardrobe to change your appearance.",
    CA_UNABLE_TO_SAVE_PENDING_BUILD = "Unable to save pending changes.", BUILD_CREATOR_NO_BUILDS = "No builds found.",
    BUILD_RATING_S = "Rating: %s", BUILD_CREATOR_ROLE_S = "Role: %s", BUILD_CREATOR_ROLE_TANK = "Tank", BUILD_CREATOR_ROLE_HEALER = "Healer",
    BUILD_CREATOR_ROLE_DAMAGER = "Damage", BUILD_CREATOR_ROLE_LEADER = "Leader", BUILD_URL_COPIED_TO_CLIPBOARD = "Build link copied.",
    SAMPLE_ABILITIES = "Sample Abilities", SAVE_CHANGES = "Save Changes", DEACTIVATE_BUILD = "Deactivate Build", CHANGE_TALENTS = "Change Talents",
    VIEW_TALENTS = "View Talents", ACTIVATE_TALENTS = "Activate", ACTIVE = "Active", DISABLED = "Disabled",
    SPEC_COMPLEXITY_1 = "Simple", SPEC_COMPLEXITY_2 = "Moderate", SPEC_COMPLEXITY_3 = "Complex", SPEC_COMPLEXITY_4 = "Very Complex",
    -- CoASpecChoiceMixin looks up SPEC_COMPLEXITY_<DifficultyRating:upper()>; the recovered
    -- spec data carries Normal / Medium / Hard (59 / 28 / 14 specs); wording is a placeholder.
    SPEC_COMPLEXITY_EASY = "Easy", SPEC_COMPLEXITY_NORMAL = "Normal", SPEC_COMPLEXITY_MEDIUM = "Medium",
    SPEC_COMPLEXITY_HARD = "Hard", SPEC_COMPLEXITY_ADVANCED = "Advanced", SPEC_COMPLEXITY_EXPERT = "Expert",
    TALENT_FRAME_RESET_BUTTON_DROPDOWN_TITLE = "Reset", TALENT_FRAME_RESET_BUTTON_DROPDOWN_LEFT = "Reset class tree",
    TALENT_FRAME_RESET_BUTTON_DROPDOWN_RIGHT = "Reset specialization tree", TALENT_FRAME_RESET_BUTTON_DROPDOWN_ALL = "Reset all",
    TALENT_FRAME_DISCARD_CHANGES_BUTTON_TOOLTIP = "Discard pending changes", TALENT_FRAME_GATE_TOOLTIP_FORMAT = "Requires %d points spent",
    TOOLTIP_TALENT_RANK = "Rank %d/%d", TOOLTIP_TALENT_LEARN = "Click to learn", TOOLTIP_TALENT_UNLEARN = "Right-click to unlearn",
    TALENT_SPEND_MORE_POINTS = "Spend more points in this tree to unlock", CLOSE_CHARACTER_ADVANCEMENT_UNSAVED_PENDING_CHANGES = "You have unsaved changes.",
    CONFIRM_DEACTIVATE_BUILD = "Deactivate this build?", CHARACTER_ADVANCEMENT_IMPORT_PENDING_BUILD = "Import this build?",
    CONFIRM_APPLY_PENDING_BUILD = "Apply pending changes?", APPLY = "Apply", DISCARD = "Discard", GO_BACK = "Go Back", DEACTIVATE = "Deactivate",
    IMPORT_BUILD = "Import Build",
    MELEE_DAMAGER = "Melee Damage", RANGED_DAMAGER = "Ranged Damage", TANK = "Tank", HEALER = "Healer", DAMAGER = "Damage",
    LEADER = "Leader", HYBRID = "Hybrid", CASTER_DAMAGER = "Caster Damage", SUPPORT = "Support", GENERIC = "Generic",
    CLOTH = "Cloth", LEATHER = "Leather", MAIL = "Mail", PLATE = "Plate",
    ARMOR_TYPE_CLOTH = "Cloth", ARMOR_TYPE_LEATHER = "Leather", ARMOR_TYPE_MAIL = "Mail", ARMOR_TYPE_PLATE = "Plate",
    ARMOR_TYPE_NAME = "%s Armor", ARMOR_TYPE_WITH_ICON = "%s %s", ROLE_TEXT_WITH_ICON = "%s %s",
}
for k, v in pairs(strings) do if _G[k] == nil then _G[k] = v end end
-- CoA primary stats, in C_PrimaryStat.internalIds order (FrameXML\Util\C_PrimaryStat.lua):
-- 1149 Strength, 1150 Agility, 1151 Intellect, 1152 Spirit, (Stamina reserved), 18149 Duality.
local primaryStats = { "Strength", "Agility", "Intellect", "Spirit", "Stamina", "Duality", "Primary stat 7" }
for i, name in ipairs(primaryStats) do if _G["PRIMARY_STAT_" .. i .. "_NAME_COA"] == nil then _G["PRIMARY_STAT_" .. i .. "_NAME_COA"] = name end end

----------------------------------------------------------------------------------------
-- Class identity. The preview context is started here (Bootstrap re-runs it harmlessly)
-- because SharedConstants/Collections evaluate IsCustomClass() while they load.
-- Same 4-value contract as Ascension's GlobalOverwrites.lua:88-107.
----------------------------------------------------------------------------------------
if ASC.Preview and ASC.Config and not ASC.Preview.Context then
    local ok, err = pcall(ASC.Preview.Begin, ASC.Config.dataset, ASC.Config.classByte, ASC.Config.level)
    if not ok then Stock.Log("preview identity failed: " .. tostring(err)) end
end
function Stock.Identity()
    local c = ASC.Preview and ASC.Preview.Context
    return c and c.identity or nil
end
_UnitClass = _UnitClass or UnitClass
_UnitClassBase = _UnitClassBase or UnitClassBase
local function isPlayer(unit)
    return unit == "player" or (unit ~= nil and UnitIsUnit ~= nil and UnitIsUnit(unit, "player"))
end
local function classTuple(name, token)
    local E = Enum
    return name, token, E and E.Class and E.Class[token] or nil, E and E.ClassMask and E.ClassMask[token] or nil
end
function UnitClass(unit)
    local id = Stock.Identity()
    if id and isPlayer(unit) then return classTuple(id.Name, id.Token) end
    return classTuple(_UnitClass(unit))
end
function UnitClassBase(unit)
    local id = Stock.Identity()
    if id and isPlayer(unit) then return classTuple(id.Name, id.Token) end
    return classTuple(_UnitClassBase(unit))
end
function UnitClassID(unit) local _, _, id = UnitClassBase(unit) return id end
function UnitClassMask(unit) local _, _, _, mask = UnitClassBase(unit) return mask or 0 end
function GetClassInfo(classID)
    if not classID then return nil end
    local token
    if Enum and Enum.Class and Enum.Class[classID] then token = classID; classID = Enum.Class[token]
    elseif Enum and Enum.ClassFile then token = Enum.ClassFile[classID] end
    local id = Stock.Identity()
    local name = (id and id.Token == token) and id.Name or (LOCALIZED_CLASS_NAMES_MALE and token and LOCALIZED_CLASS_NAMES_MALE[token]) or token
    return name, token, classID
end

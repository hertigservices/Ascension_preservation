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
VanityCollection_LoadUI = VanityCollection_LoadUI or loader("Ascension_VanityCollection")
MysticEnchant_LoadUI = MysticEnchant_LoadUI or loader("Ascension_EnchantCollection")
AppearanceUI_LoadUI = AppearanceUI_LoadUI or loader("Ascension_AppearanceUI")
-- Ascension_Collections is LoadOnDemand; Ascension's UIParent loads it on first use.
Collections_LoadUI = Collections_LoadUI or loader("Ascension_Collections")
local function OpenCharacterAdvancement()
    if not Collections then Collections_LoadUI() end
    if not Collections then Stock.Log("Collections frame is not loaded") return false end
    local ok, err = pcall(Collections.GoToTab, Collections, Collections.Tabs.CharacterAdvancement)
    if not ok then Stock.Log("GoToTab failed: " .. tostring(err)) end
    return ok
end
Stock.OpenCharacterAdvancement = OpenCharacterAdvancement
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
SlashCmdList.ASCCA = function() OpenCharacterAdvancement() end
----------------------------------------------------------------------------------------
-- 8. Development probe: a few seconds after entering the world, report any visible frame
--    that has the keyboard enabled (such a frame swallows every key, chat included), then
--    auto-open the CA tab when Stock.autoOpenPanel is set, so tests need no chat input.
----------------------------------------------------------------------------------------
Stock.autoOpenPanel = true
local probe = CreateFrame("Frame")
probe:RegisterEvent("PLAYER_ENTERING_WORLD")
probe:SetScript("OnEvent", function()
    C_Timer.After(6, function()
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
        if Stock.autoOpenPanel then
            Stock.Log("auto-opening the Character Advancement tab")
            OpenCharacterAdvancement()
            Stock.Log("errors after open: " .. #AscensionShimDB.errors)
        end
    end)
end)
Stock.Log("stock shim ready: class " .. tostring((Stock.Identity() or {}).Name))

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
-- One list per session. SavedVariablesPerCharacter replaces AscensionShimDB after these files
-- have run, so the list is re-attached on ADDON_LOADED rather than trusted at load time.
local session = {}
AscensionShimDB = AscensionShimDB or {}
AscensionShimDB.errors = session
Stock.Errors = session
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
    table.insert(session, text)
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
flusher:RegisterEvent("ADDON_LOADED")
flusher:SetScript("OnEvent", function(_, event, addon)
    if event == "ADDON_LOADED" then
        if addon == "!AscensionShim" then
            AscensionShimDB = AscensionShimDB or {}
            AscensionShimDB.errors = session
            -- saved texts win over anything written before the variables arrived
            for k, v in pairs(AscensionShimDB.customWTF or {}) do Stock.customWTF[k] = v end
            AscensionShimDB.customWTF = Stock.customWTF
        end
        return
    end
    Stock.inWorld = true
    for _, t in ipairs(queue) do send(t) end
    queue = {}
    send("prelude: " .. #session .. " line(s) logged before world entry")
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
-- Ascension's natives read/write WTF\<name>.wtf next to the account data (MysticEnchantManagerUtil
-- keeps its preset db there). Stock Lua cannot touch files, so the texts live in the shim's saved
-- variables instead: AscensionShimDB.customWTF, re-attached on ADDON_LOADED like the error list.
local customWTF = {}
Stock.customWTF = customWTF
if not ReadCustomWTF then function ReadCustomWTF(name) return customWTF[tostring(name)] end end
if not WriteCustomWTF then
    function WriteCustomWTF(name, text)
        customWTF[tostring(name)] = text
        return true
    end
end
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
-- Strings the classic (Hero / Free-Pick) Character Advancement panel, its static popups and the
-- Mystic Enchant panel read; Extensions.dll supplied them on Ascension. Wording is ours where
-- no capture recorded the original; the reason keys are Enum.CALearnResult / CAUnlearnResult.
local more = {
    ABILITY_ESSENCE_TOTAL = "%s", TALENT_ESSENCE_TOTAL = "%s", MASTERY = "Mastery", LOCATE = "Locate", REROLL = "Reroll",
    MY_SPELLS_TAB = "Spells", MY_SPELLS_TAB_TITLE = "My Spells", MY_SPELLS_TAB_TOOLTIP = "Everything you know, and everything you can learn.",
    MY_SPECS_TAB = "Specs", MY_SPECS_TAB_TITLE = "Specializations", MY_SPECS_TAB_TOOLTIP = "Saved talent loadouts.",
    ENTER_SPEC_NAME_HELP = "Enter a name for this specialization", NO_PRIMARY_STAT = "No primary stat chosen",
    CHOOSE_A_PRIMARY_STAT = "Choose a primary stat", PRIMARY_STAT = "Primary Stat",
    RESET_BUILD = "Reset Build", RESET_BUILD_TOOLTIP = "Unlearn everything and start over.", RESET_ALL_TALENTS = "Reset All Talents",
    RESET_ALL_TALENTS_TOOLTIP = "Unlearn all talents.", SHARE_BUILD = "Share Build", SHARE_BUILD_TOOLTIP = "Copy a link to this build.",
    SHOW_RARITIES = "Show Rarities", HIDE_RARITIES = "Hide Rarities", SHOW_POSSIBLE_SWAPS = "Show possible swaps",
    SPELL_AVAILABLE = "Spell available", SPELL_AVAILABLE_FIND_TRAINER = "Visit a trainer to learn it.",
    SPELL_RANK_AVAILABLE = "New rank available", SPELL_RANK_FIND_TRAINER = "Visit a trainer to learn the next rank.",
    SUGGESTED_ABILITY = "Suggested", SUGGESTED_ABILITY_TOOLTIP = "Suggested for your build.", UNKNOWN_OBJECT = "Unknown",
    LOCK_SPELL = "Lock", UNLOCK_SPELL = "Unlock", CA_MARK_FOR_SWAP = "Mark for swap", CA_SWAP_BLUE = "Swap",
    CA_RAPID_ROLLING = "Rapid Rolling", CA_RAPID_ROLLING_TEXT = "Roll abilities quickly", RAPID_ROLL_UNAVAILABLE = "Rapid rolling is unavailable until level %d.",
    DRAFT_MODE = "Draft", WILDCARD_MODE = "WildCard", TOGGLE_DRAFT_CARDS = "Draft Cards", TOGGLE_DRAFT_CARDS_TOOLTIP = "Show or hide the draft cards.",
    RECENTLY_UNLOCKED_CATEGORY = "Recently unlocked", SOON_UNLOCKED_CATEGORY = "Unlocks soon",
    CA_TALENTS_UNLOCK_AT_LEVEL = "Talents unlock at level 10.",
    CA_CANNOT_LEARN_S = "Cannot learn: %s", CA_CANNOT_UNLEARN_S = "Cannot unlearn: %s",
    CA_CANNOT_PURGE_TALENTS_S = "Cannot reset talents: %s", CA_CANNOT_RESET_BUILD_S = "Cannot reset build: %s",
    CA_FILTER_KNOWN = "Known", CA_FILTER_UNKNOWN = "Not known", CA_FILTER_KNOWN_IN_BUILD = "In build", CA_FILTER_UNKNOWN_IN_BUILD = "Not in build",
    CA_FILTER_CAN_LEARN = "Can learn", CA_FILTER_CAN_UNLEARN = "Can unlearn", CA_FILTER_CAN_ADD = "Can add", CA_FILTER_CAN_REMOVE = "Can remove",
    CA_FILTER_TYPE_ABILITY = "Abilities", CA_FILTER_TYPE_TALENT = "Talents", CA_FILTER_TYPE_TRAIT = "Traits",
    CA_FILTER_CLASS_GENERAL = "General", CA_FILTER_CLASS_REBORN_GENERAL = "General",
    CA_BROWSER_ADD_SUGGESTION_OVERRIDE = "Suggest this ability", CA_BROWSER_REMOVE_SUGGESTION_OVERRIDE = "Stop suggesting this ability",
    CA_CLEAR_ALL_SUGGESTION_OVERRIDES = "Clear suggestions",
    CA_GATE_TOOLTIP_FORMAT_LOCAL = "Spend |cffFFFFFF%d|r more Talent Essence in this tree to unlock this row",
    CA_GATE_TOOLTIP_FORMAT_GLOBAL = "Spend |cffFFFFFF%d|r more Talent Essence in any tree to unlock the rows below%s",
    CA_GATE_TOOLTIP_FORMAT_CLASS_POINTS = "Requires |cffFFFFFF%d|r more %s Class Points (essence spent on %s abilities and %s talents)",
    CA_GATE_TOOLTIP_FORMAT_ABILITY_ESSENCE = "Requires |cffFFFFFF%d|r more Ability Essence spent",
    CA_GATE_INFO_GLOBAL_AE = "You have spent |cffFFFFFF%s|r Ability Essence",
    CA_GATE_INFO = "You have |cffFFFFFF%s|r %s Talent Essence invested",
    CA_GATE_INFO_CLASS_POINTS = "You have |cffFFFFFF%s|r %s |cffFFFFFFClass Points|r",
    CA_GATE_INFO_CLASS_POINTS_HINT = "Class Points are the Ability and Talent Essence you have spent on that class. Masteries and implicit traits unlock at their Class Point requirement.",
    CA_GATE_CLASS_POINTS_TEXT = "Requires Class Points: %s",
    CA_GATE_ICON_GLOBAL_AE = "Interface\\Icons\\inv_custom_abilityessence", CA_GATE_ICON_GLOBAL_TE = "Interface\\Icons\\inv_custom_talentessence",
    D_ABILITIES_KNOWN = "%d abilities known", D_TALENTS_KNOWN = "%d talents known", S_ABILITIES = "%s abilities", S_TALENTS = "%s talents",
    ADD_CORE = "Add as core ability", ADD_OPTIMAL = "Add as optimal", ADD_EMPOWERING = "Add as empowering", ADD_SYNERGISTIC = "Add as synergistic",
    CA_NO_PENDING_CHANGES = "No pending changes.", CA_MISSING_PREREQUISITE = "%s requires another entry first.",
    CA_CONFIRM_COST = "Cost: %s", CA_CONFIRM_UNLEARN_DEACTIVATE_BUILD = "This deactivates your active build.",
    CONFIRM_RESET_BUILD = "Unlearn everything?\n%s", CONFIRM_RESET_BUILD_NO_COST = "Unlearn everything?",
    CONFIRM_APPLY_PENDING_BUILD_NO_COST = "Apply the pending changes?", CONFIRM_UNLEARN_ALL_S = "Unlearn all %s?%s",
    CONFIRM_LEARN_S = "Learn %s?%s", CONFIRM_UNLEARN_S = "Unlearn %s?%s", CONFIRM_SWAP_S = "Pick the ability to swap %s with.",
    CONFIRM_LEAVING_PENDING = "Discard your pending changes?", UNLEARN = "Unlearn", LEARN = "Learn",
    -- Enum.CALearnResult keys
    CA_LEARN_OK = "OK", CA_LEARN_UNKNOWN = "unknown entry", CA_LEARN_NOT_IN_WORLD = "not in the world", CA_LEARN_NOT_ENABLED = "Character Advancement is disabled",
    CA_LEARN_BAD_ABILITY = "not a learnable entry", CA_LEARN_BAD_GAME_MODE = "not available in this game mode", CA_LEARN_LOW_LEVEL = "your level is too low",
    CA_LEARN_ALREADY_KNOWN = "already known at its highest rank", CA_LEARN_CONDITIONS_FAILED = "not enough Class Points in this class",
    CA_LEARN_NOT_IN_BATTLEGROUNDS = "not in a battleground", CA_LEARN_WRONG_EXPANSION = "wrong expansion", CA_LEARN_DISABLED = "disabled",
    CA_LEARN_WRONG_CLASS = "not available to your class", CA_LEARN_ALREADY_KNOW_A_STARTING_NODE = "you already know a starting node",
    CA_LEARN_MISSING_CONNECTED_ENTRIES = "learn the connected talent first", CA_LEARN_NOT_ENOUGH_INVESTED_AE = "not enough Ability Essence invested",
    CA_LEARN_NOT_ENOUGH_INVESTED_TE = "not enough Talent Essence invested", CA_LEARN_MISSING_REQUIRED_ID = "requires another entry first",
    CA_LEARN_WRONG_REALM = "wrong realm", CA_LEARN_NOT_IN_COMBAT = "not while in combat", CA_LEARN_DEPRECATED = "deprecated entry",
    CA_LEARN_BUILD_DRAFT = "not during a build draft", CA_LEARN_INVULNERABILE = "not while invulnerable", CA_LEARN_NO_TALENTS_CHALLENGE = "talents are disabled by your challenge",
    CA_LEARN_TOO_MANY_UNCOMMON_ABILITIES = "too many uncommon abilities", CA_LEARN_TOO_MANY_RARE_ABILITIES = "too many rare abilities",
    CA_LEARN_TOO_MANY_EPIC_ABILITIES = "too many epic abilities", CA_LEARN_TOO_MANY_LEGENDARY_ABILITIES = "too many legendary abilities",
    CA_LEARN_MISSING_AE = "not enough Ability Essence", CA_LEARN_MISSING_TE = "not enough Talent Essence",
    CA_LEARN_WILDCARD_TAME_SPELLS = "WildCard: tame spells", CA_LEARN_WILDCARD_MASTERIES = "WildCard: masteries", CA_LEARN_WILDCARD_DISABLED = "WildCard: disabled",
    CA_LEARN_WILDCARD_LOW_LEVEL = "WildCard: level too low", CA_LEARN_WILDCARD_STARTER_REPEAT_PROTECTION = "WildCard: starter protection",
    CA_LEARN_NOT_WHILE_DEAD = "not while dead", CA_LEARN_NOT_WILDCARD = "not a WildCard realm", CA_LEARN_NOT_DRAFT = "not a Draft realm",
    CA_LEARN_GROUP = "only one entry of this group can be known", CA_LEARN_DISPLAY_ENTRY = "granted automatically",
    -- Enum.CAUnlearnResult keys
    CA_UNLEARN_OK = "OK", CA_UNLEARN_UNKNOWN = "unknown entry", CA_UNLEARN_NOT_IN_WORLD = "not in the world", CA_UNLEARN_NOT_ENABLED = "Character Advancement is disabled",
    CA_UNLEARN_BAD_ABILITY = "not an unlearnable entry", CA_UNLEARN_NOT_IN_BATTLEGROUNDS = "not in a battleground", CA_UNLEARN_BAD_GAME_MODE = "not available in this game mode",
    CA_UNLEARN_NOT_THIS_ABILITY = "this ability cannot be unlearned", CA_UNLEARN_NOT_IN_COMBAT = "not while in combat", CA_UNLEARN_NOT_KNOWN = "not known",
    CA_UNLEARN_NO_UNLEARN_ITEM = "no unlearn currency", CA_UNLEARN_MISSING_CONNECTED_ENTRIES = "a connected talent depends on it",
    CA_UNLEARN_MASTERY_ID = "granted by a mastery or trait; unlearn that instead", CA_UNLEARN_MISSING_REQUIRED_ID = "another entry requires it",
    CA_UNLEARN_NOT_ENOUGH_INVESTED_AE = "another entry needs the Ability Essence invested here", CA_UNLEARN_NOT_ENOUGH_INVESTED_TE = "another entry needs the Talent Essence invested here",
    CA_UNLEARN_NO_SCROLL_OF_FORTUNE = "no Scroll of Fortune", CA_UNLEARN_LOCKED = "locked", CA_UNLEARN_NOT_WILDCARD = "not a WildCard realm", CA_UNLEARN_NOT_DRAFT = "not a Draft realm",
    CA_UNLEARN_WILDCARD_UNSPENT_AE = "WildCard: spend your Ability Essence first", CA_UNLEARN_WILDCARD_UNSPENT_TE = "WildCard: spend your Talent Essence first",
    CA_UNLEARN_SCROLL_OF_FORTUNE_LIMIT = "Scroll of Fortune limit reached",
    -- Mystic Enchant panel
    ENCHANT_COLLECTION_COLLECTION = "Collection", ENCHANT_COLLECTION_SCROLLS = "Scrolls", ENCHANT_COLLECTION_REFORGE = "Reforge",
    ENCHANT_COLLECTION_EMPTY_SEARCH = "No enchants match your search.", ENCHANT_COLLECTION_GET_MORE_SCROLLS = "Get more scrolls",
    ENCHANT_COLLECTION_OBTAIN_SCROLLS = "Mystic Scrolls come from a Mystic Altar. None exists on this realm.",
    ENCHANT_COLLECTION_NEED_MORE_EXTRACTS = "You need more Mystic Extracts.", ENCHANT_COLLECTION_NEED_MORE_LEVELS = "Your altar level is too low.",
    ENCHANT_COLLECTION_REMOVE_SLOT = "Remove", ENCHANT_COLLECTION_SAVE_TO_COLLECTION = "Save to collection",
    ENCHANT_COLLECTION_SCROLL_COLLECTION_REFORGE = "Reforge from collection", ENCHANT_COLLECTION_SLOT_ACTIVATION = "Slot activation",
    ENCHANT_COLLECTION_TOOLTIP_EXTRACT = "Mystic Extract", ENCHANT_COLLECTION_UNDO_TOOLTIP = "Undo", ENCHANT_COLLECTION_UNDO_TOOLTIP_TEXT = "Discard the staged change.",
    ENCHANT_COLLECTION_WORLDFORGED = "Worldforged", ENCHANT_SPECIALIZATIONS = "Enchant Specializations",
    ENCHANT_SPECIALIZATION_CHANGE_HINT = "Click to activate this preset.", ENCHANT_SPECIALIZATION_CURRENT_HINT = "Active preset.",
    ENCHANT_SPECIALIZATION_PREVIEW = "Preview", ENCHANT_SPECIALIZATION_UNLOCK_HINT = "Unlock another preset.", ENCHANT_SPECIALIZATION_UNLOCK_WITH = "Unlock with %s",
    MYSTIC_ENCHANTING_ALTAR = "Mystic Altar", MYSTIC_ENCHANT_EXP_BAR_DESC = "Altar experience", NEW_MYSTIC_ENCHANT_LEARNED = "New Mystic Enchant learned: %s",
    REFORGE_SPELL = "Reforge", RE_PURCHASE_NO_MYSTIC_ALTAR = "You must be near a Mystic Altar (none exists on this realm).",
    UNLOCK_MYSTIC_ENCHANT_PRESET_HINT = "Unlock a preset", BUG_REPORT_CATEGORY_MYSTICENCHANTS = "Mystic Enchants", BUG_REPORT_LABEL_MYSTICENCHANT = "Mystic Enchant",
    BUILD_CREATOR_CLICK_TO_ADD = "Click to add", BUILD_CREATOR_CLICK_TO_REMOVE = "Right-click to remove", PTR_CLIENT = "",
    -- Hero Architect (Ascension_BuildCreator): the browsing side is live over the recovered catalogue;
    -- publishing, rating and editing reach Ascension's service and stay inert here
    PUBLISH_BUILD = "Publish Build", EDIT_BUILD = "Edit Build", DELETE_BUILD = "Delete Build", CREATE_BUILD = "Create Build",
    ACTIVATE_BUILD = "Activate Build", ACTIVE_BUILD = "Active Build", NO_ACTIVE_BUILD = "No active build", BROWSE_BUILDS = "Browse Builds",
    CHOOSE_THIS_BUILD = "Choose this build", CHANGE_BUILD_CATEGORY = "Change category", COPY_TO_CLIPBOARD = "Copy to clipboard",
    S_COPIED_TO_CLIPBOARD = "%s copied to clipboard.", VIEW_BUILD_ERROR = "This build could not be shown.", YOU_KNOW_THIS_SPELL = "You know this spell.",
    VIEW_PVE_BUILD = "View PvE Build", VIEW_PVP_BUILD = "View PvP Build", VIEW_LEVELING_BUILD = "View Leveling Build",
    BUILD_AUTHOR_S = "by %s", BUILD_AUTHOR_S_S_AGO = "by %s, %s ago", BUILD_SORT_DATE = "Newest", BUILD_SORT_RATING = "Highest rated",
    BUILD_SPELL_COMMENT = "Comment", BUILD_SPELL_FLAGS = "Flags", BUILD_UNPUBLISHED_NEEDS_FIXES = "This build needs fixes before it can be published.",
    CANNOT_PUBLISH_BUILD = "Builds cannot be published on this realm: Ascension's build service is gone.", PUBLISHING_BUILD = "Publishing...",
    PUBLISH_BUILD_FAILED = "Publishing failed.", DELETE_BUILD_FAILED = "The build could not be deleted.", ACTIVATE_BUILD_FAILED = "The build could not be activated.",
    DEACTIVATE_BUILD_FAILED = "The build could not be deactivated.", RAPID_ROLL_THIS_BUILD = "Rapid roll this build", TIP_RESTORE_WIP_BUILD = "Restore your unfinished build?",
    TOGGLE_BUILDDRAFT_DISABLED = "Build Draft is not available on this realm.", COMPLEXITY_LABEL = "Complexity: %s", PRIMARY_STAT_STRING = "Primary Stat: %s",
    BUILD_EDITOR_CATEGORY_LEVELING = "Leveling", BUILD_EDITOR_CATEGORY_LEVEL60PVE = "Level 60 PvE", BUILD_EDITOR_CATEGORY_LEVEL60PVP = "Level 60 PvP",
    BUILD_EDITOR_CATEGORY_LEVEL70PVE = "Level 70 PvE", BUILD_EDITOR_CATEGORY_LEVEL70PVP = "Level 70 PvP", BUILD_EDITOR_CATEGORY_BUILDDRAFT = "Build Draft",
    BUILD_EDITOR_CATEGORY_BUILDDRAFTENDGAMEPVE = "Build Draft PvE", BUILD_EDITOR_CATEGORY_BUILDDRAFTENDGAMEPVP = "Build Draft PvP",
    BUILD_DRAFT_COMPLETED_BUILD = "Completed", BUILD_DRAFT_COMPLETED_BUILD_TOOLTIP = "This build was completed in a Build Draft.",
    BUILD_DRAFT_TROPHY_ELIGIBLE = "Trophy eligible", BUILD_DRAFT_TROPHY_ELIGIBLE_TOOLTIP = "Eligible for a Build Draft trophy.",
    BUILD_CREATOR_OVERVIEW_HINT = "Pick a category on the left to browse the recovered community builds.",
    BUILD_CREATOR_PROS_AND_CONS_HINT = "Strengths and weaknesses, as the author wrote them.", BUILD_CREATOR_FEATURED_HINT = "Featured builds",
    BUILD_CREATOR_LINK_SPELLS_HINT = "Shift-click a spell to link it.", BUILD_CREATOR_LINK_ITEMS_HINT = "Shift-click an item to link it.",
    BUILD_CREATOR_WEAKAURAS_HINT = "WeakAuras strings the author shared.", BUILD_CREATOR_SET_COMMENT = "Set comment", BUILD_CREATOR_SET_ENCHANT_LEVEL = "Set enchant level",
    BUILD_CREATOR_FINISH_PICKING_ENCHANTS = "Finish picking enchants", BUILD_CREATOR_IMPORT_BUILD = "Import this build into your pending changes?",
    BUILD_CREATOR_EDIT_BUILD = "Edit build", BUILD_CREATOR_PUBLISH_BUILD = "Publish this build?", BUILD_CREATOR_RESET_BUILD = "Reset this build?",
    BUILD_CREATOR_DELETE_BUILD = "Delete this build?", BUILD_CREATOR_DELETE_SAVED_BUILD = "Delete this saved build?", BUILD_CREATOR_CHANGE_BUILD_CATEGORY = "Move this build to another category?",
    BUILD_DIFFICULTY_RATING_EASY = "Easy", BUILD_DIFFICULTY_RATING_NORMAL = "Normal", BUILD_DIFFICULTY_RATING_HARD = "Hard",
    BUILD_DIFFICULTY_RATING_VERY_HARD = "Very hard", BUILD_DIFFICULTY_RATING_IMPOSSIBLE = "Impossible",
    BUILDCREATOR_CATEGORY_HEADER_GENERAL = "General", BUILDCREATOR_CATEGORY_HEADER_PVE = "Player vs Environment", BUILDCREATOR_CATEGORY_HEADER_PVE_SHORT = "PvE",
    BUILDCREATOR_CATEGORY_HEADER_PVP = "Player vs Player", BUILDCREATOR_CATEGORY_HEADER_PVP_SHORT = "PvP",
    BUILDCREATOR_CATEGORY_HEADER_CREATE = "Create a build", BUILDCREATOR_CATEGORY_HEADER_CREATE_SHORT = "Create",
    BUILDCREATOR_CATEGORY_ACTIVEBUILD = "Active build", BUILDCREATOR_CATEGORY_FEATURED = "Featured", BUILDCREATOR_CATEGORY_NONE = "Uncategorised",
    BUILDCREATOR_CATEGORY_MYBUILDS = "My builds", BUILDCREATOR_CATEGORY_LEVELING = "Leveling", BUILDCREATOR_CATEGORY_LEVEL60PVE = "Level 60 PvE",
    BUILDCREATOR_CATEGORY_LEVEL60PVP = "Level 60 PvP", BUILDCREATOR_CATEGORY_LEVEL60PVPVE = "Level 60 PvPvE", BUILDCREATOR_CATEGORY_LEVEL70PVE = "Level 70 PvE",
    BUILDCREATOR_CATEGORY_LEVEL70PVP = "Level 70 PvP", BUILDCREATOR_CATEGORY_LEVEL70PVPVE = "Level 70 PvPvE", BUILDCREATOR_CATEGORY_BUILDDRAFT = "Build Draft",
    BUILDCREATOR_CATEGORY_ARCHIVED = "Archived", BUILDCREATOR_CATEGORY_BUILDDRAFTENDGAMEPVE = "Build Draft end-game PvE",
    BUILDCREATOR_CATEGORY_BUILDDRAFTENDGAMEPVP = "Build Draft end-game PvP", BUILDCREATOR_CATEGORY_HISTORY = "History",
    ADD_ENCHANT = "Add enchant", ADD_EQUIPMENT = "Add equipment", ADD_SPELL = "Add spell", BUILD_ORDER = "Sort by", ENABLE_AUTO_LEARN = "Enable auto-learn", CHANGE = "Change",
    ASC_PREVIEW_READ_ONLY = "Publishing is disabled on this preservation realm; builds are read-only.",
    ENCHANT_COLLECTION_APPLY_COLLECTION_REFORGE = "Reforge this enchant in your collection?",
    ENCHANT_COLLECTION_APPLY_COLLECTION_REFORGE_WARN = "Reforging replaces the enchant's current effect. Continue?",
    ENCHANT_COLLECTION_DESTROY_SLOT = "Destroy %s in this slot?", ENCHANT_COLLECTION_DISENCHANT_ITEM = "Disenchant %s to extract its enchant?",
    ENCHANT_COLLECTION_DISENCHANT_SLOT = "Disenchant this slot?", ENCHANT_COLLECTION_NO_ALTAR = "You are not near a Mystic Altar.",
    ENCHANT_COLLECTION_NO_ALTAR_FULL = "You must be near a Mystic Altar to change your enchants.",
    ENCHANT_COLLECTION_SCROLL_COLLECTION_REFORGE_DIALOGUE = "Reforge %s into your collection using %s?",
    ENCHANT_COLLECTION_SCROLL_COLLECTION_REFORGE_TIP = "Drag a Mystic Scroll here to add its enchant to your collection.",
    ENCHANT_ERROR_SCROLL_POS = "That enchant cannot go in this slot.", ENCHANT_SPECIALIZATION = "Enchant set", THIS_WOULD_REQUIRE = "\nThis would require:",
    ERR_LEARN_TALENT_S = "You have learned %s.", ERR_UPGRADE_TALENT_S = "You have upgraded %s.",
    CA_LEARN_MULTIPLE_PET_SPELLS = "You cannot learn more than one pet ability of this kind.",
    BUILDCREATOR_SECTION_OVERVIEW = "Overview", BUILDCREATOR_SECTION_PROS_AND_CONS = "Pros and cons", BUILDCREATOR_SECTION_ITEMIZATION = "Itemization",
    BUILDCREATOR_SECTION_ROTATION = "Rotation", BUILDCREATOR_SECTION_CONSUMABLES = "Consumables", BUILDCREATOR_SECTION_WEAKAURAS = "WeakAuras",
    BUILDCREATOR_SECTION_NOTES = "Notes", BUILDCREATOR_SECTION_SPELLS_AND_TALENTS = "Spells and talents", BUILDCREATOR_SECTION_TALENTS = "Talents",
    BUILDCREATOR_SECTION_EQUIPMENT = "Equipment", BUILDCREATOR_SECTION_MYSTIC_ENCHANTS = "Mystic Enchants",
    BUILDCREATOR_CREATEBUTTON_SAVEDBUILD = "Saved build", BUILDCREATOR_SECTION_MACROS = "Macros", CORE = "Core", OPTIMAL = "Optimal", EMPOWERING = "Empowering", SYNERGISTIC = "Synergistic",
    SPELL_SET_CORE_ABILITY = "Core ability", SPELL_SET_CORE_ABILITY_TOOLTIP = "The build revolves around this ability.",
    SPELL_SET_OPTIMAL_ABILITY = "Optimal", SPELL_SET_OPTIMAL_ABILITY_TOOLTIP = "Take this for the strongest version of the build.",
    SPELL_SET_EMPOWERING_ABILITY = "Empowering", SPELL_SET_EMPOWERING_ABILITY_TOOLTIP = "Makes the core abilities stronger.",
    SPELL_SET_SYNERGISTIC_ABILITY = "Synergistic", SPELL_SET_SYNERGISTIC_ABILITY_TOOLTIP = "Works well with the rest of the build.",
    SPELL_SET_COMMENT = "Comment", SPELL_SET_SHOW_DRAFT = "Show in draft", SPELL_SET_SHOW_DRAFT_TOOLTIP = "Show this spell during a Build Draft.",
    MYSTIC_ENCHANT_COLLECTED = "Collected", ITEM_CLASS_2 = "Weapon", ITEM_CLASS_4 = "Armor",
    RE_QUALITY_POOR_NAME = "Poor", RE_QUALITY_NORMAL_NAME = "Common", RE_QUALITY_UNCOMMON_NAME = "Uncommon", RE_QUALITY_RARE_NAME = "Rare",
    RE_QUALITY_EPIC_NAME = "Epic", RE_QUALITY_LEGENDARY_NAME = "Legendary", RE_QUALITY_ARTIFACT_NAME = "Artifact", RE_QUALITY_HEIRLOOM_NAME = "Heirloom",
}
for k, v in pairs(more) do if _G[k] == nil then _G[k] = v end end
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

-- Core preview bootstrap. The original Collections/TalentUI integration is a later pack layer.
local function message(text)
    if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage("|cff66ccffAscension preview:|r " .. text)
    elseif print then print("Ascension preview: " .. text) end
end
local config=assert(ASC.Config,"missing generated preview configuration")
ASC.Preview.Begin(config.dataset,config.classByte,config.level)
if config.buildCatalogue then ASC.Builds.Select(config.buildCatalogue) end
ASC.EventInstallationErrors=ASC.Events.Install()
local driver=CreateFrame("Frame")
driver:SetScript("OnUpdate",function() ASC.Preview.FlushNotifications(); ASC.Builds.FlushNotifications() end)
ASC.Preview.Driver=driver
local function status()
    local c=ASC.Preview.Context
    message(c.identity.Name .. ", level " .. c.level .. "; dataset " .. c.dataset .. ". Read-only; server learning is disabled.")
end
SLASH_ASCENSIONPREVIEW1="/asc"
SlashCmdList.ASCENSIONPREVIEW=function(command)
    local verb,arg=string.match(command or "","^%s*(%S*)%s*(.-)%s*$")
    if verb=="selftest" then
        local r=ASC.Data.SelfTest()
        message(r.entries .. " entries, " .. r.buckets .. " class/tab buckets; " .. r.unresolvedRelationships .. " unresolved relationships; " .. r.incompleteRuleEntries .. " entries with incomplete rules.")
        message(#ASC.EventInstallationErrors .. " frame-type event hooks unavailable. Full panel/gameplay verification is pending.")
    elseif verb=="builds" then
        local catalogue=ASC.Builds.Current()
        if not catalogue then message("No build catalogue selected in this pack."); return end
        C_BuildCreator.QueryAllBuilds(arg~="" and arg or "None")
        message(C_BuildCreator.GetNumBuilds() .. " recovered builds; " .. catalogue.metadata.realm .. ", captured " .. catalogue.metadata.capturedDate .. ". Activation is disabled.")
    elseif verb=="spec" then
        local id=tonumber(arg)
        if id and C_CharacterAdvancement.CanSwitchActiveChrSpec(id) then
            C_CharacterAdvancement.SwitchActiveChrSpec(id)
            message("Selected preview specialization " .. C_ClassInfo.GetSpecInfoByID(id).Name .. ".")
        else
            message("Use /asc spec <ID> with a specialization of the selected preview class.")
        end
    else
        status()
        message("Commands: /asc status, /asc selftest, /asc spec <ID>, /asc builds [category]. This pack currently provides the data/API core.")
    end
end

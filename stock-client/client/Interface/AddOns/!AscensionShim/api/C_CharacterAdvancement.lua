-- P2 preview contracts only. This layer never learns spells or claims persisted state.
-- UI references: CoATalentFrame, CoATreeViewMixin, CATalentBaseMixin and TalentTreeBase.
local CA=ASC.Namespace("C_CharacterAdvancement")
ASC.Preview=ASC.Preview or {}
local Preview=ASC.Preview
local reason="ASC_PREVIEW_READ_ONLY"
_G[reason]="Read-only preview: server learning is not enabled."
local context
local filtered={}
local function current() return assert(context,"Begin the recovered-data preview first") end
function Preview.Begin(dataset,classByte,level)
    ASC.Data.SelectDataset(dataset)
    local d=ASC.Data.Datasets[dataset]
    local identity
    for _, row in ipairs(d.tables.classIdentities or {}) do if row.ID==classByte then identity=row end end
    assert(identity and identity.CAClassName,"class identity has no verified CA name join")
    local ae,te=ASC.Data.GetBudget(classByte,level)
    assert(ae and te,"preview essence is unavailable for this class/level")
    context={dataset=dataset,identity=identity,level=level,ae=ae,te=te,readOnly=true}
    Preview.Context=context
    filtered={}
    return context
end
function Preview.SelectSpec(id)
    local c=current()
    local info=C_ClassInfo.GetSpecInfoByID(id)
    assert(info and info.Class==c.identity.Token,"specialization belongs to another class")
    c.specID=id
    ASC.Events.Fire("ASCENSION_CA_SPECIALIZATION_ACTIVE_ID_CHANGED",id)
    ASC.Events.Fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
end
function CA.GetAllEntries() current(); return ASC.Data.GetEntries() end
function CA.GetEntryByInternalID(id) current(); return ASC.Data.GetEntry(id) end
function CA.GetEntriesByClass(class,tab,includeHidden)
    current()
    -- P2 intentionally shows the recovered source table. Native hidden-state rules
    -- require server/context reconstruction; this is not a claim to reproduce them.
    return ASC.Data.GetEntries(class,tab)
end
function CA.GetEntryBySpellID(spell)
    local c=current()
    local candidates=ASC.Data.GetEntriesForSpell(spell)
    for _, entry in ipairs(candidates) do if entry.Class==c.identity.CAClassName then return entry end end
    if #candidates==1 then return candidates[1] end
    return nil -- ambiguous cross-class identity must not be guessed
end
function CA.GetActiveChrSpec() return current().specID end
function CA.GetActiveSpecID() current(); return 0 end -- isolated preview slot; no Hero Architect build
function CA.IsPending() current(); return false end
function CA.GetPendingRemainingAE() return current().ae end
function CA.GetPendingRemainingTE() return current().te end
function CA.GetRemainingAE() return current().ae end
function CA.GetRemainingTE() return current().te end
function CA.GetPendingTabAEInvestment() current(); return 0 end -- empty preview build
function CA.GetPendingTabTEInvestment() current(); return 0 end
function CA.GetPendingRankByEntryID(id)
    local entry=CA.GetEntryByInternalID(id)
    return 0,entry and #entry.Spells or 0
end
function CA.IsKnownID() current(); return false end
function CA.IsConnectionAllowed() current(); return false end -- empty preview build
function CA.MeetsInvestmentForAddByEntryID(id)
    local entry=CA.GetEntryByInternalID(id)
    if not entry then return false end
    for key,value in pairs(entry) do
        if key:match("^Required.*Investment$") and type(value)=="number" and value>0 then return false end
    end
    return true
end
function CA.SetFilteredEntries(text,filters)
    current();filtered={}
    text=type(text)=="string" and string.lower(text) or ""
    if filters and next(filters) then return false,"unsupported-preview-filter" end
    if text=="" then return true end
    for _, entry in ipairs(ASC.Data.GetEntries()) do
        if string.find(string.lower(entry.Name or ""),text,1,true) then filtered[entry.ID]=true end
    end
    return true
end
function CA.IsFiltered(id) current(); return filtered[id]==true end
function CA.CanAddByEntryID() current(); return false,reason end
function CA.CanRemoveByEntryID() current(); return false,reason end
function CA.CanSwitchActiveChrSpec(id)
    local info=C_ClassInfo.GetSpecInfoByID(id)
    return info ~= nil and info.Class==current().identity.Token
end
function CA.CanApplyPendingBuild() current(); return false,reason,nil,nil,nil,nil,nil,0,0 end
local function deny() current(); return false,reason end
CA.AddByEntryID=deny
CA.RemoveByEntryID=deny
CA.ClearPendingBuild=deny
CA.ClearPendingBuildByTab=deny
function CA.SwitchActiveChrSpec(id)
    if not CA.CanSwitchActiveChrSpec(id) then return false,"invalid-preview-spec" end
    Preview.SelectSpec(id) -- view selection only, no character/server mutation
    return true
end
CA.ImportPendingBuildID=deny
CA.ImportPendingBuild=deny
CA.PickupSpell=deny
function CA.ExportBuild() current(); return nil end
function CA.CancelPendingBuild()
    current()
    -- Defer the event until after native hooksecurefunc post-hooks have run.
    Preview.cancelNotification=true
end
function Preview.FlushNotifications()
    if Preview.cancelNotification then
        Preview.cancelNotification=nil
        ASC.Events.Fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
    end
end
function CA.ApplyPendingBuild()
    current()
    ASC.Events.Fire("CHARACTER_ADVANCEMENT_UPDATE_ENTRIES_RESULT",false,reason)
    return false,reason
end

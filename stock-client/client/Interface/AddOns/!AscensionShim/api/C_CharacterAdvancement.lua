-- api/C_CharacterAdvancement.lua: the read-only preview context (dataset + class identity +
-- level) that the rest of the shim keys on. Every C_CharacterAdvancement function the panels
-- call is defined in core/Live.lua over this context and the module's STATE; before the first
-- STATE arrives the Live API answers from an empty known set and refuses mutations.
local CA = ASC.Namespace("C_CharacterAdvancement")
ASC.Preview = ASC.Preview or {}
local Preview = ASC.Preview
_G.ASC_PREVIEW_READ_ONLY = _G.ASC_PREVIEW_READ_ONLY or "Read-only preview: server learning is not enabled."
_G.ASC_OFFLINE = _G.ASC_OFFLINE or "The Character Advancement server has not answered yet."
local context

function Preview.Begin(dataset, classByte, level)
    ASC.Data.SelectDataset(dataset)
    local d = ASC.Data.Datasets[dataset]
    local identity
    for _, row in ipairs(d.tables.classIdentities or {}) do if row.ID == classByte then identity = row end end
    assert(identity and identity.CAClassName, "class identity has no verified CA name join")
    local ae, te = ASC.Data.GetBudget(classByte, level)
    assert(ae and te, "preview essence is unavailable for this class/level")
    context = { dataset = dataset, identity = identity, level = level, ae = ae, te = te, readOnly = true }
    Preview.Context = context
    return context
end
function Preview.SelectSpec(id)
    local c = assert(context, "Begin the recovered-data preview first")
    local info = C_ClassInfo.GetSpecInfoByID(id)
    assert(info and info.Class == c.identity.Token, "specialization belongs to another class")
    c.specID = id
    ASC.Events.Fire("ASCENSION_CA_SPECIALIZATION_ACTIVE_ID_CHANGED", id)
    ASC.Events.Fire("CHARACTER_ADVANCEMENT_PENDING_BUILD_UPDATED")
end
function Preview.FlushNotifications() end
-- kept for Bootstrap's /asc commands; core/Live.lua defines the full surface
function CA.GetAllEntries() return ASC.Data.GetEntries() end
function CA.GetEntryByInternalID(id) return ASC.Data.GetEntry(id) end

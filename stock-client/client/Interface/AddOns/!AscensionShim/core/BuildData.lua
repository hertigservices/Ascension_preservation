-- Read-only recovered build catalogues. Captured spells are not CA entry IDs.
ASC.Builds = ASC.Builds or {}
local Builds=ASC.Builds
Builds.Catalogues=Builds.Catalogues or {}
local loading
function Builds.Begin(metadata)
    assert(not loading and metadata.format==1 and type(metadata.key)=="string","invalid build catalogue begin")
    assert(not Builds.Catalogues[metadata.key],"duplicate build catalogue")
    loading={metadata=metadata,records={},ordered={},detailed={}}
end
function Builds.Add(value)
    assert(loading and type(value.record)=="table","no build catalogue loading")
    local record=value.record
    assert(type(record.ID)=="string" and not loading.records[record.ID],"invalid or duplicate build ID")
    assert(type(record.Spells)=="table" and type(record.Name)=="string","invalid build record")
    loading.records[record.ID]=record
    loading.ordered[#loading.ordered+1]=record.ID
    loading.detailed[record.ID]=value.detailed
end
function Builds.Finalize()
    assert(loading and #loading.ordered==loading.metadata.buildCount,"build count differs from manifest")
    table.sort(loading.ordered)
    loading.complete=true
    Builds.Catalogues[loading.metadata.key]=loading
    loading=nil
end
function Builds.Select(key)
    assert(Builds.Catalogues[key] and Builds.Catalogues[key].complete,"unknown build catalogue")
    Builds.ActiveKey=key
end
function Builds.Current()
    return Builds.Catalogues[Builds.ActiveKey]
end
function Builds.Copy(value)
    if type(value)~="table" then return value end
    local result={}
    for k,v in pairs(value) do result[k]=Builds.Copy(v) end
    return result
end

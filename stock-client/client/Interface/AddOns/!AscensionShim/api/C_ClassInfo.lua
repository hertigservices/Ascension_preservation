-- GetSpecInfo contracts verified against the original DLL getter at 0x100DAC60.
-- Preserve file tokens (FIREARMS) separately from localized names (Demolition).
local API=ASC.Namespace("C_ClassInfo")
local function specs()
    local d=ASC.Data.Datasets[ASC.Data.ActiveKey]
    assert(d and d.complete and d.tables.specs, "specialization dataset is not selected")
    return d.tables.specs
end
function API.GetAllSpecs(classToken)
    local result={}
    classToken=type(classToken)=="string" and string.upper(classToken) or classToken
    for _, info in ipairs(specs()) do
        if info.Class == classToken then result[#result+1]=info.Spec end
    end
    return result
end
function API.GetSpecInfo(classToken,specToken)
    classToken=type(classToken)=="string" and string.upper(classToken) or classToken
    specToken=type(specToken)=="string" and string.upper(specToken) or specToken
    for _, info in ipairs(specs()) do
        if info.Class==classToken and info.Spec==specToken then return info end
    end
end
function API.GetSpecInfoByID(id)
    for _, info in ipairs(specs()) do if info.ID==id then return info end end
end

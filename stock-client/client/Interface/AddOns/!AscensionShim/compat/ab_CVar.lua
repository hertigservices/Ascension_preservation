-- ab_CVar.lua: C_CVar for a stock client (Ascension's is native in Extensions.dll plus
-- SharedXML\Util\C_CVar.lua on top of it, which we do not copy).
--
-- Stock 3.3.5 cannot register new CVars (SetCVar refuses unknown names), so Ascension's
-- RegisterSavedCVar keeps its values in the shim's saved variables instead
-- (AscensionShimDB.cvars, per character) and every getter/setter falls through to the real
-- CVar when the name is not ours. Minimums/Maximums are the values Ascension's file carries.
C_CVar = C_CVar or {}
C_CVar.Minimums = C_CVar.Minimums or { nameplateDistance = 5, nameplateVerticalOffset = -0.25, nameplateAngle = 0, lootToastMaximum = 1, tabTargetRange = 5 }
C_CVar.Maximums = C_CVar.Maximums or { nameplateDistance = 60, nameplateVerticalOffset = 1.25, nameplateAngle = 3.1415, lootToastMaximum = 6, tabTargetRange = 100 }

local registry = {} -- name -> { default, min, max, character }
local function store()
    AscensionShimDB = AscensionShimDB or {}
    AscensionShimDB.cvars = AscensionShimDB.cvars or {}
    return AscensionShimDB.cvars
end
local function native(name)
    local ok, value = pcall(GetCVar, name)
    if ok then return value end
end
local function raw(name)
    if registry[name] then
        local v = store()[name]
        if v == nil then v = registry[name].default end
        return v
    end
    return native(name)
end

function C_CVar.RegisterSavedCVar(cvar, defaultValue, minValue, maxValue)
    registry[cvar] = { default = defaultValue, min = minValue, max = maxValue }
end
C_CVar.RegisterSavedCharacterCVar = C_CVar.RegisterSavedCVar
function C_CVar.IsRegistered(cvar) return registry[cvar] ~= nil end
function C_CVar.Get(cvar) local v = raw(cvar) if v == nil then return nil end return tostring(v) end
function C_CVar.GetNumber(cvar) return tonumber(raw(cvar)) or 0 end
function C_CVar.GetBool(cvar)
    local v = raw(cvar)
    return v == true or v == 1 or v == "1" or v == "true"
end
function C_CVar.GetBitfield(cvar, index)
    local n = C_CVar.GetNumber(cvar)
    return math.floor(n / 2 ^ (index - 1)) % 2 == 1
end
function C_CVar.Set(cvar, value)
    if registry[cvar] then
        store()[cvar] = value
        return true
    end
    local ok = pcall(SetCVar, cvar, value)
    return ok
end
function C_CVar.SetBitfield(cvar, index, on)
    local n = C_CVar.GetNumber(cvar)
    local bit = 2 ^ (index - 1)
    local has = math.floor(n / bit) % 2 == 1
    if on and not has then n = n + bit elseif has and not on then n = n - bit end
    return C_CVar.Set(cvar, n)
end
function C_CVar.GetDefault(cvar)
    if registry[cvar] then return registry[cvar].default end
    local ok, v = pcall(GetCVarDefault, cvar)
    if ok then return v end
end
function C_CVar.GetDefaultNumber(cvar) return tonumber(C_CVar.GetDefault(cvar)) or 0 end
function C_CVar.GetDefaultBool(cvar) local v = C_CVar.GetDefault(cvar) return v == true or v == 1 or v == "1" or v == "true" end
function C_CVar.ResetToDefault(cvar) return C_CVar.Set(cvar, C_CVar.GetDefault(cvar)) end
function C_CVar.GetMin(cvar) return registry[cvar] and registry[cvar].min or C_CVar.Minimums[cvar] end
function C_CVar.GetMax(cvar) return registry[cvar] and registry[cvar].max or C_CVar.Maximums[cvar] end
function C_CVar.SetCVarSave(cvar, value) return C_CVar.Set(cvar, value) end

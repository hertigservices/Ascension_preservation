-- zz_TooltipMacros.lua: render Ascension's tooltip macros on a stock client.
--
-- Ascension's Spell.dbc descriptions (and some item texts) carry markup that Extensions.dll
-- expanded before the tooltip was drawn; the stock client prints it raw. Grammar, measured from
-- server-coa2\Data\dbc\Spell.dbc against the live tooltips in TooltipDump.lua (2026-09-10):
--
--   @ext:TEXT:ext@        extended information. Live: hidden behind one
--                         "|cff00DDFFHold SHIFT for more information|r" line, shown while
--                         Shift is held. An optional "header~body" split shows the header always.
--   @s:ID:0@              spell ID as "|Ticon:20:20:0:0|t |cffFFFFFFName|r" (Poison Mastery lists
--                         its poisons this way); @s:ID:-1@ / other offsets = that spell's description.
--   @req:ID@              "|cffFF1A1ARequires |Ticon|t Name|r"  (300036 = Primary Stat: Agility)
--   @unlockby:ID@         "|cffF83600Unlocked by |Ticon|t Name (Rank n)|r"
--   @ifknown:ID:TEXT:ifknown@ / @ifnotknown:ID:TEXT:ifnotknown@   TEXT when the spell is (not) known
--   @re:ID:N@             a Mystic Enchant reference: the enchant's name from the recovered catalogue
--   @wflocation:TEXT@, @learns:TEXT@, @a:TEXT@   kept as their text
--
-- Applied to GameTooltip, ItemRefTooltip and the two comparison tooltips after the client has
-- filled them; a tooltip line is rewritten only when it contains a macro.
local HINT = "|cff00DDFFHold SHIFT for more information|r"
local MAX_DEPTH = 3

local function spellIconName(id)
    local name, _, icon = GetSpellInfo(id)
    if not name then return nil end
    return (icon and ("|T" .. icon .. ":20:20:0:0|t ") or "") .. "|cffFFFFFF" .. name .. "|r", name, icon
end

local expand

local function expandSpell(id, offset, depth)
    id = tonumber(id)
    if not id then return "" end
    if tonumber(offset) == 0 then
        return spellIconName(id) or ""
    end
    local text = GetSpellDescription and GetSpellDescription(id) or ""
    return expand(text or "", depth + 1)
end

local function knownSpell(id)
    id = tonumber(id)
    return id and IsSpellKnown and IsSpellKnown(id) or false
end

local function enchantName(id)
    id = tonumber(id)
    local rec = ASC and ASC.Enchants and ASC.Enchants.Get and ASC.Enchants.Get(id)
    if rec then return "|cffFFFFFF" .. (rec.SpellName or ("enchant " .. id)) .. "|r" end
    local name = id and GetSpellInfo(id)
    return name and ("|cffFFFFFF" .. name .. "|r") or ""
end

function expand(text, depth)
    depth = depth or 0
    if not text or text == "" or not string.find(text, "@", 1, true) or depth > MAX_DEPTH then return text, false end
    local hidden = false
    local shift = IsShiftKeyDown and IsShiftKeyDown()
    text = string.gsub(text, "@ifknown:(%d+):(.-):ifknown@", function(id, body) return knownSpell(id) and body or "" end)
    text = string.gsub(text, "@ifnotknown:(%d+):(.-):ifnotknown@", function(id, body) return knownSpell(id) and "" or body end)
    text = string.gsub(text, "@ext:(.-):ext@", function(body)
        if shift then return body end
        local head, tail = string.match(body, "^(.-)~(.*)$")
        if head then
            if tail ~= "" then hidden = true end
            return head
        end
        hidden = true
        return ""
    end)
    text = string.gsub(text, "@s:(%d+):(%-?%d+)@", function(id, offset) return expandSpell(id, offset, depth) end)
    text = string.gsub(text, "@req:(%d+)@", function(id)
        local line = spellIconName(id)
        return line and ("|cffFF1A1ARequires " .. line .. "|r") or ""
    end)
    text = string.gsub(text, "@unlockby:(%d+)@", function(id)
        local line, name = spellIconName(id)
        if not line then return "" end
        local rank = GetSpellInfo and select(2, GetSpellInfo(tonumber(id)))
        return "|cffF83600Unlocked by " .. line .. (rank and rank ~= "" and (" (" .. rank .. ")") or "") .. "|r"
    end)
    text = string.gsub(text, "@re:(%d+):(%-?%d+)@", function(id) return enchantName(id) end)
    text = string.gsub(text, "@wflocation:(.-)@", "%1")
    text = string.gsub(text, "@learns:(.-)@", "%1")
    text = string.gsub(text, "@a:(.-)@", "%1")
    -- tidy the blank lines a removed block leaves behind
    text = string.gsub(text, "\r\n", "\n")
    text = string.gsub(text, "\n\n\n+", "\n\n")
    text = string.gsub(text, "^%s+", "")
    text = string.gsub(text, "%s+$", "")
    return text, hidden
end
ASC = ASC or {}
ASC.ExpandTooltipMacros = expand

local function process(tooltip)
    local name = tooltip:GetName()
    if not name then return end
    local anyHidden, changed = false, false
    for i = 1, tooltip:NumLines() do
        local left = _G[name .. "TextLeft" .. i]
        local text = left and left:GetText()
        if text and string.find(text, "@", 1, true) then
            local out, hidden = expand(text, 0)
            if out ~= text then left:SetText(out) changed = true end
            anyHidden = anyHidden or hidden
        end
    end
    if anyHidden then tooltip:AddLine(HINT) changed = true end
    if changed then tooltip:Show() end
end

for _, tipName in ipairs({ "GameTooltip", "ItemRefTooltip", "ShoppingTooltip1", "ShoppingTooltip2" }) do
    local tip = _G[tipName]
    if tip and tip.HookScript then
        tip:HookScript("OnTooltipSetSpell", process)
        tip:HookScript("OnTooltipSetItem", process)
    end
end

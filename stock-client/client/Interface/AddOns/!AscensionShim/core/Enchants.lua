-- core/Enchants.lua: the recovered Mystic Enchant catalogue (tools/gen_enchant_data.py).
-- Read-only records: SpellID, SpellName, ClientName, Icon, Quality (Enum.EnchantQualityEnum
-- key), MaxStacks, IsWorldforged. Nothing about costs, scrolls, slots or requirements was
-- captured, and nothing here pretends otherwise.
ASC.Enchants = ASC.Enchants or {}
local Enchants = ASC.Enchants
local catalogue -- { metadata, bySpell = {}, ordered = {} }
local loading

function Enchants.Begin(metadata)
    assert(not loading, "enchant catalogue already loading")
    assert(type(metadata) == "table" and metadata.format == 1, "unsupported enchant catalogue metadata")
    loading = { metadata = metadata, bySpell = {}, ordered = {} }
end

function Enchants.Add(record)
    assert(loading, "Enchants.Begin must precede Add")
    assert(type(record) == "table" and type(record.SpellID) == "number", "invalid enchant record")
    assert(not loading.bySpell[record.SpellID], "duplicate enchant " .. record.SpellID)
    loading.bySpell[record.SpellID] = record
    loading.ordered[#loading.ordered + 1] = record
end

function Enchants.Finalize()
    assert(loading, "no enchant catalogue to finalize")
    assert(#loading.ordered == loading.metadata.enchantCount, "enchant count differs from generation manifest")
    catalogue = loading
    loading = nil
    catalogue.complete = true
end

function Enchants.Current() return catalogue end
function Enchants.Get(spellID) return catalogue and catalogue.bySpell[spellID] or nil end
function Enchants.All() return catalogue and catalogue.ordered or {} end
function Enchants.Count() return catalogue and #catalogue.ordered or 0 end

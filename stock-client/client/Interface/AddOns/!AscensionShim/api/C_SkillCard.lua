-- api/C_SkillCard.lua: Ascension's C_SkillCard / C_SkillCardCollection natives for a stock client.
--
-- Skill Cards are a Draft / WildCard game-mode system; the only capture of it
-- (cachedata/lua/harvest/skillCards.json) recorded the API surface and, per card type, the slot
-- count and an EMPTY collection (a character outside those modes). That is exactly what this
-- file serves: the slot counts below, no cards, no pending cards, no booster costs. The tab
-- therefore opens Ascension's own Skill Cards panel with empty collections, and every
-- purchase / claim / slot write answers with the panel's own error strings. No server verb
-- exists; nothing here invents a card catalogue.
local SC = ASC.Namespace("C_SkillCard")
local SCC = ASC.Namespace("C_SkillCardCollection")

-- captured maxCount per card type (skillCards.json "types")
local MAX_CARDS = {
    SKILL_CARD_DEFAULT_NORMAL = 3, SKILL_CARD_DEFAULT_GOLDEN = 3,
    SKILL_CARD_LUCKY_NORMAL = 3, SKILL_CARD_LUCKY_GOLDEN = 3,
    SKILL_CARD_STARTER_NORMAL = 2, SKILL_CARD_STARTER_GOLDEN = 2,
    SKILL_CARD_TALENT_NORMAL = 3, SKILL_CARD_TALENT_GOLDEN = 3,
}
local NO_CARDS = "SET_SKILL_CARD_NOT_COLLECTED"
local NO_SHOP = "PURCHASE_SEALED_CARD_NO_TOKEN"

local function log(msg)
    local S = ASC.Stock
    if S and S.Log then S.Log(msg) end
end

-- C_SkillCard: the character's card slots (all empty)
function SC.GetMaxCardCount(cardType) return MAX_CARDS[cardType] or 0 end
function SC.GetCardCount() return 0 end
function SC.GetCardAtIndex() return nil end
function SC.GetCardRankAtIndex() return 0 end
function SC.GetCardID() return nil end
function SC.GetCardSpellID() return nil end
function SC.GetSkillCardInfo() return nil end
function SC.GetSkillCardInfoAtIndex() return nil end
function SC.GetSkillCardQuality() return nil end
function SC.IsCardAtIndexActive() return false end
function SC.IsCardAtIndexBlocked() return false end
function SC.IsCardedID() return false end
function SC.IsCardedSpellID() return false end
function SC.SetCardAtIndex(cardType, index, cardID)
    log("C_SkillCard.SetCardAtIndex(" .. tostring(cardType) .. "," .. tostring(index) .. "," .. tostring(cardID) .. ") refused: no card collection on this port")
    return false, NO_CARDS
end
function SC.RemoveCardAtIndex() return false, NO_CARDS end

-- C_SkillCardCollection: the account's collection (empty) and the booster shop (absent)
function SCC.GetNumSkillCards() return 0 end
function SCC.GetSkillCardAtIndex() return nil end
function SCC.SetSkillCardFilter() end
function SCC.HasAnySkillCardsCollected() return false end
function SCC.IsCollected() return false end
function SCC.GetProgress() return 0 end
function SCC.GetMaxRank() return 1 end
function SCC.GetNumPendingSkillCards() return 0 end
function SCC.GetPendingSkillCardAtIndex() return nil end
function SCC.CanClaimPendingSkillCardAtIndex() return false end
function SCC.ClaimPendingSkillCard() return false end
function SCC.ClaimAllPendingSkillCards() return false end
function SCC.GetBonusSealedCardPacksProgress() return 0, 0 end
function SCC.GetMaxNumPurchasableSealedCardBoosterPacks() return 0 end
function SCC.GetSealedCardCost() return nil end
function SCC.GetSealedCardBoosterPackCost() return nil end
function SCC.CanPurchaseSealedCard() return false, NO_SHOP end
function SCC.CanPurchaseSealedCardBoosterPack() return false, NO_SHOP end
function SCC.PurchaseSealedCard() log("C_SkillCardCollection.PurchaseSealedCard refused: no booster shop on this port") return false end
function SCC.PurchaseAllSealedCards() return false end
function SCC.PurchaseSealedCardBoosterPack() return false end

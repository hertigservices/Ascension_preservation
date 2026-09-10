-- Widen the world-database columns Ascension's harvested items and quests overflow.
--
-- On a non-STRICT MySQL (every realm database here) an oversized value is silently truncated or
-- clamped, so the import "succeeds" with wrong data: item descriptions longer than 255 characters
-- lose their tail, and quest reward quantities above 65,535 (Ascension hands out currency-like
-- rewards in the hundreds of thousands) clamp. jealous-sound's CoA server carries the same two
-- widenings (data/sql/updates/pending_db_world, "562,380 items harvested out of the client's
-- itemcache" / "18,550 quests harvested"); these ALTERs are ours, sized to the same limits.
--
-- Apply with the worldserver STOPPED (a running core has item_template cached and the table open),
-- then re-run the consolidator's import_world.py so the truncated rows are written in full.
ALTER TABLE `item_template` MODIFY `description` VARCHAR(1024) NOT NULL DEFAULT '';
ALTER TABLE `quest_template`
    MODIFY `RewardAmount1` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardAmount2` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardAmount3` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardAmount4` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardChoiceItemQuantity1` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardChoiceItemQuantity2` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardChoiceItemQuantity3` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardChoiceItemQuantity4` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardChoiceItemQuantity5` INT UNSIGNED NOT NULL DEFAULT 0,
    MODIFY `RewardChoiceItemQuantity6` INT UNSIGNED NOT NULL DEFAULT 0;

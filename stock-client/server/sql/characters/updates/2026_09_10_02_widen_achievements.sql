-- Ascension achievement ids reach 322,523; AzerothCore's stock columns are smallint unsigned and
-- a clamped id collides on the primary key, rolling the whole character-create transaction
-- back (see the hub's TROUBLESHOOTING.md, "MySQL silent clamp"). Widen before the first login.
ALTER TABLE character_achievement MODIFY achievement INT UNSIGNED NOT NULL DEFAULT 0;
ALTER TABLE character_achievement_progress MODIFY criteria INT UNSIGNED NOT NULL DEFAULT 0;

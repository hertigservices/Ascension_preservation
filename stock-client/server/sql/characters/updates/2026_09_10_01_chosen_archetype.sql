-- mod-ascension-ca: P4 columns on an ascension_ca_character table created before 2026-09-10 (P3).
-- Fresh installs get them from ascension_ca_characters.sql; apply this once on older tables.
ALTER TABLE ascension_ca_character ADD COLUMN chosen TINYINT UNSIGNED NOT NULL DEFAULT 0, ADD COLUMN archetype CHAR(36) NOT NULL DEFAULT '';

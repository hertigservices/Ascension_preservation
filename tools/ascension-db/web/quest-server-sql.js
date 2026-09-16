/* Guarded AzerothCore-style reward importer embedded in every SQL download.
 * Explicit CALL selects one mode-specific source view. Never auto-apply on load.
 */
(() => {
  'use strict';
  const writable = ['RewardXPDifficulty', 'RewardMoney', 'RewardMoneyDifficulty', 'RewardHonor', 'RewardArenaPoints', 'RewardTalents'];
  for (let i = 1; i <= 4; i++) writable.push(`RewardItem${i}`, `RewardAmount${i}`);
  for (let i = 1; i <= 6; i++) writable.push(`RewardChoiceItemID${i}`, `RewardChoiceItemQuantity${i}`);
  const held = ['RewardDisplaySpell', 'RewardSpell', 'RewardTitle'];
  for (let i = 1; i <= 5; i++) held.push(`RewardFactionID${i}`, `RewardFactionValue${i}`, `RewardFactionOverride${i}`);
  const json = (alias, fields = writable) => `JSON_OBJECT(${fields.map(f => `'${f}',${alias}.\`${f}\``).join(',')})`;
  const equalImage = (alias, image) => writable.map(f => `${alias}.\`${f}\` <=> CAST(JSON_UNQUOTE(JSON_EXTRACT(${image},'$.${f}')) AS SIGNED)`).join(' AND ');
  const assign = (alias, image) => writable.map(f => `${alias}.\`${f}\`=CAST(JSON_UNQUOTE(JSON_EXTRACT(${image},'$.${f}')) AS SIGNED)`).join(',');
  function generate() {
    const checks = [...writable, ...held].map(f => `IF NOT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='quest_template' AND column_name='${f}' AND data_type IN ('tinyint','smallint','mediumint','int','bigint') AND extra NOT LIKE '%GENERATED%' AND extra NOT LIKE '%on update%') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Unsupported quest reward schema: ${f}'; END IF;`).join('\n');
    const bounds = writable.map(f => {
      const min = "CASE WHEN c.column_type LIKE '%unsigned%' THEN 0 WHEN c.data_type='tinyint' THEN -128 WHEN c.data_type='smallint' THEN -32768 WHEN c.data_type='mediumint' THEN -8388608 WHEN c.data_type='int' THEN -2147483648 ELSE -9223372036854775808 END";
      const max = "CASE c.data_type WHEN 'tinyint' THEN IF(c.column_type LIKE '%unsigned%',255,127) WHEN 'smallint' THEN IF(c.column_type LIKE '%unsigned%',65535,32767) WHEN 'mediumint' THEN IF(c.column_type LIKE '%unsigned%',16777215,8388607) WHEN 'int' THEN IF(c.column_type LIKE '%unsigned%',4294967295,2147483647) ELSE 9223372036854775807 END";
      return `WHEN r.\`${f}\` IS NOT NULL AND EXISTS(SELECT 1 FROM information_schema.columns c WHERE c.table_schema=DATABASE() AND c.table_name='quest_template' AND c.column_name='${f}' AND (r.\`${f}\` < (${min}) OR r.\`${f}\` > (${max}))) THEN 'Out of target range: ${f}'`;
    }).join('\n');
    const after = `JSON_OBJECT(${writable.map(f => `'${f}',COALESCE(r.\`${f}\`,q.\`${f}\`)`).join(',')})`;
    const heldDiff = ['RewardKillHonor', ...held].map(f => `(r.\`${f}\` IS NOT NULL AND NOT(r.\`${f}\` <=> q.\`${f}\`))`).join(' OR ');
    const rewardDiff = writable.map(f => `(r.\`${f}\` IS NOT NULL AND NOT(r.\`${f}\` <=> q.\`${f}\`))`).join(' OR ');
    const unsafeBefore = writable.map(f => `q.\`${f}\` > 9223372036854775807`).join(' OR ');
    return `
-- SERVER IMPORT: load this export into a disposable copy of your world database.
-- Stop its worldserver first. Choose ONE exact mode-specific source_path:
-- SELECT DISTINCT source_path,game_mode FROM ascension_quest_export;
-- CALL ascension_apply_quest_rewards('cachedata/by-mode/conquest-of-azeroth/questcache.tsv.gz','conquest-of-azeroth',FALSE);
-- FALSE reports every selected row; TRUE applies only rows reported ready.
-- Review the report first. Exact-name item dependencies and integer widths are checked.
-- Spells, titles, honor multipliers and reputation differences require separate core/DBC review.
-- Rune of Ascension and other item currencies use RewardItem/RewardAmount normally;
-- absent item definitions are reported, never invented. IDs do not prove mode identity.
CREATE TABLE ascension_reward_import_journal (
 quest_id BIGINT UNSIGNED PRIMARY KEY, record_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 before_json LONGTEXT NOT NULL, after_json LONGTEXT NOT NULL,
 source_path TEXT NOT NULL, game_mode TEXT NOT NULL, quest_title TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
DELIMITER $$
CREATE PROCEDURE ascension_apply_quest_rewards(IN chosen_source TEXT, IN chosen_mode TEXT, IN apply_changes BOOLEAN)
SQL SECURITY INVOKER
main: BEGIN
 DECLARE lock_ok INT DEFAULT 0; DECLARE locked_rows BIGINT DEFAULT 0;
 DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; IF lock_ok=1 THEN DO RELEASE_LOCK(CONCAT(DATABASE(),':ascension-reward-import')); END IF; RESIGNAL; END;
 DECLARE EXIT HANDLER FOR SQLWARNING BEGIN ROLLBACK; IF lock_ok=1 THEN DO RELEASE_LOCK(CONCAT(DATABASE(),':ascension-reward-import')); END IF; SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='SQL warning refused; no quest rewards changed'; END;
 IF apply_changes IS NULL OR chosen_mode IS NULL OR chosen_mode IN ('','unknown','unspecified') OR chosen_mode REGEXP '[^a-z0-9-]' OR chosen_source IS NULL OR LOCATE(CONCAT('/by-mode/',chosen_mode,'/'),chosen_source)=0 OR chosen_source NOT LIKE '%/questcache.tsv.gz' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Choose one explicit mode-specific quest cache source'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ascension_quest_export WHERE source_path=chosen_source AND game_mode=chosen_mode AND adapter='client-quest-cache') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='No matching mode-specific source in this export'; END IF;
 IF (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('quest_template','item_template','ascension_reward_import_journal') AND engine='InnoDB')<>3 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='InnoDB world quest/item tables are required'; END IF;
 IF EXISTS(SELECT 1 FROM information_schema.triggers WHERE trigger_schema=DATABASE() AND event_object_table='quest_template') OR EXISTS(SELECT 1 FROM information_schema.key_column_usage WHERE (referenced_table_schema=DATABASE() AND referenced_table_name='quest_template') OR (table_schema=DATABASE() AND table_name='quest_template' AND referenced_table_name IS NOT NULL)) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Quest triggers/foreign keys require separate review'; END IF;
 ${checks}
 SET lock_ok=GET_LOCK(CONCAT(DATABASE(),':ascension-reward-import'),10);
 IF lock_ok<>1 OR lock_ok IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Reward import lock unavailable'; END IF;
 DROP TEMPORARY TABLE IF EXISTS ascension_reward_candidates;
 CREATE TEMPORARY TABLE ascension_reward_candidates (record_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,quest_id BIGINT UNSIGNED NULL,status TEXT NOT NULL,before_json LONGTEXT,after_json LONGTEXT) ENGINE=InnoDB;
 SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
 START TRANSACTION;
 -- Lock world and staging rows during validation/application. Use while worldserver is stopped.
 SELECT COUNT(*) INTO locked_rows FROM quest_template FOR UPDATE;
 SELECT COUNT(*) INTO locked_rows FROM item_template FOR SHARE;
 SELECT COUNT(*) INTO locked_rows FROM ascension_reward_import_journal FOR UPDATE;
 SELECT COUNT(*) INTO locked_rows FROM ascension_quest_export FOR SHARE;
 SELECT COUNT(*) INTO locked_rows FROM ascension_quest_rewards FOR SHARE;
 SELECT COUNT(*) INTO locked_rows FROM ascension_quest_reward_items FOR SHARE;
 SELECT COUNT(*) INTO locked_rows FROM ascension_quest_item_evidence FOR SHARE;
 SELECT COUNT(*) INTO locked_rows FROM ascension_quest_export_issues FOR SHARE;
 INSERT INTO ascension_reward_candidates
 SELECT e.record_key,q.ID,CASE
 WHEN e.quest_id NOT REGEXP '^[1-9][0-9]{0,9}$' THEN 'Invalid quest ID'
 WHEN q.ID IS NULL THEN 'Missing quest template; no defaults invented'
 WHEN NOT(CAST(q.LogTitle AS BINARY)<=>CAST(e.title AS BINARY)) THEN 'Target quest name differs; identity review required'
 WHEN (SELECT COUNT(*) FROM ascension_quest_export other WHERE other.source_path=chosen_source AND other.game_mode=chosen_mode AND other.quest_id=e.quest_id)<>1 THEN 'Ambiguous duplicate quest in selected source'
 WHEN j.quest_id IS NOT NULL AND (NOT(${equalImage('q','j.after_json')}) OR NOT(CAST(q.LogTitle AS BINARY)<=>CAST(j.quest_title AS BINARY))) THEN 'Previously applied reward edited; refusing overwrite'
 WHEN j.quest_id IS NOT NULL AND j.record_key<>e.record_key THEN 'Another source owns the applied reward; rollback first'
 WHEN j.quest_id IS NOT NULL THEN 'already-applied'
 WHEN ${unsafeBefore} THEN 'Existing reward exceeds rollback integer range'
 WHEN EXISTS(SELECT 1 FROM ascension_quest_export_issues problem WHERE problem.record_key=e.record_key) THEN 'Source conversion issue; inspect ascension_quest_export_issues'
 WHEN EXISTS(SELECT 1 FROM ascension_quest_reward_items i LEFT JOIN item_template it ON it.entry=i.item_id WHERE i.record_key=e.record_key AND it.entry IS NULL) THEN 'Missing target reward item/currency definition'
 WHEN EXISTS(SELECT 1 FROM ascension_quest_reward_items i LEFT JOIN ascension_quest_item_evidence ie ON ie.record_key=i.record_key AND ie.item_id=i.item_id WHERE i.record_key=e.record_key AND ie.item_id IS NULL) THEN 'Missing mode-specific item identity evidence'
 WHEN EXISTS(SELECT 1 FROM ascension_quest_reward_items i JOIN ascension_quest_item_evidence ie ON ie.record_key=i.record_key AND ie.item_id=i.item_id JOIN item_template it ON it.entry=i.item_id WHERE i.record_key=e.record_key AND NOT(CAST(it.name AS BINARY)<=>CAST(ie.item_name AS BINARY))) THEN 'Target reward item name differs; identity review required'
 ${bounds}
 WHEN r.RewardReputationMask IS NOT NULL AND r.RewardReputationMask<>0 THEN 'Reputation mask requires core-specific review'
 WHEN ${heldDiff} THEN 'Spell/title/reputation reward differs; core/DBC review required'
 WHEN NOT(${rewardDiff}) THEN 'unchanged'
 ELSE 'ready' END,${json('q')},${after}
 FROM ascension_quest_export e JOIN ascension_quest_rewards r USING(record_key)
 LEFT JOIN quest_template q ON e.quest_id REGEXP '^[1-9][0-9]{0,9}$' AND q.ID=CAST(e.quest_id AS UNSIGNED)
 LEFT JOIN ascension_reward_import_journal j ON j.quest_id=q.ID
 WHERE e.source_path=chosen_source AND e.game_mode=chosen_mode AND e.adapter='client-quest-cache';
 IF apply_changes THEN
  INSERT INTO ascension_reward_import_journal SELECT quest_id,record_key,before_json,after_json,chosen_source,chosen_mode,(SELECT q.LogTitle FROM quest_template q WHERE q.ID=quest_id) FROM ascension_reward_candidates WHERE status='ready';
  UPDATE quest_template q JOIN ascension_reward_candidates c ON q.ID=c.quest_id SET ${assign('q','c.after_json')} WHERE c.status='ready';
  IF EXISTS(SELECT 1 FROM quest_template q JOIN ascension_reward_candidates c ON q.ID=c.quest_id WHERE c.status='ready' AND NOT(${equalImage('q','c.after_json')})) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Reward after-image mismatch; rolled back'; END IF;
 END IF;
 COMMIT;
 DO RELEASE_LOCK(CONCAT(DATABASE(),':ascension-reward-import'));
 SELECT record_key,quest_id,status,apply_changes AS application_requested FROM ascension_reward_candidates ORDER BY record_key;
END$$
CREATE PROCEDURE ascension_rollback_quest_rewards()
SQL SECURITY INVOKER
BEGIN
 DECLARE lock_ok INT DEFAULT 0; DECLARE locked_rows BIGINT DEFAULT 0;
 DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; IF lock_ok=1 THEN DO RELEASE_LOCK(CONCAT(DATABASE(),':ascension-reward-import')); END IF; RESIGNAL; END;
 DECLARE EXIT HANDLER FOR SQLWARNING BEGIN ROLLBACK; IF lock_ok=1 THEN DO RELEASE_LOCK(CONCAT(DATABASE(),':ascension-reward-import')); END IF; SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Rollback warning refused'; END;
 ${checks}
 SET lock_ok=GET_LOCK(CONCAT(DATABASE(),':ascension-reward-import'),10);
 IF lock_ok<>1 OR lock_ok IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Reward rollback lock unavailable'; END IF;
 IF (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('quest_template','ascension_reward_import_journal') AND engine='InnoDB')<>2 OR EXISTS(SELECT 1 FROM information_schema.triggers WHERE trigger_schema=DATABASE() AND event_object_table='quest_template') OR EXISTS(SELECT 1 FROM information_schema.key_column_usage WHERE (referenced_table_schema=DATABASE() AND referenced_table_name='quest_template') OR (table_schema=DATABASE() AND table_name='quest_template' AND referenced_table_name IS NOT NULL)) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Unsafe rollback engine, trigger or foreign key'; END IF;
 START TRANSACTION;
 SELECT COUNT(*) INTO locked_rows FROM quest_template FOR UPDATE;
 SELECT COUNT(*) INTO locked_rows FROM ascension_reward_import_journal FOR UPDATE;
 IF EXISTS(SELECT 1 FROM ascension_reward_import_journal j LEFT JOIN quest_template q ON q.ID=j.quest_id WHERE q.ID IS NULL OR NOT(${equalImage('q','j.after_json')}) OR NOT(CAST(q.LogTitle AS BINARY)<=>CAST(j.quest_title AS BINARY))) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Changed/missing quest reward; rollback refused without changes'; END IF;
 UPDATE quest_template q JOIN ascension_reward_import_journal j ON q.ID=j.quest_id SET ${assign('q','j.before_json')};
 IF EXISTS(SELECT 1 FROM quest_template q JOIN ascension_reward_import_journal j ON q.ID=j.quest_id WHERE NOT(${equalImage('q','j.before_json')})) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Rollback before-image mismatch'; END IF;
 DELETE FROM ascension_reward_import_journal;
 COMMIT;
 DO RELEASE_LOCK(CONCAT(DATABASE(),':ascension-reward-import'));
END$$
DELIMITER ;
`;
  }
  globalThis.AscensionQuestServerSQL = Object.freeze({generate, writable, held});
})();

#!/usr/bin/env python3
"""Integration checks in an explicitly labelled, disposable MySQL 8.4 container."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', required=True)
    args = parser.parse_args()
    info = json.loads(subprocess.check_output(['docker', 'inspect', args.container]))[0]
    if info['Config'].get('Labels', {}).get('assignment') != 'quest-sql-publication' or info['HostConfig']['NetworkMode'] != 'none':
        raise SystemExit('Refusing a container without the explicit disposable label/network boundary')
    db = 'quest_sql_fixture_' + uuid.uuid4().hex[:12]
    checks = []

    def mysql(sql, good=True):
        p = subprocess.run(['docker', 'exec', '-i', args.container, 'mysql', '-uroot', '-N', '-B', '--raw'],
                           input=f'USE `{db}`;\n' + sql, text=True, capture_output=True)
        if good and p.returncode:
            raise AssertionError(p.stderr)
        if not good and not p.returncode:
            raise AssertionError('Expected refusal, got success: ' + p.stdout)
        return p.stdout if good else p.stderr

    subprocess.run(['docker', 'exec', args.container, 'mysql', '-uroot', '-e', f'CREATE DATABASE `{db}` CHARACTER SET utf8mb4'], check=True)
    root = Path(__file__).resolve().parent.parent / 'ascension-db' / 'web'
    js = r'''
const base=process.argv[1];require(base+'/quest-sql.js');require(base+'/quest-server-sql.js');
const q=AscensionQuestSQL,s=AscensionQuestServerSQL;
const fields=[...s.writable,...s.held,'RewardKillHonor'];
let out=q.header({test:true});
out+='CREATE TABLE quest_template (ID INT UNSIGNED PRIMARY KEY,LogTitle TEXT NOT NULL,'+fields.map(f=>'`'+f+'` '+(f==='RewardKillHonor'?'FLOAT':f==='RewardAmount1'?'SMALLINT UNSIGNED':f==='RewardMoney'?'INT':'INT UNSIGNED')+' NOT NULL DEFAULT 0').join(',')+') ENGINE=InnoDB;\n';
out+='CREATE TABLE item_template (entry INT UNSIGNED PRIMARY KEY,name TEXT NOT NULL) ENGINE=InnoDB;\n';
out+="INSERT INTO item_template VALUES(375250,'Rune of Ascension'),(123,'Different item');\n";
const stats=q.summary(),source='cachedata/by-mode/conquest-of-azeroth/questcache.tsv.gz';
for(let id=1;id<=7;id++){
 out+=`INSERT INTO quest_template(ID,LogTitle,RewardAmount1,RewardItem1,RewardMoney) VALUES(${id},'Quest ${id}',1,375250,10);\n`;
 const raw={RewOrReqMoney:id===5?'-100':'10',RewardItem1:id===2?'999':id===7?'123':'375250',RewardAmount1:id===4?'NaN':id===1?'100000':'1',Details:"'; DROP TABLE item_template; --\\\u0000雪"};
 const record=[String(id),id===3?'Different quest':`Quest ${id}`,'quest','conquest-of-azeroth','Client captures',raw];
 const row=q.convert(record,{key:`a/0/${id}`,path:source,identity:'test'});
 row.itemEvidence={path:'itemcache.tsv.gz',sha256:'a'.repeat(64),items:{375250:['Rune of Ascension','10'],123:['Source item','10']}};
 out+=q.sql(row);q.count(stats,row);
 if(id===6){row.key='b/0/6';out+=q.sql(row);q.count(stats,row);}
}
out+=q.footer(stats)+s.generate();process.stdout.write(out);
'''
    sql = subprocess.check_output(['node', '-e', js, str(root)], text=True)
    mysql(sql)
    checks.append('complete SQL and both server procedures load on MySQL 8.4')
    call = "CALL ascension_apply_quest_rewards('cachedata/by-mode/conquest-of-azeroth/questcache.tsv.gz','conquest-of-azeroth',{});"
    report = mysql(call.format('FALSE'))
    for reason in ('Out of target range', 'Missing target reward item', 'Target quest name differs', 'Source conversion issue', 'Ambiguous duplicate', 'Target reward item name differs', 'ready'):
        assert reason in report, (reason, report)
    assert mysql('SELECT RewardMoney FROM quest_template WHERE ID=5;').strip() == '10'
    checks.append('dry run reports overflow, missing currency, identity mismatch, malformed value, duplicate and ready rows without changes')
    mysql(call.format('TRUE'))
    assert mysql('SELECT RewardMoney FROM quest_template WHERE ID=5;').strip() == '-100'
    assert mysql('SELECT RewardAmount1 FROM quest_template WHERE ID=1;').strip() == '1'
    checks.append('application changes only eligible rewards, preserving refused rows')
    mysql('ALTER TABLE quest_template MODIFY RewardAmount1 INT UNSIGNED NOT NULL DEFAULT 0;')
    mysql(call.format('TRUE'))
    assert mysql('SELECT RewardAmount1 FROM quest_template WHERE ID=1;').strip() == '100000'
    checks.append('100000 currency units survive exactly on an explicitly widened compatible target')
    repeat = mysql(call.format('TRUE'))
    assert repeat.count('already-applied') == 2, repeat
    assert mysql('SELECT COUNT(*) FROM ascension_reward_import_journal;').strip() == '2'
    checks.append('repeat application is idempotent and retains one before-image per quest')
    mysql('UPDATE quest_template SET RewardAmount1=999 WHERE ID=1;')
    assert 'rollback refused' in mysql('CALL ascension_rollback_quest_rewards();', good=False)
    assert mysql('SELECT RewardMoney FROM quest_template WHERE ID=5;').strip() == '-100'
    checks.append('operator edits refuse the entire rollback without partial restoration')
    assert 'Previously applied reward edited' in mysql(call.format('TRUE'))
    checks.append('reapplication refuses to overwrite post-import operator edits')
    mysql('UPDATE quest_template SET RewardAmount1=100000 WHERE ID=1; CALL ascension_rollback_quest_rewards();')
    assert mysql('SELECT RewardAmount1,RewardMoney FROM quest_template WHERE ID=1;').strip() == '1\t10'
    assert mysql('SELECT RewardMoney FROM quest_template WHERE ID=5;').strip() == '10'
    assert mysql('SELECT COUNT(*) FROM ascension_reward_import_journal;').strip() == '0'
    mysql('CALL ascension_rollback_quest_rewards();')
    checks.append('guarded rollback and repeated rollback restore original reward values')
    mysql('CREATE TRIGGER test_reward_trigger BEFORE UPDATE ON quest_template FOR EACH ROW SET NEW.RewardMoney=0;')
    assert 'triggers' in mysql(call.format('TRUE'), good=False)
    mysql('DROP TRIGGER test_reward_trigger;')
    checks.append('target triggers are refused before any application')
    assert 'explicit mode-specific' in mysql("CALL ascension_apply_quest_rewards('cachedata/union/questcache.tsv.gz','conquest-of-azeroth',TRUE);", good=False)
    checks.append('union/implicit source application is refused')
    assert 'explicit mode-specific' in mysql("CALL ascension_apply_quest_rewards('cachedata/by-mode/unknown/questcache.tsv.gz','unknown',TRUE);", good=False)
    mysql('ALTER TABLE quest_template MODIFY RewardAmount1 BIGINT UNSIGNED NOT NULL DEFAULT 0; UPDATE quest_template SET RewardAmount1=18446744073709551615 WHERE ID=1;')
    assert 'Existing reward exceeds rollback integer range' in mysql(call.format('TRUE'))
    mysql('UPDATE quest_template SET RewardAmount1=1 WHERE ID=1; CALL ascension_rollback_quest_rewards();')
    checks.append('unknown modes and unrepresentable before-images are refused')
    assert mysql('SELECT COUNT(*) FROM item_template;').strip() == '2'
    text_hex = mysql("SELECT HEX(JSON_UNQUOTE(JSON_EXTRACT(raw_json,'$.Details'))) FROM ascension_quest_export LIMIT 1;").strip()
    assert bytes.fromhex(text_hex).decode() == "'; DROP TABLE item_template; --\\\0雪"
    checks.append('SQL injection text, NUL, backslash and Unicode round-trip without execution')
    print(json.dumps({'database': db, 'engine': mysql('SELECT VERSION();').strip(), 'checks': checks, 'check_count': len(checks)}, indent=2))


if __name__ == '__main__':
    main()

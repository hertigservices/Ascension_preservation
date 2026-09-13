import json
from pathlib import Path
import tempfile
import unittest
import intake
import privacy


class PrivacyTests(unittest.TestCase):
    def test_private_patterns_missing_invalid_and_synthetic_identity(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'private.txt'
            with self.assertRaises(ValueError):privacy.IdentityGuard(p)
            p.write_text('# private rules\nFixture Secret Organisation\n\\bExampleHandle\\b\n')
            guard=privacy.IdentityGuard(p);guard.check({'name':'Hogger'})
            with self.assertRaisesRegex(ValueError,'matching identity is not logged'):guard.check({'name':'EXAMPLEHANDLE'})
            p.write_text('[bad regex')
            with self.assertRaisesRegex(ValueError,'file is invalid'):privacy.IdentityGuard(p)
    def test_configured_guard_is_required_again_for_delivery_and_metadata_changes_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);donation=root/'rows.json';donation.write_text('{"id":448,"name":"Hogger"}')
            patterns=root/'patterns';patterns.write_text('Synthetic Secret Organisation\n')
            private=root/'private';private.mkdir()
            (private/'publication-config.json').write_text(json.dumps({'identity_patterns_file':str(patterns)}))
            policy=root/'policy.json';policy.write_text(json.dumps({'source':'example','publication':'approved','permission':'Synthetic test fixture','collections':{'records':{'kind':'npc','fields':{'id':'id','name':'name'}}}}))
            with intake.Intake(private,reserve=0) as e:
                report=e.ingest(donation,'example');self.assertEqual(report['examples'][0]['name'],'Hogger')
                first=intake.export(e,'example',policy,root/'public')
                with self.assertRaises(ValueError):intake.verify_export(first)
                intake.verify_export(first,privacy.configured(private))
                metadata=e.source('example');metadata['trust']='aggregated-suspect';metadata['known_caps']={'rows_per_npc':20};e.source('example',metadata)
                second=intake.export(e,'example',policy,root/'public');self.assertNotEqual(first,second)
                patterns.unlink()
                with self.assertRaises(ValueError):intake.export(e,'example',policy,root/'public')
if __name__=='__main__':unittest.main()

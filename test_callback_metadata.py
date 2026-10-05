import json
import unittest
from ops.callback_metadata import digest,summary

class CallbackMetadataTests(unittest.TestCase):
    def test_changed_create_url_has_disjoint_exact_approval(self):
        first='https://callback.example/a-private-path';second='https://callback.example/b-private-path'
        state={'callback_approved':[first],'callback_pending':{'candidate':{'url':second,'expires':1600}}}
        result=summary(state,1000)
        self.assertEqual(result['approved_digests'],[digest(first)])
        self.assertEqual(result['pending'][0]['digest'],digest(second))
        self.assertEqual(result['overlap_digests'],[])
        self.assertNotIn('private-path',json.dumps(result));self.assertNotIn('https://',json.dumps(result))
    def test_summary_reports_expiry_without_modifying_state(self):
        state={'callback_pending':{'candidate':{'url':'https://callback.example/a','expires':1600}}}
        before=json.dumps(state)
        self.assertTrue(summary(state,1600)['pending'][0]['expired'])
        self.assertEqual(before,json.dumps(state))

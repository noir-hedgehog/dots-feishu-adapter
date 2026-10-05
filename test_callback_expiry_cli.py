import unittest
from deploy.tunnel_admin import valid_pending_callback,LocalActionError

class CallbackExpiryCLITests(unittest.TestCase):
    def control(self,now=1000):
        from types import SimpleNamespace
        state={'personal_audience_confirmed':True,'binding':['owner','user','chat'],'callback_pending':{'candidate':{'expires':1600,'url':'https://callback.example/events'}}}
        return SimpleNamespace(snapshot=lambda:state,clock=lambda:now)
    def test_fifteen_minute_old_candidate_is_expired_even_with_correct_id(self):
        with self.assertRaises(LocalActionError) as result:valid_pending_callback(self.control(1900),'candidate')
        self.assertEqual(result.exception.code,'callback_expired')
    def test_boundary_and_missing_candidate_fail_closed(self):
        with self.assertRaises(LocalActionError):valid_pending_callback(self.control(1600),'candidate')
        with self.assertRaises(LocalActionError) as result:valid_pending_callback(self.control(),'wrong')
        self.assertEqual(result.exception.code,'callback_missing')
    def test_current_candidate_and_pairing_required(self):
        self.assertEqual(valid_pending_callback(self.control(1500),'candidate')['expires'],1600)
        control=self.control();control.snapshot()['binding']=None
        with self.assertRaises(LocalActionError) as result:valid_pending_callback(control,'candidate')
        self.assertEqual(result.exception.code,'not_paired')

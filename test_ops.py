import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from deploy.run import HealthApplication, read_secret

class OpsTests(unittest.TestCase):
    def test_credentials_private_and_nonempty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test'
            path.write_text('value\n'); path.chmod(0o600)
            self.assertEqual(read_secret('test', '', directory), 'value')
            path.chmod(0o644)
            with self.assertRaises(ValueError): read_secret('test', '', directory)
            path.chmod(0o600); path.write_text('')
            with self.assertRaises(ValueError): read_secret('test', '', directory)
    def test_credentials_reject_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test'; path.symlink_to('/dev/null')
            with self.assertRaises(ValueError): read_secret('test', '', directory)
    def test_health_does_not_claim_subscription_delivery(self):
        import threading
        adapter = SimpleNamespace(lock=threading.Lock(), subscriptions={}, outbox={})
        holder = {}
        app = HealthApplication(None, adapter, holder, SimpleNamespace(is_alive=lambda: True))
        statuses = []
        body = app({'PATH_INFO': '/readyz'}, lambda status, headers: statuses.append(status))
        self.assertEqual(statuses[-1], '503 Service Unavailable')
        holder['client'] = SimpleNamespace(_conn=SimpleNamespace(state=SimpleNamespace(name='OPEN')))
        body = app({'PATH_INFO': '/readyz'}, lambda status, headers: statuses.append(status))
        self.assertEqual(statuses[-1], '200 OK')
        self.assertEqual(json.loads(body[0])['subscriptions'], 0)
        holder['client']._conn.state.name = 'CLOSED'
        app({'PATH_INFO': '/readyz'}, lambda status, headers: statuses.append(status))
        self.assertEqual(statuses[-1], '503 Service Unavailable')

import contextlib
import errno
import io
import json
import unittest
from unittest.mock import patch
from deploy.diagnostics import phase,report,safe_error
from ops.tunnel_profile import render_profile
from ops import apply_startup_fix

class StartupFixTests(unittest.TestCase):
    def test_profile_uses_runtime_file_without_environment_dependency(self):
        profile=render_profile()
        self.assertIn('api_key: "file:/run/credentials/feishu-dot-tunnel.service/control_plane_api_key"',profile)
        self.assertNotIn('env:',profile)
        self.assertIn('127.0.0.1:8765/mcp',profile)
        self.assertIn('127.0.0.1:8766',profile)
        self.assertNotIn('0.0.0.0',profile)
    def test_error_output_never_contains_exception_text_paths_or_values(self):
        sensitive='PRIVATE_TEST_CONTENT https://example.invalid/private-path?credential=test'
        cases=[ValueError(sensitive),PermissionError(sensitive),FileNotFoundError(sensitive),OSError(errno.EROFS,sensitive),json.JSONDecodeError(sensitive,sensitive,1)]
        for error in cases:
            output=json.dumps(safe_error(error,'credential_load'))
            self.assertNotIn(sensitive,output)
            self.assertNotIn('example.invalid',output)
            self.assertEqual(json.loads(output)['stage'],'credential_load')
        self.assertEqual(safe_error(OSError(errno.EADDRINUSE,sensitive),'http_bind')['category'],'port_in_use')
    def test_feishu_client_error_only_numeric_code(self):
        from lark_oapi.ws.exception import ClientException
        result=safe_error(ClientException(10014,'PRIVATE_TEST_CONTENT'),'feishu_ws_connect')
        self.assertEqual(result,{'event':'adapter_error','stage':'feishu_ws_connect','category':'feishu_endpoint_rejected','provider_code':10014})
    def test_nested_phase_logs_once_at_nearest_failure(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(ValueError):
                with phase('startup'):
                    with phase('control_load'):raise ValueError('PRIVATE_TEST_CONTENT')
        records=output.getvalue().splitlines()
        self.assertEqual(len(records),1)
        self.assertEqual(json.loads(records[0])['stage'],'control_load')
        self.assertNotIn('PRIVATE_TEST_CONTENT',output.getvalue())
    def test_patch_refuses_running_or_autorestarting_services_without_writes(self):
        for state in ('active','activating','deactivating'):
            with patch('ops.apply_startup_fix.os.geteuid',return_value=0),patch('ops.apply_startup_fix.subprocess.check_output',return_value=state),patch('ops.apply_startup_fix.atomic_install') as install:
                with self.assertRaises(ValueError):apply_startup_fix.main()
                install.assert_not_called()
    def test_patch_does_not_start_stop_or_read_credentials(self):
        import inspect
        source=inspect.getsource(apply_startup_fix)
        self.assertNotIn("'start'",source)
        self.assertNotIn("'restart'",source)
        self.assertNotIn("'stop'",source)
        self.assertNotIn('read_secret',source)
        self.assertNotIn('.env',source)
    def test_future_health_telemetry_has_no_ids_messages_or_keys(self):
        from types import SimpleNamespace
        from deploy.tunnel_runtime import Gateway
        holder={'client':SimpleNamespace(_conn=SimpleNamespace(state=SimpleNamespace(name='OPEN'))),'monitor':SimpleNamespace(is_alive=lambda:True),'control_status':{'pairing_candidates':1,'binding_confirmed':False},'sdk_received_events':1,'last_sdk_event_at':123}
        status=[]
        body=Gateway(holder)({'PATH_INFO':'/readyz'},lambda s,h:status.append(s))
        payload=json.loads(body[0]);self.assertEqual(payload['sdk_received_events'],1)
        self.assertEqual(payload['pairing_candidates'],1)
        self.assertFalse(payload['paired'])
        self.assertFalse(set(payload)&{'secret','text','user','chat','url'})
    def test_systemd_0440_accepted_only_for_verified_runtime_directory(self):
        import tempfile
        from pathlib import Path
        from deploy.run import read_secret
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'dummy';p.write_text('test-only-value');p.chmod(0o440)
            with self.assertRaises(ValueError):read_secret('dummy','',directory)
            with patch('deploy.run.systemd_credential_directory',return_value=True):
                self.assertEqual(read_secret('dummy','',directory),'test-only-value')
                p.chmod(0o444)
                with self.assertRaises(ValueError):read_secret('dummy','',directory)
                p.chmod(0o640)
                with self.assertRaises(ValueError):read_secret('dummy','',directory)
    def test_systemd_directory_provenance_and_readonly_guard(self):
        from deploy.run import systemd_credential_directory
        from types import SimpleNamespace
        import os
        with patch.dict(os.environ,{'CREDENTIALS_DIRECTORY':'/run/credentials/feishu-dot-adapter.service'}):
            with patch('deploy.run.Path.is_symlink',return_value=False),patch('deploy.run.Path.is_dir',return_value=True),patch('deploy.run.Path.stat',return_value=SimpleNamespace(st_uid=0,st_mode=0o40550)):
                self.assertTrue(systemd_credential_directory('/run/credentials/feishu-dot-adapter.service'))
                self.assertFalse(systemd_credential_directory('/tmp/arbitrary'))
            with patch('deploy.run.Path.is_symlink',return_value=False),patch('deploy.run.Path.is_dir',return_value=True),patch('deploy.run.Path.stat',return_value=SimpleNamespace(st_uid=0,st_mode=0o40750)):
                self.assertFalse(systemd_credential_directory('/run/credentials/feishu-dot-adapter.service'))
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(systemd_credential_directory('/run/credentials/feishu-dot-adapter.service'))
    def test_log_reader_rejects_unknown_values_and_extra_fields(self):
        from ops.show_startup_errors import accepted
        record={'event':'adapter_error','stage':'credential_load','category':'configuration_or_credential_validation_failed'}
        self.assertTrue(accepted(record))
        self.assertFalse(accepted({**record,'text':'PRIVATE_TEST_CONTENT'}))
        self.assertFalse(accepted({**record,'category':'PRIVATE_TEST_CONTENT'}))
        self.assertFalse(accepted({**record,'provider_code':'PRIVATE_TEST_CONTENT'}))

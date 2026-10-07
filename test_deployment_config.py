"""Generic deployment targets must be explicit and preserve existing bindings."""
import json
import tempfile
import unittest
from pathlib import Path
from deploy.literal_env import parse_literal_env,validate_identifier
from ops.tunnel_profile import render_profile,profile_tunnel_id
from ops.enable_personal import validate_existing_target


class DeploymentConfigTests(unittest.TestCase):
    def test_user_supplied_ids_drive_configuration(self):
        for app,tunnel in [('cli_testA','tunnel_testA'),('cli_testB','tunnel_testB')]:
            raw=f'FEISHU_APP_ID={app}\nFEISHU_APP_SECRET=test-only\nTUNNEL_ID={tunnel}\nCONTROL_PLANE_API_KEY=test-only\n'
            values=parse_literal_env(raw)
            self.assertEqual(values['FEISHU_APP_ID'],app)
            self.assertEqual(profile_tunnel_id(render_profile(values['TUNNEL_ID'])),tunnel)

    def test_placeholders_and_yaml_injection_are_rejected(self):
        for value in ('REPLACE_WITH_TUNNEL_ID','tunnel_', 'tunnel_test"\nother: value','tunnel_test # comment','tunnel_'+'a'*129):
            with self.subTest(value=value),self.assertRaises(ValueError):render_profile(value)
        with self.assertRaises(ValueError):validate_identifier('REPLACE_WITH_FEISHU_APP_ID','cli_')

    def test_profile_repair_preserves_target_and_uses_credential_file(self):
        legacy=render_profile('tunnel_existing').replace(
            'file:/run/credentials/feishu-dot-tunnel.service/control_plane_api_key','env:CONTROL_PLANE_API_KEY')
        repaired=render_profile(profile_tunnel_id(legacy))
        self.assertEqual(profile_tunnel_id(repaired),'tunnel_existing')
        self.assertNotIn('env:',repaired)
        self.assertIn('file:/run/credentials/feishu-dot-tunnel.service/control_plane_api_key',repaired)

    def test_profile_repair_refuses_missing_placeholder_or_duplicate_id(self):
        for text in ('', 'control_plane:\n  tunnel_id: "REPLACE_WITH_TUNNEL_ID"\n',
                'control_plane:\n  tunnel_id: "tunnel_a"\n  tunnel_id: "tunnel_b"\n'):
            with self.assertRaises(ValueError):profile_tunnel_id(text)

    def test_reprovisioning_preserves_existing_app_and_tunnel(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json';profile=Path(directory)/'profile.yaml'
            config.write_text(json.dumps({'feishu_app_id':'cli_existing'}))
            profile.write_text(render_profile('tunnel_existing'))
            before=(config.read_bytes(),profile.read_bytes())
            validate_existing_target(config,profile,'cli_existing','tunnel_existing')
            for app,tunnel in [('cli_other','tunnel_existing'),('cli_existing','tunnel_other')]:
                with self.assertRaises(ValueError):validate_existing_target(config,profile,app,tunnel)
            self.assertEqual((config.read_bytes(),profile.read_bytes()),before)

    def test_new_install_has_no_implicit_target(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json';profile=Path(directory)/'profile.yaml'
            validate_existing_target(config,profile,'cli_new','tunnel_new')
            self.assertFalse(config.exists());self.assertFalse(profile.exists())


if __name__=='__main__':unittest.main()

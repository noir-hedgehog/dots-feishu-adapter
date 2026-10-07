import tempfile
import unittest
from pathlib import Path
from deploy.literal_env import parse_literal_env,read_literal_env

APP_ID='cli_testapp'
TUNNEL_ID='tunnel_testtarget'

class LiteralEnvTests(unittest.TestCase):
    def raw(self,secret='dummy-feishu',key='dummy-control'):
        return f'FEISHU_APP_ID={APP_ID}\nFEISHU_APP_SECRET={secret}\nTUNNEL_ID={TUNNEL_ID}\nCONTROL_PLANE_API_KEY={key}\n'
    def test_exact_keys_and_identifiers(self):
        data=parse_literal_env(self.raw());self.assertEqual(data['FEISHU_APP_SECRET'],'dummy-feishu')
        for raw in [self.raw()+'OTHER=1\n',self.raw()+'FEISHU_APP_SECRET=other\n',self.raw().replace(APP_ID,'wrong'),self.raw().replace(TUNNEL_ID,'wrong')]:
            with self.assertRaises(ValueError):parse_literal_env(raw)
    def test_empty_template_refuses_enable(self):
        with self.assertRaises(ValueError):parse_literal_env(self.raw('',''))
        self.assertEqual(parse_literal_env(self.raw('',''),require_secrets=False)['CONTROL_PLANE_API_KEY'],'')
    def test_no_shell_evaluation_or_interpolation(self):
        literal='$(touch /tmp/SHOULD_NOT_EXIST);`id`$HOME#literal=equals'
        self.assertEqual(parse_literal_env(self.raw(literal))['FEISHU_APP_SECRET'],literal)
        with self.assertRaises(ValueError):parse_literal_env(self.raw('"quoted"'))
        with self.assertRaises(ValueError):parse_literal_env('export '+self.raw())
    def test_private_regular_file_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'dummy.env';p.write_text(self.raw());p.chmod(0o600)
            self.assertEqual(read_literal_env(p)[1]['CONTROL_PLANE_API_KEY'],'dummy-control')
            p.chmod(0o644)
            with self.assertRaises(ValueError):read_literal_env(p)
            link=Path(directory)/'link';link.symlink_to(p)
            with self.assertRaises(OSError):read_literal_env(link)
    def test_controls_size_and_multiline_rejected(self):
        for raw in [self.raw('bad\x00value'),self.raw('a'*16385),self.raw('one\ntwo')]:
            with self.assertRaises(ValueError):parse_literal_env(raw)

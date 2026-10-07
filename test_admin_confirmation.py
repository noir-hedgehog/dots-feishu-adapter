import unittest
from unittest.mock import patch
from deploy.tunnel_admin import confirmation

class ConfirmationTests(unittest.TestCase):
    def test_terminal_padding_preserves_explicit_confirmation(self):
        for value in ['RESET PAIRING',' RESET PAIRING','RESET PAIRING ','  RESET PAIRING  ']:
            with patch('builtins.input',return_value=value):self.assertTrue(confirmation('prompt','RESET PAIRING'))
    def test_empty_wrong_or_changed_phrase_stays_rejected(self):
        for value in ['', ' ', 'yes', 'reset pairing','RESET  PAIRING','RESET PAIRING extra']:
            with patch('builtins.input',return_value=value):self.assertFalse(confirmation('prompt','RESET PAIRING'))

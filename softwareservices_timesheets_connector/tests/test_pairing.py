"""Koppelen, testen en ontkoppelen vanuit Odoo (SPEC 5.1 §4.2, §8, §14.17), met een nep van de dienst in plaats van het net."""
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .. import ssts
from .common import SECRET_B64

SERVICE = 'https://dienst.example.com'


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self.content = json.dumps(payload).encode()
        self.headers = headers or {}

    def json(self):
        return json.loads(self.content)


class FakeService:
    """Plays our /api/module/v1/*: checks the signature of signed calls and signs its own answers."""

    def __init__(self):
        self.calls = []
        self.secret = None

    def __call__(self, url, raw, hdrs):
        path = url[len(SERVICE):]
        body = json.loads(raw)
        self.calls.append((path, body, dict(hdrs)))
        if path == '/api/module/v1/pair':
            if body['code'] != 'ABCD-EFGH-JKLM':
                return FakeResponse(404, {'error': 'pair_code_invalid'})
            self.secret = SECRET_B64
            return FakeResponse(200, {'connection_id': 'c_1', 'key_id': 'k_pair', 'secret': SECRET_B64, 'service_url': SERVICE,
                                      'mode': 'push', 'poll_interval_seconds': 300, 'server_time': 1})
        secret = ssts.secret_bytes(self.secret)
        ssts.verify(secret, ssts.TO_APP, path, raw, hdrs)
        payload = {'ok': True, 'tenant_name': 'Klant BV'}
        answer = json.dumps(payload).encode()
        return FakeResponse(200, payload, ssts.headers(secret, None, ssts.TO_ODOO, path, answer))


@tagged('post_install', '-at_install', 'ss_timesheets')
class TestPairing(TransactionCase):

    def setUp(self):
        super().setUp()
        self.conn = self.env['ss_timesheets.connection'].get_connection()
        self.conn.write({'service_url': SERVICE, 'odoo_url': 'https://odoo.klant.be', 'state': 'unpaired', 'key_id': False,
                         'secret': False})
        self.fake = FakeService()
        patcher = patch.object(type(self.conn), '_http_post', lambda rec, url, raw, hdrs: self.fake(url, raw, hdrs))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pair_test_and_unpair(self):
        self.conn.pair_code = 'abcd efgh-jklm'
        self.conn.action_pair()
        path, body, hdrs = self.fake.calls[0]
        self.assertEqual(path, '/api/module/v1/pair')
        self.assertEqual(body['code'], 'ABCD-EFGH-JKLM')
        self.assertEqual(body['odoo_url'], 'https://odoo.klant.be')
        self.assertTrue(body['server_version'].startswith('18') and body['module_version'].startswith('18.0.'))
        self.assertTrue(body['db_uuid'] and body['companies'] and body['mode'] == 'push')
        self.assertNotIn('X-SS-Signature', hdrs)                                   # de code is de authenticatie
        self.assertEqual((self.conn.state, self.conn.key_id, self.conn.secret, self.conn.pair_code), ('paired', 'k_pair', SECRET_B64, False))
        # Test: ondertekend heen en terug
        result = self.conn.action_test()
        self.assertEqual(result['params']['type'], 'success')
        self.assertIn('Klant BV', self.conn.last_message)
        self.assertEqual(self.fake.calls[-1][2]['X-SS-Key-Id'], 'k_pair')
        # Ontkoppelen: /revoke aangeroepen en het geheim gewist
        self.conn.action_unpair()
        self.assertEqual(self.fake.calls[-1][0], '/api/module/v1/revoke')
        self.assertEqual((self.conn.state, self.conn.key_id, self.conn.secret), ('unpaired', False, False))

    def test_wrong_code_and_insecure_url(self):
        self.conn.pair_code = 'ZZZZ-ZZZZ-ZZZZ'
        with self.assertRaises(UserError):
            self.conn.action_pair()
        self.assertEqual(self.conn.state, 'unpaired')
        self.conn.pair_code = 'kort'
        with self.assertRaises(UserError):
            self.conn.action_pair()
        self.conn.write({'pair_code': 'ABCD-EFGH-JKLM', 'service_url': 'http://dienst.example.com'})
        with self.assertRaises(UserError):
            self.conn.action_pair()
        self.assertEqual(len(self.fake.calls), 1)                                  # alleen de foute code ging over de lijn

    def test_unsigned_answer_is_refused(self):
        self.conn.write({'state': 'paired', 'key_id': 'k_pair', 'secret': SECRET_B64})
        self.fake.secret = SECRET_B64
        with patch.object(type(self.conn), '_http_post', lambda rec, url, raw, hdrs: FakeResponse(200, {'ok': True})):
            with self.assertRaises(UserError):
                self.conn.action_test()

    def test_unpair_when_service_is_down(self):
        import requests
        self.conn.write({'state': 'paired', 'key_id': 'k_pair', 'secret': SECRET_B64})

        def down(rec, url, raw, hdrs):
            raise requests.ConnectionError('weg')
        with patch.object(type(self.conn), '_http_post', down):
            self.conn.action_unpair()
        self.assertEqual((self.conn.state, self.conn.secret), ('unpaired', False))
        self.assertIn('not reachable', self.conn.last_message)

    def test_secret_never_logged(self):
        with self.assertLogs('odoo.addons.softwareservices_timesheets_connector', level='INFO') as logs:
            self.conn.pair_code = 'ABCD-EFGH-JKLM'
            self.conn.action_pair()
            self.conn.action_unpair()
        self.assertFalse(any(SECRET_B64 in line for line in logs.output))

"""De controller over HTTP (SPEC 5.1 §5, §7, §14.12 en 14.16): handtekening, tijd, nonce, en een ondertekend antwoord."""
import json
import time

from odoo.tests import HttpCase, tagged

from .. import ssts
from .common import KEY_ID, SECRET_B64, ConnectorCase

PREFIX = '/ss_timesheets/v1/'


@tagged('post_install', '-at_install', 'ss_timesheets')
class TestController(HttpCase, ConnectorCase):

    def post(self, endpoint, body=None, key_id=KEY_ID, secret=SECRET_B64, now=None, headers=None, raw=None):
        path = PREFIX + endpoint
        raw = raw if raw is not None else json.dumps(body or {}).encode()
        hdrs = {'Content-Type': 'application/json'}
        if secret:
            hdrs.update(ssts.headers(ssts.secret_bytes(secret), key_id, ssts.TO_ODOO, path, raw, now=now))
        hdrs.update(headers or {})
        res = self.opener.post(self.base_url() + path, data=raw, headers=hdrs, timeout=30)
        return res, hdrs

    def assertSigned(self, res, endpoint):
        self.assertTrue(ssts.verify(ssts.secret_bytes(SECRET_B64), ssts.TO_APP, PREFIX + endpoint, res.content, res.headers))

    def test_ping_and_refusals(self):
        res = self.opener.post(self.base_url() + PREFIX + 'ping', data=b'{}', headers={'Content-Type': 'application/json'}, timeout=30)
        self.assertEqual((res.status_code, res.json()), (401, {'error': 'unknown_key'}))
        res, hdrs = self.post('ping')
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers.get('Cache-Control'), 'no-store')
        self.assertSigned(res, 'ping')
        data = res.json()
        self.assertTrue(data['server_version'].startswith('20'))
        self.assertEqual(data['companies'][0]['id'], self.company_a.id)
        # hetzelfde verzoek nog eens → replay
        again = self.opener.post(self.base_url() + PREFIX + 'ping', data=b'{}', headers=hdrs, timeout=30)
        self.assertEqual((again.status_code, again.json()), (401, {'error': 'replay'}))
        old, _h = self.post('ping', now=time.time() - 400)
        self.assertEqual((old.status_code, old.json()['error']), (401, 'clock_skew'))
        self.assertTrue(old.json()['server_time'])
        bad, _h = self.post('ping', secret='eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=')
        self.assertEqual(bad.json(), {'error': 'bad_signature'})
        unknown, _h = self.post('ping', key_id='k_onbekend')
        self.assertEqual(unknown.json(), {'error': 'unknown_key'})
        # handtekening over een andere body
        _res, hdrs = self.post('companies')
        tampered = self.opener.post(self.base_url() + PREFIX + 'companies', data=b'{"x":1}',
                                    headers={**hdrs, 'X-SS-Nonce': 'b' * 32}, timeout=30)
        self.assertEqual(tampered.json(), {'error': 'bad_signature'})
        logged = self.env['ir.logging'].sudo().search([('name', '=', 'ss_timesheets')])
        self.assertTrue(logged)
        self.assertFalse(any(SECRET_B64 in m for m in logged.mapped('message')))

    def test_bad_signature_leaves_no_nonce_and_old_nonces_go(self):
        """I7: wie het geheim niet kent, laat geen nonce achter; elke nieuwe nonce ruimt die ouder dan 15 minuten op."""
        cr = self.env.cr
        bad, hdrs = self.post('ping', secret='eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=')
        self.assertEqual(bad.json(), {'error': 'bad_signature'})
        cr.execute('SELECT count(*) FROM ss_timesheets_nonce WHERE nonce = %s', (hdrs['X-SS-Nonce'],))
        self.assertEqual(cr.fetchone()[0], 0)
        cr.execute("""INSERT INTO ss_timesheets_nonce (key_id, nonce, seen_at)
                      VALUES (%s, %s, now() at time zone 'UTC' - interval '20 minutes')""", (KEY_ID, 'a' * 32))
        good, hdrs = self.post('ping')
        self.assertEqual(good.status_code, 200, good.text)
        cr.execute('SELECT nonce FROM ss_timesheets_nonce WHERE key_id = %s', (KEY_ID,))
        nonces = [r[0] for r in cr.fetchall()]
        self.assertIn(hdrs['X-SS-Nonce'], nonces)
        self.assertNotIn('a' * 32, nonces)

    def test_unknown_endpoint_and_get(self):
        res, _h = self.post('partners')
        self.assertEqual(res.status_code, 404)
        res = self.opener.get(self.base_url() + PREFIX + 'ping', timeout=30)
        self.assertIn(res.status_code, (404, 405))

    def test_bad_json(self):
        res, _h = self.post('ping', raw=b'[1, 2')
        self.assertEqual(res.status_code, 400)
        self.assertSigned(res, 'ping')

    def test_create_over_http(self):
        res, _h = self.post('timesheets/create', {'lines': [self.line_body(client_ref='http-1')]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertSigned(res, 'timesheets/create')
        result = res.json()['results'][0]
        line = self.env['account.analytic.line'].browse(result['id'])
        self.assertEqual((line.ss_client_ref, line.employee_id, line.create_uid), ('http-1', self.emp_an, self.connector))
        refused, _h = self.post('timesheets/create', {'lines': [self.line_body(client_ref='http-2', employee_id=self.emp_bo.id)]})
        self.assertEqual(refused.json()['results'][0]['error'], 'employee_not_allowed')

"""SSTS1 (SPEC 5.1 §5): dezelfde testvectoren als tests/fixtures/ssts1_vectors.json aan de kant van de dienst."""
from odoo.tests import BaseCase, tagged

from .. import ssts
from .common import VECTORS


@tagged('post_install', '-at_install', 'ss_timesheets')
class TestSign(BaseCase):

    def test_vectors(self):
        secret = ssts.secret_bytes(VECTORS['secret_b64'])
        for v in VECTORS['vectors']:
            body = v['body'].encode()
            self.assertEqual(ssts.canonical(v['direction'], v['path'], v['timestamp'], v['nonce'], body), v['canonical'])
            self.assertEqual(ssts.signature(secret, v['direction'], v['path'], v['timestamp'], v['nonce'], body), v['signature'])
            hdrs = {'X-SS-Timestamp': v['timestamp'], 'X-SS-Nonce': v['nonce'], 'X-SS-Signature': v['signature']}
            self.assertTrue(ssts.verify(secret, v['direction'], v['path'], body, hdrs, now=int(v['timestamp'])))

    def _refused(self, code, **kw):
        v = VECTORS['vectors'][0]
        secret = ssts.secret_bytes(VECTORS['secret_b64'])
        hdrs = {'X-SS-Timestamp': v['timestamp'], 'X-SS-Nonce': v['nonce'], 'X-SS-Signature': v['signature'], **kw.pop('hdrs', {})}
        args = {'secret': secret, 'direction': v['direction'], 'path': v['path'], 'body': v['body'].encode(),
                'now': int(v['timestamp']), **kw}
        with self.assertRaises(ssts.SignatureError) as cm:
            ssts.verify(args['secret'], args['direction'], args['path'], args['body'], hdrs, seen_nonce=args.get('seen_nonce'),
                        now=args['now'])
        self.assertEqual(cm.exception.code, code)

    def test_refusals(self):
        ts = int(VECTORS['vectors'][0]['timestamp'])
        self._refused('clock_skew', now=ts + 301)
        self._refused('clock_skew', now=ts - 301)
        self._refused('bad_signature', hdrs={'X-SS-Signature': '0' * 64})
        self._refused('bad_signature', hdrs={'X-SS-Nonce': ''})
        self._refused('bad_signature', body=b'{"a":1}')
        self._refused('bad_signature', path='/ss_timesheets/v1/companies')
        self._refused('bad_signature', direction=ssts.TO_APP)
        self._refused('bad_signature', secret=b'x' * 32)
        self._refused('replay', seen_nonce=lambda nonce: True)
        # 299 s af is nog goed
        v = VECTORS['vectors'][0]
        hdrs = {'X-SS-Timestamp': v['timestamp'], 'X-SS-Nonce': v['nonce'], 'X-SS-Signature': v['signature']}
        self.assertTrue(ssts.verify(ssts.secret_bytes(VECTORS['secret_b64']), v['direction'], v['path'], v['body'].encode(), hdrs,
                                    now=ts + 299))

    def test_signature_before_nonce(self):
        """I7 (beveiligingsreview 8.3): een foute handtekening of tijd komt nooit aan de nonce-opslag."""
        seen = []
        self._refused('bad_signature', hdrs={'X-SS-Signature': '0' * 64}, seen_nonce=seen.append)
        self._refused('bad_signature', body=b'{"a":1}', seen_nonce=seen.append)
        self._refused('clock_skew', now=int(VECTORS['vectors'][0]['timestamp']) + 301, seen_nonce=seen.append)
        self.assertEqual(seen, [])

    def test_headers_roundtrip(self):
        secret = b's' * 32
        hdrs = ssts.headers(secret, 'k_1', ssts.TO_APP, '/api/module/v1/ping', b'{}', now=1000)
        self.assertEqual(hdrs['X-SS-Key-Id'], 'k_1')
        self.assertTrue(ssts.verify(secret, ssts.TO_APP, '/api/module/v1/ping', b'{}', hdrs, now=1000))

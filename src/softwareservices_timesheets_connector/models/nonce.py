"""Gezien nonces per key_id (SPEC 5.1 §5): een unieke index, zodat twee gelijke verzoeken tegelijk er hoogstens één doorlaten,
ook met meerdere Odoo-workers. Een nonce wordt pas bewaard na een geldige handtekening (I7); elke nieuwe nonce ruimt de nonces
ouder dan 15 minuten op (de cron doet dat ook, maar draait niet overal), zodat de tabel nooit groeit."""
from datetime import timedelta

from odoo import api, fields, models

KEEP_MINUTES = 15


class Nonce(models.Model):
    _name = 'ss_timesheets.nonce'
    _description = 'Used nonce ({{PRODUCT_NAME}})'
    _log_access = False

    key_id = fields.Char(required=True)
    nonce = fields.Char(required=True)
    seen_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)

    _sql_constraints = [('key_nonce_unique', 'unique(key_id, nonce)', 'This nonce has already been used.')]

    @api.model
    def _seen(self, key_id, nonce):
        """Store the nonce; True if it was already there. In the request's own transaction: a concurrent request with the
        same nonce waits on the unique index and then sees it. Nonces older than KEEP_MINUTES go in the same breath (the clock
        window is 5 minutes, so an old nonce can never be replayed)."""
        self.env.cr.execute("DELETE FROM ss_timesheets_nonce WHERE seen_at < %s",
                            (fields.Datetime.now() - timedelta(minutes=KEEP_MINUTES),))
        self.env.cr.execute("""INSERT INTO ss_timesheets_nonce (key_id, nonce, seen_at) VALUES (%s, %s, now() at time zone 'UTC')
                               ON CONFLICT (key_id, nonce) DO NOTHING RETURNING id""", (key_id, nonce))
        return self.env.cr.fetchone() is None

    @api.model
    def _cleanup(self):
        self.env.cr.execute("DELETE FROM ss_timesheets_nonce WHERE seen_at < %s",
                            (fields.Datetime.now() - timedelta(minutes=KEEP_MINUTES),))

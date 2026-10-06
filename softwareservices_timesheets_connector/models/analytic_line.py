from odoo import fields, models


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    # het id van de regel in TimeViking: maakt /timesheets/create idempotent (SPEC 5.1 §7)
    ss_client_ref = fields.Char('Referentie TimeViking', index=True, copy=False, readonly=True)

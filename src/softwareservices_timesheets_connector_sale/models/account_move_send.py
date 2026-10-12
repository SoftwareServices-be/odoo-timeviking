from odoo import api, models


class AccountMoveSend(models.AbstractModel):
    _inherit = 'account.move.send'

    @api.model
    def _get_invoice_extra_attachments(self, move):
        """The timesheet report goes with the invoice in the send wizard and the email."""
        return super()._get_invoice_extra_attachments(move) | move.ss_timesheet_report_id

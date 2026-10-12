import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

REPORT = 'softwareservices_timesheets_connector_sale.action_report_invoice_timesheet'


class AccountMove(models.Model):
    _inherit = 'account.move'

    ss_timesheet_report_id = fields.Many2one('ir.attachment', string='Timesheet report', copy=False, readonly=True)

    def _ss_timesheet_lines(self):
        """The timesheets this invoice bills (sale_timesheet: timesheet_ids; the hours from TimeViking are normal
        timesheets). With sudo: who may see the invoice may see the time it bills."""
        self.ensure_one()
        return self.sudo().timesheet_ids

    def _ss_timesheet_report_name(self):
        return _('Timesheets %s.pdf', (self.name or '').replace('/', '_'))

    def _ss_attach_timesheet_report(self):
        """(Re)create the PDF attachment of each invoice with timesheets. Returns the new attachments."""
        attachments = self.env['ir.attachment']
        for move in self:
            if not move._ss_timesheet_lines():
                continue
            pdf, _kind = self.env['ir.actions.report'].sudo()._render_qweb_pdf(REPORT, move.ids)
            old = move.ss_timesheet_report_id
            attachment = self.env['ir.attachment'].sudo().create({
                'name': move._ss_timesheet_report_name(),
                'type': 'binary',
                'raw': pdf,
                'mimetype': 'application/pdf',
                'res_model': 'account.move',
                'res_id': move.id,
            })
            move.sudo().ss_timesheet_report_id = attachment
            old.sudo().unlink()
            attachments |= attachment
        return attachments

    def action_ss_attach_timesheet_report(self):
        moves = self.filtered(lambda m: m._ss_timesheet_lines())
        if not moves:
            raise UserError(_('This invoice has no timesheets.'))
        for move, attachment in zip(moves, moves._ss_attach_timesheet_report()):
            move.message_post(body=_('Timesheet report attached.'), attachment_ids=attachment.ids)
        return True

    @api.model
    def _ss_auto_attach(self):
        return self.env['ss_timesheets.connection'].get_connection().invoice_timesheet_report

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        sales = posted.filtered(lambda m: m.is_sale_document(include_receipts=False))
        if sales and self._ss_auto_attach():
            for move in sales.filtered(lambda m: m._ss_timesheet_lines()):
                try:
                    with self.env.cr.savepoint():
                        move._ss_attach_timesheet_report()
                except Exception:                                  # never block an invoice for a PDF
                    _logger.exception('Timesheet report for invoice %s could not be attached', move.id)
        return posted

from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

REPORT = 'softwareservices_timesheets_connector_sale.report_invoice_timesheet'


@tagged('post_install', '-at_install')
class TestInvoiceTimesheetReport(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)        # since 19 the accounting test user may not create employees or projects
        cls.company = cls.company_data['company']
        cls.employee = env['hr.employee'].create({'name': 'Ivy Invoicer', 'company_id': cls.company.id})
        cls.project = env['project.project'].create({'name': 'Support contract', 'partner_id': cls.partner_a.id,
                                                     'allow_timesheets': True, 'company_id': cls.company.id})
        cls.task = env['project.task'].create({'name': 'Helpdesk', 'project_id': cls.project.id})
        Line = env['account.analytic.line']
        cls.lines = Line.create([
            {'employee_id': cls.employee.id, 'project_id': cls.project.id, 'task_id': cls.task.id,
             'date': date(2026, 9, 7), 'unit_amount': 1.5, 'name': 'Printer issue'},
            {'employee_id': cls.employee.id, 'project_id': cls.project.id, 'task_id': cls.task.id,
             'date': date(2026, 9, 8), 'unit_amount': 2.0, 'name': 'Mailbox migration', 'ss_client_ref': 'tv-1'},
        ])
        cls.other_line = Line.create({'employee_id': cls.employee.id, 'project_id': cls.project.id,
                                      'date': date(2026, 9, 9), 'unit_amount': 9.0, 'name': 'Not invoiced'})
        cls.connection = env['ss_timesheets.connection'].get_connection()

    def make_invoice(self, post=False):
        invoice = self.init_invoice('out_invoice', partner=self.partner_a, invoice_date=date(2026, 9, 30),
                                    products=self.product_a)
        # what sale_timesheet does when it invoices time (timesheet_invoice_id; reinvoice_move_id since 20)
        inverse = self.env['account.move']._fields['timesheet_ids'].inverse_name
        self.lines.sudo().write({inverse: invoice.id})
        if post:
            invoice.action_post()
        return invoice

    def test_html(self):
        invoice = self.make_invoice()
        html = self.env['ir.actions.report']._render_qweb_html(REPORT, invoice.ids)[0].decode()
        self.assertIn('Printer issue', html)
        self.assertIn('Mailbox migration', html)
        self.assertIn('3:30', html)
        self.assertIn('Logged in TimeViking', html)
        self.assertNotIn('Not invoiced', html)

    def test_attach_button(self):
        invoice = self.make_invoice()
        invoice.action_ss_attach_timesheet_report()
        attachment = invoice.ss_timesheet_report_id
        self.assertTrue(attachment)
        self.assertEqual((attachment.res_model, attachment.res_id), ('account.move', invoice.id))
        self.assertTrue(attachment.name.startswith('Timesheets'))
        invoice.action_ss_attach_timesheet_report()                # again: replaced, not duplicated
        self.assertNotEqual(invoice.ss_timesheet_report_id, attachment)
        self.assertFalse(attachment.exists())

    def test_no_timesheets(self):
        invoice = self.init_invoice('out_invoice', partner=self.partner_a, invoice_date=date(2026, 9, 30),
                                    products=self.product_a)
        with self.assertRaises(UserError):
            invoice.action_ss_attach_timesheet_report()
        invoice.action_post()
        self.assertFalse(invoice.ss_timesheet_report_id)

    def test_attach_on_post_by_default(self):
        self.assertTrue(self.connection.invoice_timesheet_report)
        invoice = self.make_invoice(post=True)
        self.assertTrue(invoice.ss_timesheet_report_id)
        extra = self.env['account.move.send']._get_invoice_extra_attachments(invoice)
        self.assertIn(invoice.ss_timesheet_report_id, extra)

    def test_setting_off(self):
        self.connection.invoice_timesheet_report = False
        invoice = self.make_invoice(post=True)
        self.assertFalse(invoice.ss_timesheet_report_id)

    def test_pdf(self):
        Report = self.env['ir.actions.report']
        state = Report.get_wkhtmltopdf_state() if hasattr(Report, 'get_wkhtmltopdf_state') \
            else Report.get_pdf_engine_state(Report._get_pdf_engine())
        if state != 'ok':
            self.skipTest('no PDF engine')
        # the assets come from report.url; a closed port keeps wkhtmltopdf from waiting on this test's own server
        self.env['ir.config_parameter'].sudo().set_str('report.url', 'http://127.0.0.1:9')
        invoice = self.make_invoice()
        pdf, kind = Report.with_context(force_report_rendering=True)._render_qweb_pdf(REPORT, invoice.ids)
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_translated(self):
        module = 'softwareservices_timesheets_connector_sale'
        self.env['res.lang']._activate_lang('nl_BE')
        self.env['ir.module.module']._load_module_terms([module], ['nl_BE'], overwrite=True)
        env = self.env(context={'lang': 'nl_BE'})
        arch = env['account.move'].get_view(self.env.ref('account.view_move_form').id)['arch']
        self.assertIn('Urenstaat bijvoegen', arch)

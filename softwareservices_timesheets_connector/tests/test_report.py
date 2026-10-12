"""The timesheet report: per employee, with the TimeViking mention, from the wizard and the Print menu."""
from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConnectorCase

MODULE = 'softwareservices_timesheets_connector'
REPORT = f'{MODULE}.report_timesheet'


@tagged('post_install', '-at_install')
class TestTimesheetReport(ConnectorCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Line = cls.env['account.analytic.line']
        cls.l1 = Line.create({'employee_id': cls.emp_an.id, 'project_id': cls.project.id, 'task_id': cls.task.id,
                              'date': date(2026, 9, 21), 'unit_amount': 1.5, 'name': 'Kick-off'})
        cls.l2 = Line.create({'employee_id': cls.emp_an.id, 'project_id': cls.project.id, 'date': date(2026, 9, 22),
                              'unit_amount': 2.25, 'name': 'Wireframes', 'ss_client_ref': 'tv-42'})
        cls.l3 = Line.create({'employee_id': cls.emp_bo.id, 'project_id': cls.project.id, 'date': date(2026, 9, 23),
                              'unit_amount': 4.0, 'name': 'Review'})
        cls.l_out = Line.create({'employee_id': cls.emp_bo.id, 'project_id': cls.project.id, 'date': date(2026, 10, 5),
                                 'unit_amount': 1.0, 'name': 'Out of period'})

    def test_sections(self):
        sections = (self.l1 | self.l2 | self.l3)._ss_report_sections()
        self.assertEqual([s['employee'] for s in sections], ['SS An', 'SS Bo'])
        self.assertEqual((sections[0]['hours_str'], sections[0]['via_count'], sections[0]['count']), ('3:45', 1, 2))
        self.assertEqual(sections[1]['hours_str'], '4:00')

    def test_wizard_and_html(self):
        wizard = self.env['ss_timesheets.report_wizard'].create({'date_from': date(2026, 9, 1), 'date_to': date(2026, 9, 30)})
        lines = wizard._lines()
        self.assertTrue((self.l1 | self.l2 | self.l3) <= lines)
        self.assertNotIn(self.l_out, lines)
        self.env.company.external_report_layout_id = self.env.ref('web.external_layout_standard')
        action = wizard.action_print()
        self.assertEqual(action['report_name'], REPORT)
        html = self.env['ir.actions.report']._render_qweb_html(REPORT, (self.l1 | self.l2 | self.l3).ids)[0].decode()
        for text in ('SS An', 'SS Bo', 'Kick-off', 'Wireframes', '3:45', 'Logged in TimeViking', 'https://timeviking.com'):
            self.assertIn(text, html)

    def test_wizard_filters_and_errors(self):
        Wizard = self.env['ss_timesheets.report_wizard']
        wizard = Wizard.create({'date_from': date(2026, 9, 1), 'date_to': date(2026, 9, 30),
                                'employee_ids': [(6, 0, self.emp_bo.ids)]})
        self.assertEqual(wizard._lines().mapped('employee_id'), self.emp_bo)
        with self.assertRaises(UserError):
            Wizard.create({'date_from': date(2026, 9, 30), 'date_to': date(2026, 9, 1)}).action_print()
        with self.assertRaises(UserError):
            Wizard.create({'date_from': date(2001, 1, 1), 'date_to': date(2001, 1, 2)}).action_print()

    def test_print_menu_and_menu(self):
        action = self.env.ref(f'{MODULE}.action_report_timesheet')
        self.assertEqual(action.binding_model_id.model, 'account.analytic.line')
        self.assertEqual(self.env.ref(f'{MODULE}.menu_report_wizard').parent_id, self.env.ref('hr_timesheet.menu_timesheets_reports'))

    def test_plain_user_gets_own_lines_only(self):
        user = self.user_an
        user.write({'groups_id': [(4, self.env.ref('hr_timesheet.group_hr_timesheet_user').id)]})
        self.project.privacy_visibility = 'employees'          # the 'followers' project of ConnectorCase hides it
        wizard = self.env['ss_timesheets.report_wizard'].with_user(user).create(
            {'date_from': date(2026, 9, 1), 'date_to': date(2026, 9, 30)})
        self.assertEqual(wizard._lines().mapped('employee_id'), self.emp_an)

    def test_pdf(self):
        Report = self.env['ir.actions.report']
        state = Report.get_wkhtmltopdf_state() if hasattr(Report, 'get_wkhtmltopdf_state') \
            else Report.get_pdf_engine_state(Report._get_pdf_engine())
        if state != 'ok':
            self.skipTest('no PDF engine')
        # the assets come from report.url; a closed port keeps wkhtmltopdf from waiting on this test's own server
        self.env['ir.config_parameter'].sudo().set_param('report.url', 'http://127.0.0.1:9')
        pdf, kind = Report.with_context(force_report_rendering=True)._render_qweb_pdf(REPORT, (self.l1 | self.l3).ids)
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_translated(self):
        self.env['res.lang']._activate_lang('nl_BE')
        self.env['ir.module.module']._load_module_terms([MODULE], ['nl_BE'], overwrite=True)
        env = self.env(context={'lang': 'nl_BE'})
        self.assertEqual(env.ref(f'{MODULE}.menu_report_wizard').name, 'Urenstaat (PDF)')
        html = env['ir.actions.report'].with_context(lang='nl_BE')._render_qweb_html(REPORT, self.l2.ids)[0].decode()
        self.assertIn('Geregistreerd in TimeViking', html)

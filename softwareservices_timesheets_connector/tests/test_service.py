"""De service-laag (SPEC 5.1 §6, §7, §14 punten 11 en 13–15): wat de dienst in Odoo mag, en niets anders."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import ConnectorCase


@tagged('post_install', '-at_install', 'ss_timesheets')
class TestInstall(ConnectorCase):

    def test_group_user_and_models(self):
        """§14.11: groep, technische gebruiker (inactief, geen wachtwoord, geen Settings) en de drie modellen."""
        user = self.connector
        self.assertFalse(user.active)
        self.assertFalse(user.share)
        self.env.cr.execute('SELECT password FROM res_users WHERE id = %s', (user.id,))
        self.assertFalse(self.env.cr.fetchone()[0])
        self.assertTrue(user.has_group('softwareservices_timesheets_connector.group_connector'))
        self.assertTrue(user.has_group('project.group_project_user'))
        self.assertTrue(user.has_group('hr_timesheet.group_hr_timesheet_approver'))
        self.assertFalse(user.has_group('base.group_system'))
        self.assertFalse(user.has_group('base.group_erp_manager'))
        self.assertFalse(user.has_group('hr_timesheet.group_timesheet_manager'))
        for model in ('ss_timesheets.connection', 'ss_timesheets.nonce', 'ss_timesheets.employee_link'):
            self.assertIn(model, self.env)
        self.assertIn('ss_client_ref', self.env['account.analytic.line']._fields)

    def test_secret_only_for_settings(self):
        """§12/§14.17: het geheim is alleen leesbaar voor base.group_system."""
        officer = self.env['res.users'].create({
            'name': 'SS Koppelingslezer', 'login': 'ss_reader@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('softwareservices_timesheets_connector.group_connector').id])]})
        conn = self.connection.with_user(officer)
        self.assertEqual(conn.service_url, 'https://dienst.example.com')
        with self.assertRaises(AccessError):
            conn.read(['secret'])
        with self.assertRaises(AccessError):
            conn.read(['key_id'])
        plain = self.env['res.users'].create({'name': 'SS Gewoon', 'login': 'ss_plain@example.com',
                                              'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.connection.with_user(plain).read(['service_url'])
        self.assertEqual(self.connection.sudo().secret, self.connection.secret)


@tagged('post_install', '-at_install', 'ss_timesheets')
class TestRead(ConnectorCase):

    def test_ping(self):
        status, data = self.run_endpoint('ping', {})
        self.assertEqual(status, 200)
        self.assertTrue(data['server_version'].startswith('19'))
        self.assertEqual(data['module_version'][:5], '19.0.')
        self.assertEqual(data['companies'], [{'id': self.company_a.id, 'name': self.company_a.name}])
        self.assertIn('ss_client_ref', data['fields']['account.analytic.line'])
        self.assertIn('work_email', data['fields']['hr.employee'])

    def test_companies_employees_projects_tasks_only_for_the_linked_company(self):
        """'Ander bedrijf': alles van bedrijf B blijft onzichtbaar, ook als de dienst erom vraagt."""
        self.assertEqual([c['id'] for c in self.run_endpoint('companies', {})[1]['items']], [self.company_a.id])
        employees = self.run_endpoint('employees', {'company_ids': [self.company_a.id, self.company_b.id]})[1]['items']
        ids = {e['id'] for e in employees}
        self.assertTrue({self.emp_an.id, self.emp_bo.id, self.emp_cas.id} <= ids)
        self.assertNotIn(self.emp_b.id, ids)
        an = next(e for e in employees if e['id'] == self.emp_an.id)
        self.assertEqual((an['user_login'], an['work_email'], an['company_id'][0]), ('ss_an@example.com', 'ss_an@example.com', self.company_a.id))
        self.assertIsInstance(an['write_date'], str)
        self.assertEqual(self.run_endpoint('employees', {'company_ids': [self.company_b.id]})[1]['items'], [])
        projects = {p['id'] for p in self.run_endpoint('projects', {'company_ids': []})[1]['items']}
        self.assertIn(self.project.id, projects)                                 # ook een privéproject (followers)
        self.assertNotIn(self.project_no_ts.id, projects)
        self.assertNotIn(self.project_b.id, projects)
        tasks = self.run_endpoint('tasks', {'project_ids': [self.project.id, self.project_b.id]})[1]['items']
        self.assertEqual([t['id'] for t in tasks], [self.task.id])

    def test_since_and_paging(self):
        status, data = self.run_endpoint('projects', {'limit': 1, 'offset': 0})
        self.assertEqual(len(data['items']), 1)
        later = fields.Datetime.to_string(fields.Datetime.now() + timedelta(days=1))
        self.assertEqual(self.run_endpoint('projects', {'since': later})[1]['items'], [])
        self.assertEqual(self.run_endpoint('projects', {'limit': 'x'})[0], 400)

    def test_unknown_endpoint(self):
        self.assertEqual(self.run_endpoint('partners', {})[0], 404)
        self.assertEqual(self.run_endpoint('call', {'model': 'res.partner'})[0], 404)


@tagged('post_install', '-at_install', 'ss_timesheets')
class TestTimesheets(ConnectorCase):

    def create(self, **extra):
        status, data = self.run_endpoint('timesheets/create', {'lines': [self.line_body(**extra)]})
        self.assertEqual(status, 200)
        return data['results'][0]

    def test_create_and_duplicate(self):
        """§14.13: regel met de werknemer, user_id van de werknemer, create_uid = connector, ss_client_ref gezet."""
        result = self.create()
        self.assertTrue(result['id'], result)
        line = self.env['account.analytic.line'].browse(result['id'])
        self.assertEqual(line.employee_id, self.emp_an)
        self.assertEqual(line.user_id, self.user_an)
        self.assertEqual(line.create_uid, self.connector)
        self.assertEqual((line.ss_client_ref, line.unit_amount, line.task_id, line.project_id), ('1001', 1.5, self.task, self.project))
        self.assertEqual(result['write_date'], fields.Datetime.to_string(line.write_date))
        again = self.create()
        self.assertEqual((again['error'], again['id']), ('duplicate', line.id))
        self.assertEqual(self.env['account.analytic.line'].search_count([('ss_client_ref', '=', '1001')]), 1)
        # zonder gebruiker: user_id blijft leeg
        no_user = self.env['ss_timesheets.employee_link'].create({'employee_id': self.emp_cas.id})
        res = self.create(client_ref='1002', employee_id=self.emp_cas.id)
        self.assertFalse(self.env['account.analytic.line'].browse(res['id']).user_id)
        no_user.unlink()

    def test_refusals_per_line(self):
        """§14.13: niet toegelaten of niet gekoppeld → employee_not_allowed, geen regel; de rest van de batch gaat door."""
        lines = [self.line_body(client_ref='a', employee_id=self.emp_bo.id),          # allowed = False
                 self.line_body(client_ref='b', employee_id=self.emp_cas.id),         # niet gekoppeld
                 self.line_body(client_ref='c', employee_id=self.emp_b.id, project_id=self.project_b.id, task_id=self.task_b.id,
                                company_id=self.company_b.id),                         # ander bedrijf
                 self.line_body(client_ref='d', project_id=self.project_no_ts.id, task_id=False),
                 self.line_body(client_ref='e', date='21-09-2026'),
                 self.line_body(client_ref='f', hours=-1),
                 self.line_body(client_ref='g')]
        results = self.run_endpoint('timesheets/create', {'lines': lines})[1]['results']
        self.assertEqual([r.get('error') for r in results],
                         ['employee_not_allowed', 'employee_not_allowed', 'employee_not_allowed', 'project_not_allowed', 'validation',
                          'validation', None])
        self.assertEqual(self.env['account.analytic.line'].search([('ss_client_ref', 'in', list('abcdefg'))]).mapped('ss_client_ref'), ['g'])

    def test_write(self):
        """§14.14: normaal → nieuwe write_date; verouderde expected_write_date → conflict met current; onbekend veld geweigerd."""
        line = self.env['account.analytic.line'].browse(self.create()['id'])
        old = fields.Datetime.to_string(line.write_date)
        res = self.run_endpoint('timesheets/write', {'lines': [{'id': line.id, 'expected_write_date': old,
                                                                 'values': {'hours': 2.0, 'name': 'Anders'}}]})[1]['results'][0]
        self.assertNotIn('error', res)
        self.assertEqual((line.unit_amount, line.name), (2.0, 'Anders'))
        self.assertEqual(line.write_uid, self.connector)
        res = self.run_endpoint('timesheets/write', {'lines': [{'id': line.id, 'expected_write_date': '2000-01-01 00:00:00',
                                                                 'values': {'hours': 3.0}}]})[1]['results'][0]
        self.assertEqual(res['error'], 'conflict')
        self.assertEqual(res['current']['unit_amount'], 2.0)
        self.assertEqual(line.unit_amount, 2.0)
        res = self.run_endpoint('timesheets/write', {'lines': [{'id': line.id, 'values': {'user_id': 1}}]})[1]['results'][0]
        self.assertEqual(res['error'], 'field_not_allowed')
        res = self.run_endpoint('timesheets/write', {'lines': [{'id': line.id, 'values': {'employee_id': self.emp_bo.id}}]})[1]['results'][0]
        self.assertEqual(res['error'], 'employee_not_allowed')
        res = self.run_endpoint('timesheets/write', {'lines': [{'id': 999999999, 'values': {'hours': 1}}]})[1]['results'][0]
        self.assertEqual(res['error'], 'not_found')

    def test_write_validated(self):
        """§14.14: een gevalideerde regel → validated (validated bestaat alleen met Enterprise timesheet_grid)."""
        Line = self.env['account.analytic.line']
        if 'validated' not in Line._fields:
            self.skipTest('geen validated-veld (Community zonder timesheet_grid)')
        line = Line.browse(self.create()['id'])
        line.sudo().write({'validated': True})
        res = self.run_endpoint('timesheets/write', {'lines': [{'id': line.id, 'values': {'hours': 3.0}}]})[1]['results'][0]
        self.assertEqual(res['error'], 'validated')

    def test_delete_and_scope(self):
        """§14.15: een regel van een niet-gekoppelde werknemer → error, de regel bestaat nog."""
        mine = self.create()['id']
        other = self.env['account.analytic.line'].create({'employee_id': self.emp_cas.id, 'project_id': self.project.id,
                                                          'name': 'In Odoo zelf', 'unit_amount': 1, 'date': '2026-09-21'})
        results = self.run_endpoint('timesheets/delete', {'ids': [other.id, mine, 999999999]})[1]['results']
        self.assertEqual([r.get('error') for r in results], ['employee_not_allowed', None, 'not_found'])
        self.assertTrue(other.exists())
        self.assertFalse(self.env['account.analytic.line'].browse(mine).exists())

    def test_search_and_exists_only_linked_employees(self):
        mine = self.create()['id']
        other = self.env['account.analytic.line'].create({'employee_id': self.emp_cas.id, 'project_id': self.project.id,
                                                          'name': 'In Odoo zelf', 'unit_amount': 1, 'date': '2026-09-21'})
        items = self.run_endpoint('timesheets/search', {'employee_ids': [self.emp_an.id, self.emp_cas.id], 'date_from': '2026-09-01',
                                                        'date_to': '2026-09-30'})[1]['items']
        self.assertEqual([i['id'] for i in items], [mine])
        self.assertEqual(items[0]['ss_client_ref'], '1001')
        self.assertEqual(self.run_endpoint('timesheets/search', {'ids': [mine, other.id]})[1]['items'][0]['id'], mine)
        self.assertEqual(self.run_endpoint('timesheets/exists', {'ids': [mine, other.id, 999999999]})[1], {'existing': [mine]})
        self.assertEqual(self.run_endpoint('timesheets/search', {'employee_ids': [self.emp_an.id], 'date_from': '2026-10-01'})[1]['items'], [])

    def test_too_many_lines(self):
        status, data = self.run_endpoint('timesheets/create', {'lines': [self.line_body()] * 201})
        self.assertEqual((status, data['error']), (413, 'too_many_lines'))


@tagged('post_install', '-at_install', 'ss_timesheets')
class TestLinksAndKeys(ConnectorCase):

    def test_links_full_list(self):
        """§6.1: de volledige lijst; nieuwe toegelaten, de keuze van de beheerder blijft, ontbrekende weg, ander bedrijf genegeerd."""
        status, data = self.run_endpoint('links', {'links': [{'employee_id': self.emp_bo.id, 'external_user': '2 bo'},
                                                             {'employee_id': self.emp_cas.id, 'external_user': '4 cas'},
                                                             {'employee_id': self.emp_b.id, 'external_user': '3 dirk'}]})
        self.assertEqual(status, 200)
        self.assertEqual(sorted((i['employee_id'], i['allowed']) for i in data['items']),
                         sorted([(self.emp_bo.id, False), (self.emp_cas.id, True)]))
        self.assertEqual(data['unknown'], [self.emp_b.id])
        self.assertFalse(self.link_an.exists())
        self.assertEqual(self.link_bo.external_user, '2 bo')

    def test_rotate_and_grace_period(self):
        """§8: nieuw geheim; het oude werkt nog 10 minuten, daarna niet meer."""
        from .common import KEY_ID
        status, data = self.run_endpoint('rotate', {})
        self.assertEqual(status, 200)
        Conn = self.env['ss_timesheets.connection']
        self.assertTrue(Conn._find_secret(data['key_id'])[1])
        self.assertTrue(Conn._find_secret(KEY_ID)[1])
        self.connection.prev_valid_until = fields.Datetime.now() - timedelta(seconds=1)
        self.assertEqual(Conn._find_secret(KEY_ID), (None, None))
        Conn._cron_cleanup()
        self.assertFalse(self.connection.prev_secret)
        self.assertTrue(Conn._find_secret(data['key_id'])[1])

    def test_revoke(self):
        from .common import KEY_ID
        self.assertEqual(self.run_endpoint('revoke', {}), (200, {'ok': True}))
        self.assertEqual(self.connection.state, 'unpaired')
        self.assertFalse(self.connection.secret)
        self.assertEqual(self.env['ss_timesheets.connection']._find_secret(KEY_ID), (None, None))

    def test_nonce_store_and_cleanup(self):
        Nonce = self.env['ss_timesheets.nonce']
        self.assertFalse(Nonce._seen('k_x', 'a' * 32))
        self.assertTrue(Nonce._seen('k_x', 'a' * 32))
        self.assertFalse(Nonce._seen('k_y', 'a' * 32))                           # per key_id
        self.env.cr.execute("UPDATE ss_timesheets_nonce SET seen_at = seen_at - interval '16 minutes' WHERE key_id = 'k_x'")
        Nonce._cleanup()
        self.assertFalse(Nonce._seen('k_x', 'a' * 32))

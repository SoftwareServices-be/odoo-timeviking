"""Rooster en verlof via de module (SPEC 9 §8.3, taak 9.5): dezelfde grenzen als de uren (bedrijven van de koppeling,
gekoppelde werknemers; schrijven alleen voor toegelaten werknemers), de rol Time Off: Officer van de technische gebruiker,
en Odoo's eigen goedkeuringsregels. Versie-onafhankelijk: de test kijkt welke modellen en velden deze Odoo heeft."""
from odoo.tests import tagged

from .common import ConnectorCase

OFFICER = 'hr_holidays.group_hr_holidays_user'
DAY = '2027-03-01'                       # een maandag, ver na de contractdatum hieronder


@tagged('post_install', '-at_install', 'ss_timesheets')
class TestHr(ConnectorCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.has_leave = 'hr.leave' in env
        Employee = env['hr.employee']
        cls.calendar_a = cls.emp_an.resource_calendar_id or cls.company_a.resource_calendar_id
        if 'contract_date_start' in Employee._fields:            # 19/20: buiten een contract is elke dag vrij
            (cls.emp_an | cls.emp_bo).write({'contract_date_start': '2026-01-01'})
        if cls.has_leave:
            cls.type_model = 'hr.leave.type' if 'hr.leave.type' in env else 'hr.work.entry.type'
            cls.type_field = 'holiday_status_id' if 'holiday_status_id' in env['hr.leave']._fields else 'work_entry_type_id'
            Type = env[cls.type_model]
            vals = {'name': 'SS Klein verlet', 'request_unit': 'day', 'leave_validation_type': 'hr'}
            vals['requires_allocation'] = 'no' if Type._fields['requires_allocation'].type == 'selection' else False
            if 'time_off_selectable' in Type._fields:             # 20: hr.work.entry.type
                vals.update({'code': 'SSKV9', 'time_off_selectable': True, 'count_as': 'absence'})
            else:
                vals['company_id'] = cls.company_a.id
            cls.leave_type = Type.create(vals)

    def setUp(self):
        super().setUp()
        self.env['ss_timesheets.connection']._ensure_connector()
        # Odoo's meldingen bij verlof hebben de technische gebruiker als afzender; zonder e-mailadres en zonder
        # mail.default.from weigert Odoo ze te versturen (zie de installatiehandleiding)
        self.connector.partner_id.email = 'koppeling@example.com'

    def ok(self, endpoint, body):
        status, data = self.run_endpoint(endpoint, body)
        self.assertEqual(status, 200, data)
        return data

    def items(self, endpoint, body):
        """All pages (500 per page; Odoo 20 has hundreds of work entry types), like ModuleTransport._pages."""
        out, offset = [], 0
        while True:
            page = self.ok(endpoint, {**body, 'offset': offset})['items']
            out += page
            if len(page) < 500:
                return out
            offset += 500

    def need_leave(self):
        if not self.has_leave:
            self.skipTest('Time Off (hr_holidays) is niet geïnstalleerd')

    def leave_vals(self, employee, day_from=DAY, day_to=DAY, **extra):
        return {'employee_id': employee.id, self.type_field: self.leave_type.id, 'request_date_from': day_from,
                'request_date_to': day_to, **extra}

    # ── rechten en ping ──

    def test_connector_rights_and_ping(self):
        connector = self.connector
        self.assertTrue(connector.has_group('hr.group_hr_user'))
        if self.has_leave:
            self.assertTrue(connector.has_group(OFFICER))
            self.assertFalse(connector.has_group('hr_holidays.group_hr_holidays_manager'))
        self.assertFalse(connector.has_group('base.group_system'))
        self.assertFalse(connector.active)
        self.assertTrue(set(self.env['res.company'].search([]).ids) <= set(connector.company_ids.ids))
        data = self.ok('ping', {})
        self.assertIn('leave/create', data['endpoints'])
        self.assertIn('calendars', data['endpoints'])
        self.assertIn('resource.calendar', data['fields'])
        self.assertIn('project.group_project_user', data['groups'])
        if self.has_leave:
            self.assertIn(OFFICER, data['groups'])
            self.assertIn(self.type_field, data['fields']['hr.leave'])
            self.assertFalse({'name', 'private_name'} & set(data['fields']['hr.leave']))      # nooit de reden
            self.assertIn('holiday_id', data['fields']['account.analytic.line'])

    # ── rooster ──

    def test_calendars_only_of_the_companies_or_linked_employees(self):
        Calendar = self.env['resource.calendar']
        cal_b = Calendar.create({'name': 'SS Rooster B', 'company_id': self.company_b.id})
        cal_b_cas = Calendar.create({'name': 'SS Rooster B voor Cas', 'company_id': self.company_b.id})
        self.emp_cas.resource_calendar_id = cal_b_cas
        ids = {c['id'] for c in self.ok('calendars', {'company_ids': [self.company_a.id, self.company_b.id]})['items']}
        self.assertIn(self.calendar_a.id, ids)
        self.assertFalse({cal_b.id, cal_b_cas.id} & ids)
        asked = [cal_b.id, cal_b_cas.id, self.calendar_a.id]
        self.assertEqual([c['id'] for c in self.ok('calendars', {'ids': asked})['items']], [self.calendar_a.id])
        self.env['ss_timesheets.employee_link'].create({'employee_id': self.emp_cas.id})        # Cas gekoppeld: zijn rooster mag
        self.assertEqual({c['id'] for c in self.ok('calendars', {'ids': asked})['items']}, {self.calendar_a.id, cal_b_cas.id})
        atts = self.ok('calendars/attendances', {'calendar_ids': [self.calendar_a.id, cal_b.id]})['items']
        self.assertTrue(atts)
        self.assertEqual({a['calendar_id'][0] for a in atts}, {self.calendar_a.id})
        rows = self.ok('calendars/employees', {'employee_ids': [self.emp_an.id, self.emp_b.id, self.emp_cas.id]})['items']
        self.assertEqual({r['id'] for r in rows}, {self.emp_an.id, self.emp_cas.id})          # emp_b: ander bedrijf

    def test_versions_contracts_and_dates_per_version(self):
        if 'hr.version' in self.env:
            rows = self.ok('calendars/versions', {'employee_ids': [self.emp_an.id, self.emp_cas.id]})['items']
            self.assertTrue(rows)
            self.assertEqual({r['employee_id'][0] for r in rows}, {self.emp_an.id})          # Cas is niet gekoppeld
            self.assertIn('date_version', rows[0])
        else:
            self.assertEqual(self.run_endpoint('calendars/versions', {'employee_ids': [self.emp_an.id]})[0], 404)
        if 'hr.contract' in self.env:
            self.ok('calendars/contracts', {'employee_ids': [self.emp_an.id]})
        status, data = self.run_endpoint('calendars/dates', {'calendar_id': self.calendar_a.id, 'date_from': DAY, 'date_to': '2027-03-07'})
        if hasattr(self.env['resource.calendar'], 'get_attendances'):                       # 20
            self.assertEqual(status, 200, data)
            days = {str(d)[:10] for r in data['items'] for d in [r['date'], *r.get('other_dates', [])]}
            self.assertIn(DAY, days)
            self.assertNotIn('2027-03-06', days)                                             # zaterdag
            self.assertEqual(self.run_endpoint('calendars/dates', {'calendar_id': self.calendar_a.id, 'date_from': '2027-01-01',
                                                                   'date_to': '2027-06-01'})[0], 400)
        else:
            self.assertEqual((status, data['error']), (400, 'not_supported'))

    def test_holidays_without_resource_of_the_companies(self):
        Leaves = self.env['resource.calendar.leaves']
        a = Leaves.create({'name': 'SS Feestdag A', 'company_id': self.company_a.id, 'resource_id': False,
                           'date_from': '2027-03-10 00:00:00', 'date_to': '2027-03-10 23:59:59'})
        cal_b = self.env['resource.calendar'].create({'name': 'SS Rooster B', 'company_id': self.company_b.id})
        b = Leaves.with_company(self.company_b).create({'name': 'SS Feestdag B', 'company_id': self.company_b.id, 'resource_id': False,
                                                        'calendar_id': cal_b.id, 'date_from': '2027-03-11 00:00:00',
                                                        'date_to': '2027-03-11 23:59:59'})
        own = Leaves.create({'name': 'SS Persoonlijk', 'company_id': self.company_a.id, 'resource_id': self.emp_an.resource_id.id,
                             'calendar_id': self.calendar_a.id, 'date_from': '2027-03-12 00:00:00', 'date_to': '2027-03-12 23:59:59'})
        ids = {h['id'] for h in self.ok('holidays', {'company_ids': [self.company_a.id, self.company_b.id], 'date_from': '2027-01-01'})['items']}
        self.assertEqual(b.company_id, self.company_b)
        self.assertIn(a.id, ids)
        self.assertNotIn(b.id, ids)                                                         # ander bedrijf
        self.assertNotIn(own.id, ids)                                                       # persoonlijk (resource_id)

    # ── verlof ──

    def test_types_allocations_and_balance(self):
        self.need_leave()
        self.assertEqual(self.ok('leave/types', {'company_ids': [self.company_a.id]})['model'], self.type_model)
        self.assertIn(self.leave_type.id, {t['id'] for t in self.items('leave/types', {'company_ids': [self.company_a.id]})})
        self.ok('leave/allocations', {'employee_ids': [self.emp_an.id, self.emp_cas.id]})
        rows = self.ok('leave/balance', {'employee_id': self.emp_an.id, 'type_ids': [self.leave_type.id], 'as_of': DAY,
                                         'company_id': self.company_a.id})['items']
        self.assertEqual([r['id'] for r in rows], [self.leave_type.id])
        self.assertIn('virtual_remaining_leaves', rows[0])
        status, data = self.run_endpoint('leave/balance', {'employee_id': self.emp_cas.id, 'type_ids': [self.leave_type.id], 'as_of': DAY})
        self.assertEqual((status, data['error']), (403, 'employee_not_allowed'))

    def _to_confirm(self, leave_id):
        """20 approves a request of an Officer at once (SPEC 9 G2): back to approval, like the sync does."""
        leave = self.env['hr.leave'].browse(leave_id)
        if leave.state == 'validate':
            self.ok('leave/reopen', {'id': leave_id, 'action': 'action_back_to_approval'})
        self.assertEqual(leave.state, 'confirm')
        return leave

    def test_request_then_odoo_decides(self):
        """Goedkeuren en weigeren doet de klant in Odoo (SPEC 9 L1, module 1.2.0): de dienst kan een aanvraag niet goedkeuren
        of weigeren, alleen goedgekeurd verlof intrekken (18 weigeren, 19/20 terug naar goedkeuring)."""
        self.need_leave()
        data = self.ok('leave/create', {'vals': self.leave_vals(self.emp_an, DAY, '2027-03-02', name='SS notitie'),
                                        'company_id': self.company_a.id, 'tz': 'Europe/Brussels'})
        leave = self._to_confirm(data['id'])
        self.assertEqual((leave.employee_id, leave.create_uid, leave.number_of_days), (self.emp_an, self.connector, 2))
        found = self.ok('leave/search', {'employee_ids': [self.emp_an.id], 'date_from': '2027-01-01'})['items']
        self.assertEqual([r['id'] for r in found], [leave.id])
        self.assertFalse({'name', 'private_name'} & set(found[0]))                          # de reden komt nooit mee
        self.assertEqual(self.ok('leave/exists', {'ids': [leave.id, 999999]})['existing'], [leave.id])
        self.assertEqual(self.run_endpoint('leave/approve', {'id': leave.id})[0], 404)       # bestaat niet meer (1.2.0)
        status, data = self.run_endpoint('leave/refuse', {'id': leave.id})                 # een aanvraag weigeren: niet de dienst
        self.assertEqual((status, data['error']), (422, 'validation'))
        self.assertEqual(leave.state, 'confirm')
        leave.action_approve()                                                              # de verantwoordelijke, in Odoo
        if leave.state == 'validate1':
            leave.action_validate() if hasattr(type(leave), 'action_validate') else leave.action_approve()
        self.assertEqual(leave.state, 'validate')
        if hasattr(type(leave), 'action_back_to_approval'):                                # 19/20: intrekken = terug en weg
            self.ok('leave/reopen', {'id': leave.id, 'action': 'action_back_to_approval'})
            self.assertEqual(leave.state, 'confirm')
            status, data = self.run_endpoint('leave/reopen', {'id': leave.id})               # vanaf 'confirm': niets te doen
            self.assertEqual((status, data['error']), (422, 'validation'))
            leave.action_approve()
        else:                                                                               # 18: geen terugzetten via de dienst
            status, data = self.run_endpoint('leave/reopen', {'id': leave.id, 'action': 'action_reset_confirm'})
            self.assertEqual((status, data['error']), (400, 'not_supported'))
        self.ok('leave/refuse', {'id': leave.id})                                           # intrekken op 18
        self.assertEqual(leave.state, 'refuse')
        status, data = self.run_endpoint('leave/refuse', {'id': leave.id})
        self.assertEqual((status, data['error']), (422, 'validation'))
        status, data = self.run_endpoint('leave/withdraw', {'id': leave.id})                # een Officer: alleen confirm/cancel
        self.assertEqual((status, data['error']), (422, 'validation'))
        self.assertTrue(leave.exists())

    def test_write_and_withdraw(self):
        self.need_leave()
        leave = self._to_confirm(self.ok('leave/create', {'vals': self.leave_vals(self.emp_an)})['id'])
        self.ok('leave/write', {'id': leave.id, 'values': {'request_date_to': '2027-03-03'}})
        self.assertEqual(str(leave.request_date_to), '2027-03-03')
        status, data = self.run_endpoint('leave/write', {'id': leave.id, 'values': {'state': 'validate'}})
        self.assertEqual((status, data['error']), (400, 'field_not_allowed'))
        status, data = self.run_endpoint('leave/refuse', {'id': leave.id, 'action': 'action_unlink_all'})
        self.assertEqual((status, data['error']), (400, 'not_supported'))
        self.ok('leave/withdraw', {'id': leave.id})
        self.assertFalse(leave.exists())

    def test_only_allowed_employees(self):
        self.need_leave()
        for employee in (self.emp_bo, self.emp_cas, self.emp_b):                            # niet toegelaten, niet gekoppeld, ander bedrijf
            status, data = self.run_endpoint('leave/create', {'vals': self.leave_vals(employee)})
            self.assertEqual((status, data['error']), (403, 'employee_not_allowed'), employee.name)
        self.assertFalse(self.env['hr.leave'].search([('employee_id', 'in', [self.emp_bo.id, self.emp_cas.id, self.emp_b.id])]))
        # verlof dat de beheerder in Odoo maakte voor Bo: lezen mag (gekoppeld), wijzigen niet (niet toegelaten)
        theirs = self.env['hr.leave'].create(self.leave_vals(self.emp_bo))
        self.assertIn(theirs.id, {r['id'] for r in self.ok('leave/search', {'ids': [theirs.id]})['items']})
        for endpoint in ('leave/refuse', 'leave/withdraw'):
            self.assertEqual(self.run_endpoint(endpoint, {'id': theirs.id})[0], 403, endpoint)
        status, _data = self.run_endpoint('leave/create', {'vals': {**self.leave_vals(self.emp_an), 'state': 'validate'}})
        self.assertEqual(status, 400)

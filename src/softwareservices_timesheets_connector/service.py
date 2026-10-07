"""
De service-laag (SPEC 5.1 §6 en §7): alles wat de dienst in Odoo mag, en niets anders.

Het ORM-werk draait als de technische gebruiker van de module (inactief, geen login) in sudo-modus. Sudo omdat
de gewone rechten van Odoo hier te grof of te fijn zijn (privéprojecten, hr.employee voor wie geen HR-officer is); de grenzen
staan dus hier, per endpoint, expliciet:

  - alleen de bedrijven van de koppeling (connection.company_ids);
  - projecten met allow_timesheets in die bedrijven, en hun taken;
  - urenregels (met een project) van gekoppelde werknemers; schrijven alleen voor werknemers met allowed = True;
  - alleen de velden uit SPEC 4.1 §5; geen generieke call.

Rooster en verlof (SPEC 9 §8.3, taak 9.5) volgen dezelfde grenzen: roosters van de bedrijven (of van gekoppelde werknemers),
feestdagen zonder resource, en verlof, toewijzingen en saldo van gekoppelde werknemers; verlof aanvragen, aanpassen,
goedkeuren, weigeren, terugzetten en intrekken alleen voor werknemers met allowed = True. De reden van verlof (name,
private_name) leest de module nooit; onze notitie mag als name mee bij het aanmaken. Odoo's eigen goedkeuringsregels
(_check_approval_update) draaien als de technische gebruiker zonder sudo: hij is Time Off: Officer en kan dus precies wat de
bot van de API-koppeling kan, niet meer.

De connector-gebruiker staat als create_uid/write_uid op elke regel en elk verlof (geen impersonatie).
"""
import logging
import time
from datetime import date as Date, timedelta

import pytz

import psycopg2

from odoo import _, fields, release
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError

_logger = logging.getLogger(__name__)

MAX_LIMIT = 500
MAX_LINES = 200
CONNECTOR_XMLID = 'softwareservices_timesheets_connector.user_connector'

FIELDS = {
    'res.company': ['id', 'name', 'currency_id'],
    'hr.employee': ['id', 'name', 'work_email', 'user_id', 'company_id', 'active', 'write_date'],
    'project.project': ['id', 'name', 'company_id', 'partner_id', 'active', 'allow_timesheets', 'privacy_visibility', 'write_date',
                        'date_start', 'date'],
    'project.task': ['id', 'name', 'project_id', 'stage_id', 'state', 'user_ids', 'active', 'allocated_hours', 'date_deadline',
                     'write_date', 'parent_id', 'planned_date_begin', 'planned_date_end'],
    'account.analytic.line': ['id', 'date', 'unit_amount', 'name', 'project_id', 'task_id', 'employee_id', 'user_id', 'company_id',
                              'write_date', 'create_uid', 'validated', 'ss_client_ref', 'holiday_id', 'global_leave_id'],
}
# Rooster en verlof (SPEC 9 §5.1, §6.1): dezelfde velden als app/odoo/client.py; per versie alleen wat bestaat. Nooit name of
# private_name van hr.leave (de reden is persoonlijk, SPEC 9 §14).
HR_FIELDS = {
    'resource.calendar': ['id', 'name', 'company_id', 'hours_per_day', 'active', 'write_date', 'tz', 'two_weeks_calendar',
                          'flexible_hours', 'schedule_type', 'hours_per_week', 'calendar_type', 'days_per_week',
                          'full_time_required_hours'],
    'resource.calendar.attendance': ['id', 'calendar_id', 'dayofweek', 'hour_from', 'hour_to', 'day_period', 'sequence', 'week_type',
                                     'display_type', 'date_from', 'date_to', 'resource_id', 'duration_hours', 'date', 'recurrency',
                                     'recurrency_type', 'recurrency_interval', 'recurrency_end_type', 'recurrency_count',
                                     'recurrency_until', 'recurrency_excluded_occurences'],
    'resource.calendar.leaves': ['id', 'name', 'company_id', 'calendar_id', 'date_from', 'date_to', 'resource_id', 'write_date',
                                 'time_type', 'count_as'],
    'hr.version': ['id', 'employee_id', 'date_version', 'resource_calendar_id'],
    'hr.contract': ['id', 'employee_id', 'date_start', 'date_end', 'resource_calendar_id', 'state'],
    'hr.leave.type': ['id', 'name', 'active', 'color', 'request_unit', 'leave_validation_type', 'unpaid', 'allows_negative',
                      'max_allowed_negative', 'write_date', 'requires_allocation', 'company_id', 'time_type', 'count_as',
                      'include_public_holidays_in_duration', 'time_off_selectable', 'unit_of_measure', 'sequence'],
    'hr.leave': ['id', 'employee_id', 'state', 'request_date_from', 'request_date_to', 'date_from', 'date_to', 'number_of_days',
                 'number_of_hours', 'request_date_from_period', 'request_date_to_period', 'request_hour_from', 'request_hour_to',
                 'request_unit_half', 'request_unit_hours', 'company_id', 'create_uid', 'write_date', 'holiday_status_id',
                 'work_entry_type_id', 'timesheet_ids'],
    'hr.leave.allocation': ['id', 'employee_id', 'state', 'number_of_days', 'date_from', 'date_to', 'write_date', 'holiday_status_id',
                            'work_entry_type_id', 'number_of_hours_display', 'number_of_hours', 'allocation_type', 'accrual_plan_id'],
}
HR_FIELDS['hr.work.entry.type'] = HR_FIELDS['hr.leave.type']                  # Odoo 20: de verlofsoort (SPEC 9 G1)
PLAIN_IDS = {'holiday_id', 'global_leave_id'}
BALANCE_FIELDS = ['max_leaves', 'leaves_taken', 'virtual_remaining_leaves']
DATE_FIELDS = ('hour_from', 'hour_to', 'duration_hours', 'day_period', 'dayofweek')      # get_attendances (Odoo 20)
MAX_DATE_DAYS = 92
# wat de dienst op een hr.leave mag schrijven (SPEC 9 §11): de request_*-velden en name (onze notitie, alleen schrijven)
LEAVE_WRITE_FIELDS = {'request_date_from', 'request_date_to', 'request_date_from_period', 'request_date_to_period',
                      'request_hour_from', 'request_hour_to', 'request_unit_half', 'request_unit_hours', 'name'}
LEAVE_CREATE_FIELDS = LEAVE_WRITE_FIELDS | {'employee_id', 'holiday_status_id', 'work_entry_type_id'}
# Odoo's eigen overgangen per endpoint (nooit write({'state': ...})). Goedkeuren doet de klant in Odoo zelf (SPEC 9 L1, module
# 1.2.0): de dienst trekt alleen goedgekeurd verlof in (18: weigeren; 19/20: terug naar goedkeuring en verwijderen) en zet op
# 20 de automatische goedkeuring van een nieuwe aanvraag terug (L3). Beide alleen vanaf een goedgekeurde toestand.
LEAVE_ACTIONS = {'leave/refuse': ('action_refuse',), 'leave/reopen': ('action_back_to_approval',)}
LEAVE_ACTION_STATES = ('validate', 'validate1')
DEFAULT_TZ = 'Europe/Brussels'

# wat /timesheets/write mag veranderen (onze naam → veld in Odoo); user_id nooit: Odoo vult het uit de werknemer
WRITE_FIELDS = {'date': 'date', 'hours': 'unit_amount', 'unit_amount': 'unit_amount', 'name': 'name', 'project_id': 'project_id',
                'task_id': 'task_id', 'employee_id': 'employee_id', 'company_id': 'company_id'}


class ServiceError(Exception):
    """An error for the whole request: HTTP status + code + text."""

    def __init__(self, status, code, message=None, details=None):
        super().__init__(message or code)
        self.status, self.code, self.message, self.details = status, code, message or code, details


class LineError(Exception):
    """An error for one line in a batch."""

    def __init__(self, code, message=None, **extra):
        super().__init__(message or code)
        self.code, self.message, self.extra = code, message or code, extra


def _dt(value):
    if not value:
        return value
    if isinstance(value, Date) and not hasattr(value, 'hour'):
        return fields.Date.to_string(value)
    return fields.Datetime.to_string(value)


def _ids(value, name):
    if value in (None, False):
        return []
    if not isinstance(value, (list, tuple)) or not all(isinstance(i, int) and not isinstance(i, bool) for i in value):
        raise ServiceError(400, 'bad_request', f'{name} must be a list of ids')
    return list(value)


def _int(value, name, required=True):
    if value in (None, False) and not required:
        return False
    if not isinstance(value, int) or isinstance(value, bool):
        raise LineError('validation', f'{name} is missing or not an id')
    return value


def _date(value, name='date'):
    try:
        return fields.Date.to_string(fields.Date.to_date(value))
    except (TypeError, ValueError):
        raise LineError('validation', f'{name} moet YYYY-MM-DD zijn') from None


class ConnectorService:
    def __init__(self, env, connection):
        connector = env.ref(CONNECTOR_XMLID)
        base = env(user=connector.id, su=True)
        allowed = connection.sudo().company_ids or base['res.company'].search([])
        self.connection = connection.sudo()
        self.company_ids = allowed.ids
        self.env = base(context={'allowed_company_ids': allowed.ids, 'active_test': False, 'tz': 'UTC',
                                 'tracking_disable': True, 'mail_notrack': True})

    # ── hulp ──

    def _fields(self, model):
        present = self.env[model]._fields
        return [f for f in FIELDS[model] if f in present]

    def _read(self, records, model):
        return self._rows(records, self._fields(model))

    @staticmethod
    def _rows(records, fields_):
        rows = records.read([f for f in fields_ if f != 'id'])
        for row in rows:
            for key, value in row.items():
                if hasattr(value, 'year'):
                    row[key] = _dt(value)
                elif key in PLAIN_IDS:                    # alleen het id: de naam van een verlof kan de reden bevatten
                    row[key] = value[0] if isinstance(value, (list, tuple)) and value else (value or False)
        return rows

    @staticmethod
    def _page(body):
        try:
            limit = min(int(body.get('limit') or MAX_LIMIT), MAX_LIMIT)
            offset = max(int(body.get('offset') or 0), 0)
        except (TypeError, ValueError):
            raise ServiceError(400, 'bad_request', 'limit and offset must be numbers') from None
        return limit, offset

    @staticmethod
    def _since(body):
        since = body.get('since')
        if not since:
            return []
        try:
            return [('write_date', '>', fields.Datetime.to_string(fields.Datetime.to_datetime(since)))]
        except (TypeError, ValueError):
            raise ServiceError(400, 'bad_request', 'since must be YYYY-MM-DD HH:MM:SS') from None

    def _companies(self, body):
        wanted = _ids(body.get('company_ids'), 'company_ids')
        return [c for c in wanted if c in self.company_ids] if wanted else list(self.company_ids)

    def _search(self, model, domain, body):
        limit, offset = self._page(body)
        records = self.env[model].search(domain, limit=limit, offset=offset, order='write_date, id')
        return {'items': self._read(records, model)}

    def _project_domain(self):
        return [('allow_timesheets', '=', True), ('company_id', 'in', self.company_ids + [False])]    # ook gedeelde projecten (zonder bedrijf)

    def _links(self, allowed_only=False):
        domain = [('allowed', '=', True)] if allowed_only else []
        return set(self.env['ss_timesheets.employee_link'].search(domain).mapped('employee_id').ids)

    def _line_domain(self):
        return [('project_id', '!=', False), ('employee_id', 'in', sorted(self._links())), ('company_id', 'in', self.company_ids)]

    def _check_employee(self, employee_id):
        if employee_id not in self._links(allowed_only=True):
            raise LineError('employee_not_allowed', 'This employee is no longer allowed for {{PRODUCT_NAME}}')
        employee = self.env['hr.employee'].browse(employee_id).exists()
        if not employee or employee.company_id.id not in self.company_ids:
            raise LineError('employee_not_allowed', 'This employee does not belong to a connected company')
        return employee

    def _check_project(self, project_id, task_id):
        project = self.env['project.project'].search(self._project_domain() + [('id', '=', project_id)])
        if not project:
            raise LineError('project_not_allowed', 'This project does not exist, does not allow timesheets or belongs to another company')
        if task_id:
            task = self.env['project.task'].browse(task_id).exists()
            if not task or task.project_id != project:
                raise LineError('validation', 'This task does not belong to the project')
        return project

    def _current(self, line):
        return self._read(line, 'account.analytic.line')[0]

    # ── endpoints (SPEC 5.1 §7) ──

    def ping(self, body):
        Connection = self.connection.env['ss_timesheets.connection']
        Connection._ensure_connector()                     # Time Off later geïnstalleerd: de rol meteen erbij (SPEC 9 §8.3)
        info = self.connection._server_info()
        companies = self.env['res.company'].browse(self.company_ids)
        fields_ = {model: self._fields(model) for model in FIELDS}
        fields_['hr.employee.public'] = [f for f in fields_['hr.employee'] if f != 'active']
        # rooster en verlof: alleen de modellen die deze Odoo heeft (ModuleTransport.fields_get → OdooClient.features)
        fields_.update({model: self._present(model, wanted) for model, wanted in HR_FIELDS.items() if model in self.env})
        return {**info, 'server_version': release.version, 'server_time': int(time.time()), 'mode': self.connection.mode,
                'companies': [{'id': c.id, 'name': c.name} for c in companies], 'fields': fields_,
                'groups': Connection._connector_groups(), 'endpoints': sorted(ENDPOINTS)}

    def companies(self, body):
        return {'items': [{'id': c.id, 'name': c.name, 'currency': c.currency_id.name}
                          for c in self.env['res.company'].browse(self.company_ids)]}

    def employees(self, body):
        out = self._search('hr.employee', [('company_id', 'in', self._companies(body))] + self._since(body), body)
        users = {u.id: u for u in self.env['res.users'].browse([r['user_id'][0] for r in out['items'] if r.get('user_id')])}
        for row in out['items']:
            user = users.get(row['user_id'][0]) if row.get('user_id') else None
            row['user_login'], row['user_email'] = (user.login, user.email) if user else (False, False)
        return out

    def projects(self, body):
        domain = [('allow_timesheets', '=', True), ('company_id', 'in', self._companies(body) + [False])] + self._since(body)
        if body.get('project_ids'):
            domain.append(('id', 'in', _ids(body['project_ids'], 'project_ids')))
        return self._search('project.project', domain, body)

    def tasks(self, body):
        wanted = _ids(body.get('project_ids'), 'project_ids')
        projects = self.env['project.project'].search(self._project_domain() + [('id', 'in', wanted)]).ids if wanted else []
        return self._search('project.task', [('project_id', 'in', projects)] + self._since(body), body)

    def timesheets_search(self, body):
        domain = self._line_domain() + self._since(body)
        ids = _ids(body.get('ids'), 'ids')
        if ids:
            domain.append(('id', 'in', ids))
        else:
            domain.append(('employee_id', 'in', _ids(body.get('employee_ids'), 'employee_ids')))
            for key, op in (('date_from', '>='), ('date_to', '<=')):
                if body.get(key):
                    domain.append(('date', op, self._day(body, key)))
        # Odoo's eigen verlofregels (project_timesheet_holidays, SPEC 9 §6.5): 'exclude' voor de uren, 'only' voor het opruimen
        marks = [f for f in ('holiday_id', 'global_leave_id') if f in self.env['account.analytic.line']._fields]
        if body.get('leave_lines') == 'exclude':
            domain += [(f, '=', False) for f in marks]
        elif body.get('leave_lines') == 'only':
            if not marks:
                return {'items': []}
            domain += (['|'] if len(marks) == 2 else []) + [(f, '!=', False) for f in marks]
        return self._search('account.analytic.line', domain, body)

    def timesheets_exists(self, body):
        ids = _ids(body.get('ids'), 'ids')
        found = self.env['account.analytic.line'].search(self._line_domain() + [('id', 'in', ids)]).ids if ids else []
        return {'existing': found}

    def _lines(self, body, key='lines'):
        lines = body.get(key)
        if not isinstance(lines, list) or not all(isinstance(x, dict) for x in lines):
            raise ServiceError(400, 'bad_request', f'{key} must be a list')
        if len(lines) > MAX_LINES:
            raise ServiceError(413, 'too_many_lines', f'At most {MAX_LINES} lines per request')
        return lines

    def _each(self, items, ident, work):
        """Run work(item) per item in its own savepoint, so one error does not stop the batch."""
        results = []
        for item in items:
            ref = ident(item)
            try:
                with self.env.cr.savepoint():
                    results.append(work(item))
                    self.env.flush_all()
            except LineError as e:
                self.env.invalidate_all()
                results.append({**ref, 'error': e.code, 'message': e.message, **e.extra})
            except (UserError, ValidationError, AccessError, MissingError, psycopg2.Error) as e:
                self.env.invalidate_all()
                message = e.args[0] if isinstance(e, Exception) and e.args else str(e)
                _logger.info('ss_timesheets: line refused by Odoo: %s', message)
                results.append({**ref, 'error': 'validation', 'message': str(message)})
        return {'results': results}

    def timesheets_create(self, body):
        Line = self.env['account.analytic.line']

        def create(line):
            ref = line.get('client_ref')
            ref = str(ref) if ref not in (None, False, '') else False
            if ref:
                existing = Line.search([('ss_client_ref', '=', ref)], limit=1)
                if existing:
                    raise LineError('duplicate', 'This line already exists', id=existing.id, write_date=_dt(existing.write_date))
            employee = self._check_employee(_int(line.get('employee_id'), 'employee_id'))
            project_id = _int(line.get('project_id'), 'project_id')
            task_id = _int(line.get('task_id'), 'task_id', required=False)
            project = self._check_project(project_id, task_id)
            company_id = line.get('company_id') or employee.company_id.id
            if company_id not in self.company_ids or company_id != employee.company_id.id:
                raise LineError('validation', "The line's company must be the employee's company")
            hours = line.get('hours')
            if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours < 0 or hours > 24:
                raise LineError('validation', 'hours must be between 0 and 24')
            vals = {'date': _date(line.get('date')), 'unit_amount': round(float(hours), 4), 'name': str(line.get('name') or '/')[:2000],
                    'project_id': project.id, 'task_id': task_id or False, 'employee_id': employee.id, 'company_id': company_id,
                    'ss_client_ref': ref}
            rec = Line.with_context(allowed_company_ids=[company_id] + [c for c in self.company_ids if c != company_id]).create(vals)
            rec.flush_recordset()
            return {'client_ref': line.get('client_ref'), 'id': rec.id, 'write_date': _dt(rec.write_date)}
        return self._each(self._lines(body), lambda line: {'client_ref': line.get('client_ref')}, create)

    def _own_line(self, line_id, lock=False):
        line_id = _int(line_id, 'id')
        if lock:
            self.env.cr.execute('SELECT id FROM account_analytic_line WHERE id = %s FOR UPDATE', (line_id,))
        line = self.env['account.analytic.line'].search([('id', '=', line_id), ('project_id', '!=', False),
                                                          ('company_id', 'in', self.company_ids)])
        if not line:
            raise LineError('not_found', 'This line no longer exists')
        self._check_employee(line.employee_id.id)
        if 'validated' in line._fields and line.validated:
            raise LineError('validated', 'This line has been validated in Odoo', current=self._current(line))
        return line

    def timesheets_write(self, body):
        def write(item):
            line = self._own_line(item.get('id'), lock=True)
            expected = item.get('expected_write_date')
            if expected and _dt(line.write_date) != str(expected)[:19]:
                raise LineError('conflict', 'The line was changed in Odoo', current=self._current(line))
            values = item.get('values')
            if not isinstance(values, dict) or not values:
                raise LineError('validation', 'values is missing')
            unknown = sorted(set(values) - set(WRITE_FIELDS))
            if unknown:
                raise LineError('field_not_allowed', 'The module cannot write these fields: ' + ', '.join(unknown))
            vals = {WRITE_FIELDS[k]: v for k, v in values.items()}
            employee = self._check_employee(_int(vals['employee_id'], 'employee_id')) if 'employee_id' in vals else line.employee_id
            if 'project_id' in vals or 'task_id' in vals:
                project_id = _int(vals.get('project_id', line.project_id.id), 'project_id')
                task_id = _int(vals.get('task_id', line.task_id.id), 'task_id', required=False)
                self._check_project(project_id, task_id)
                vals['task_id'] = task_id or False
            if 'date' in vals:
                vals['date'] = _date(vals['date'])
            if 'unit_amount' in vals:
                hours = vals['unit_amount']
                if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours < 0 or hours > 24:
                    raise LineError('validation', 'hours must be between 0 and 24')
            if 'company_id' in vals and vals['company_id'] != employee.company_id.id:
                raise LineError('validation', "The line's company must be the employee's company")
            if 'name' in vals:
                vals['name'] = str(vals['name'] or '/')[:2000]
            line.write(vals)
            line.flush_recordset()
            return {'id': line.id, 'write_date': _dt(line.write_date)}
        return self._each(self._lines(body), lambda item: {'id': item.get('id')}, write)

    def timesheets_delete(self, body):
        def delete(line_id):
            line = self._own_line(line_id)
            line.unlink()
            return {'id': line_id, 'ok': True}
        return self._each(_ids(body.get('ids'), 'ids')[:MAX_LINES], lambda line_id: {'id': line_id}, delete)

    def links(self, body):
        """The full list of linked employees from the service (idempotent): new ones get allowed = True, existing ones keep the
        choice of the Odoo administrator, links missing from the list are removed."""
        items = body.get('links')
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            raise ServiceError(400, 'bad_request', 'links must be a list')
        Link = self.env['ss_timesheets.employee_link']
        wanted = {}
        for item in items:
            employee_id = item.get('employee_id')
            if isinstance(employee_id, int) and not isinstance(employee_id, bool):
                wanted[employee_id] = str(item.get('external_user') or '')[:200]
        employees = self.env['hr.employee'].search([('id', 'in', list(wanted)), ('company_id', 'in', self.company_ids)])
        existing = {link.employee_id.id: link for link in Link.search([])}
        for employee in employees:
            link = existing.pop(employee.id, None)
            if link:
                if link.external_user != wanted[employee.id]:
                    link.external_user = wanted[employee.id]
            else:
                Link.create({'employee_id': employee.id, 'external_user': wanted[employee.id]})
        if existing:
            Link.browse([link.id for link in existing.values()]).unlink()
        return {'items': [{'employee_id': link.employee_id.id, 'allowed': link.allowed} for link in Link.search([])],
                'unknown': sorted(set(wanted) - set(employees.ids))}

    # ── rooster (SPEC 9 §5, §8.3) ──

    def _present(self, model, wanted):
        have = self.env[model]._fields
        return [f for f in wanted if f in have]

    def _need(self, model):
        if model not in self.env:
            raise ServiceError(404, 'not_installed', f'{model} does not exist in this Odoo')

    @staticmethod
    def _day(body, key):
        try:
            return _date(body.get(key), key)
        except LineError as e:
            raise ServiceError(400, 'bad_request', e.message) from None

    def _search_fields(self, model, domain, wanted, body, order='id', active_test=False):
        limit, offset = self._page(body)
        records = self.env[model].with_context(active_test=active_test).search(domain, limit=limit, offset=offset, order=order)
        return {'items': self._rows(records, self._present(model, wanted))}

    def _linked_employees(self, wanted=None, allowed_only=False):
        """Linked employees (allowed_only: with allowed = True) of the companies of the connection, optionally ∩ wanted."""
        ids = sorted(self._links(allowed_only))
        if wanted is not None:
            ids = sorted(set(ids) & set(wanted))
        if not ids:
            return []
        return self.env['hr.employee'].search([('id', 'in', ids), ('company_id', 'in', self.company_ids)]).ids

    def _employee_calendars(self):
        """Every calendar a linked employee has now, or had in a version (19/20) or contract (18)."""
        employees = self._linked_employees()
        cals = set(self.env['hr.employee'].browse(employees).mapped('resource_calendar_id').ids)
        for model in ('hr.version', 'hr.contract'):
            if model in self.env and employees:
                cals |= set(self.env[model].search([('employee_id', 'in', employees)]).mapped('resource_calendar_id').ids)
        return cals

    def _allowed_calendars(self, ids):
        """Of these calendar ids: the ones of the companies (or without company), and those of linked employees."""
        if not ids:
            return []
        ok = set(self.env['resource.calendar'].search([('id', 'in', ids), ('company_id', 'in', self.company_ids + [False])]).ids)
        rest = set(ids) - ok
        if rest:
            ok |= rest & self._employee_calendars()
        return sorted(ok)

    def calendars(self, body):
        if 'ids' in body:
            domain = [('id', 'in', self._allowed_calendars(_ids(body.get('ids'), 'ids')))]
        else:
            domain = [('company_id', 'in', self._companies(body) + [False])]
        return self._search_fields('resource.calendar', domain, HR_FIELDS['resource.calendar'], body)

    def calendar_attendances(self, body):
        cals = self._allowed_calendars(_ids(body.get('calendar_ids'), 'calendar_ids'))
        return self._search_fields('resource.calendar.attendance', [('calendar_id', 'in', cals)],
                                   HR_FIELDS['resource.calendar.attendance'], body, active_test=True)

    def calendar_dates(self, body):
        """Odoo 20: resource.calendar.get_attendances for one calendar and at most 92 days (SPEC 9 G10)."""
        Calendar = self.env['resource.calendar']
        if not hasattr(Calendar, 'get_attendances'):
            raise ServiceError(400, 'not_supported', 'This Odoo has no get_attendances (Odoo 20 only)')
        calendar_id = body.get('calendar_id')
        if not isinstance(calendar_id, int) or isinstance(calendar_id, bool) or not self._allowed_calendars([calendar_id]):
            raise ServiceError(404, 'not_found', 'This schedule does not exist or belongs to another company')
        date_from, date_to = self._day(body, 'date_from'), self._day(body, 'date_to')
        span = fields.Date.to_date(date_to) - fields.Date.to_date(date_from)
        if span < timedelta(0) or span >= timedelta(days=MAX_DATE_DAYS):
            raise ServiceError(400, 'bad_request', f'At most {MAX_DATE_DAYS} days at a time')
        have = self.env['resource.calendar.attendance']._fields
        wanted = body.get('fields') or list(DATE_FIELDS)
        fields_ = [f for f in DATE_FIELDS if f in wanted and f in have]
        return {'items': Calendar.browse(calendar_id).get_attendances(date_from, date_to, fields_) or []}

    def calendar_employees(self, body):
        """The current calendar of linked employees: [{id, resource_calendar_id}]."""
        employees = self._linked_employees(_ids(body.get('employee_ids'), 'employee_ids'))
        return self._search_fields('hr.employee', [('id', 'in', employees)], ['id', 'resource_calendar_id'], body)

    def calendar_versions(self, body):
        """19/20: hr.version of linked employees (the calendar per period, SPEC 9 G12)."""
        self._need('hr.version')
        employees = self._linked_employees(_ids(body.get('employee_ids'), 'employee_ids'))
        return self._search_fields('hr.version', [('employee_id', 'in', employees)], HR_FIELDS['hr.version'], body,
                                   order='date_version, id', active_test=True)

    def calendar_contracts(self, body):
        """18 with hr_contract: running and closed contracts of linked employees (only the calendar fields)."""
        self._need('hr.contract')
        employees = self._linked_employees(_ids(body.get('employee_ids'), 'employee_ids'))
        return self._search_fields('hr.contract', [('employee_id', 'in', employees), ('state', 'in', ['open', 'close'])],
                                   HR_FIELDS['hr.contract'], body, order='date_start, id', active_test=True)

    def holidays(self, body):
        """Public holidays and closing days: resource.calendar.leaves without a resource (SPEC 9 G13)."""
        domain = [('resource_id', '=', False), ('company_id', 'in', self._companies(body) + [False])]
        if body.get('date_from'):
            domain.append(('date_to', '>=', self._day(body, 'date_from')))
        return self._search_fields('resource.calendar.leaves', domain, HR_FIELDS['resource.calendar.leaves'], body,
                                   order='date_from, id')

    # ── verlof (SPEC 9 §6, §8.3) ──

    def _type_model(self):
        self._need('hr.leave')
        return 'hr.leave.type' if 'hr.leave.type' in self.env else 'hr.work.entry.type'

    def _type_field(self):
        return 'holiday_status_id' if 'holiday_status_id' in self.env['hr.leave']._fields else 'work_entry_type_id'

    def _type_domain(self, companies):
        model = self._type_model()
        if 'time_off_selectable' in self.env[model]._fields:              # 20: geen company_id meer (SPEC 9 G1)
            return [('time_off_selectable', '=', True)]
        return ['|', ('company_id', 'in', companies), ('company_id', '=', False)]

    def _allowed_types(self, ids):
        ids = [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]
        if not ids:
            return []
        model = self._type_model()
        return self.env[model].search(self._type_domain(self.company_ids) + [('id', 'in', ids)]).ids

    def leave_types(self, body):
        model = self._type_model()
        out = self._search_fields(model, self._type_domain(self._companies(body)), HR_FIELDS[model], body)
        out['model'] = model
        return out

    def leave_allocations(self, body):
        self._need('hr.leave.allocation')
        employees = self._linked_employees(_ids(body.get('employee_ids'), 'employee_ids'))
        domain = [('employee_id', 'in', employees), ('state', '=', 'validate')] + self._since(body)
        return self._search_fields('hr.leave.allocation', domain, HR_FIELDS['hr.leave.allocation'], body)

    def leave_balance(self, body):
        """Balance per type for one linked employee: the computed fields of the type, with context employee_id (SPEC 9 G8)."""
        model = self._type_model()
        employee_id = body.get('employee_id')
        if not isinstance(employee_id, int) or isinstance(employee_id, bool) or not self._linked_employees([employee_id]):
            raise ServiceError(403, 'employee_not_allowed', 'This employee is not linked to {{PRODUCT_NAME}}')
        company_id = body.get('company_id')
        if company_id not in self.company_ids:
            company_id = self.env['hr.employee'].browse(employee_id).company_id.id
        types = self._allowed_types(_ids(body.get('type_ids'), 'type_ids'))
        ctx = {'employee_id': employee_id, 'leave_date_from': self._day(body, 'as_of'), 'allowed_company_ids': [company_id]}
        rows = self.env[model].with_context(**ctx).browse(types).read(BALANCE_FIELDS)
        return {'items': rows}

    def _leave_domain(self):
        return [('employee_id', 'in', self._linked_employees())]

    def leave_search(self, body):
        """hr.leave of linked employees, without the reason (name/private_name are never read)."""
        self._need('hr.leave')
        domain = self._leave_domain() + self._since(body)
        if 'ids' in body:
            domain.append(('id', 'in', _ids(body.get('ids'), 'ids')))
        else:
            domain.append(('employee_id', 'in', _ids(body.get('employee_ids'), 'employee_ids')))
            if body.get('date_from'):
                domain.append(('request_date_to', '>=', self._day(body, 'date_from')))
        return self._search_fields('hr.leave', domain, HR_FIELDS['hr.leave'], body, order='write_date, id', active_test=True)

    def leave_exists(self, body):
        """Of these hr.leave ids: the ones that still exist in the companies of the connection (deleted in Odoo, SPEC 9 §6.4)."""
        self._need('hr.leave')
        ids = _ids(body.get('ids'), 'ids')
        found = self.env['hr.leave'].with_context(active_test=True).search(
            [('id', 'in', ids), ('employee_id.company_id', 'in', self.company_ids)]).ids if ids else []
        return {'existing': found}

    def _leave_env(self, body, company_id):
        """Like the bot: allowed_company_ids = the company of the employee, a tz, active_test on, and Odoo's own chatter and
        activities (no tracking_disable)."""
        tz = body.get('tz') if body.get('tz') in pytz.all_timezones_set else DEFAULT_TZ
        ctx = {k: v for k, v in self.env.context.items() if k not in ('tracking_disable', 'mail_notrack', 'active_test')}
        return self.env(context={**ctx, 'allowed_company_ids': [company_id], 'tz': tz})

    def _single(self, work):
        """Run one change in its own savepoint; Odoo's refusal becomes an error for the whole request (422/403)."""
        try:
            with self.env.cr.savepoint():
                out = work()
                self.env.flush_all()
            return out
        except LineError as e:
            self.env.invalidate_all()
            raise ServiceError(403 if e.code == 'employee_not_allowed' else 400, e.code, e.message) from None
        except AccessError as e:
            self.env.invalidate_all()
            raise ServiceError(403, 'access_denied', str(e.args[0] if e.args else e)) from None
        except (UserError, ValidationError, MissingError, psycopg2.Error) as e:
            self.env.invalidate_all()
            message = str(e.args[0] if e.args else e)
            _logger.info('ss_timesheets: time off refused by Odoo: %s', message)
            raise ServiceError(422, 'validation', message) from None

    def _allowed_employee(self, employee_id):
        try:
            return self._check_employee(_int(employee_id, 'employee_id'))
        except LineError as e:
            raise ServiceError(403 if e.code == 'employee_not_allowed' else 400, e.code, e.message) from None

    def _own_leave(self, body):
        """The hr.leave of the body, of an allowed employee, in the leave environment of that employee."""
        self._need('hr.leave')
        leave_id = body.get('id')
        if not isinstance(leave_id, int) or isinstance(leave_id, bool):
            raise ServiceError(400, 'bad_request', 'id is missing or not an id')
        leave = self.env['hr.leave'].browse(leave_id).exists()
        if not leave:
            raise ServiceError(404, 'not_found', 'This time off no longer exists')
        employee = self._allowed_employee(leave.employee_id.id)
        return leave.with_env(self._leave_env(body, employee.company_id.id))

    def _leave_values(self, values, allowed):
        if not isinstance(values, dict) or not values:
            raise ServiceError(400, 'bad_request', 'vals is missing')
        have = self.env['hr.leave']._fields
        unknown = sorted(k for k in values if k not in allowed or k not in have)
        if unknown:
            raise ServiceError(400, 'field_not_allowed', 'The module cannot write these fields: ' + ', '.join(unknown))
        return dict(values)

    def leave_create(self, body):
        """A new hr.leave for an allowed employee, with a type the connection may see. Odoo decides the rest (and on 20
        approves it at once, because the technical user is Time Off: Officer, SPEC 9 G2)."""
        self._need('hr.leave')
        vals = self._leave_values(body.get('vals'), LEAVE_CREATE_FIELDS)
        employee = self._allowed_employee(vals.get('employee_id'))
        field = self._type_field()
        if vals.get(field) not in self._allowed_types([vals.get(field)]):
            raise ServiceError(400, 'bad_request', 'This time-off type does not exist or belongs to another company')
        Leave = self._leave_env(body, employee.company_id.id)['hr.leave']

        def create():
            leave = Leave.create([vals])
            return {'id': leave[:1].id or False}
        return self._single(create)

    def leave_write(self, body):
        """The request_* fields (and our note) of a leave; Odoo itself only allows it while the leave is to approve."""
        leave = self._own_leave(body)
        values = self._leave_values(body.get('values'), LEAVE_WRITE_FIELDS)

        def write():
            leave.write(values)
            return {'ok': True, 'write_date': _dt(leave.write_date)}
        return self._single(write)

    def _approval_check(self, leave, action):
        """Odoo's own rules for this transition, as the technical user without sudo (a Time Off Officer, like the bot)."""
        target = 'refuse' if action == 'action_refuse' else 'confirm'
        plain = leave.with_env(leave.env(su=False))
        if hasattr(plain, '_check_approval_update'):
            plain._check_approval_update(target)

    def _leave_action(self, endpoint, body):
        actions = LEAVE_ACTIONS[endpoint]
        action = body.get('action') or actions[0]
        if action not in actions or not hasattr(type(self.env['hr.leave']), action):
            raise ServiceError(400, 'not_supported', f'{action} does not exist in this Odoo')
        leave = self._own_leave(body)
        if leave.state not in LEAVE_ACTION_STATES:
            raise ServiceError(422, 'validation', 'The service does not approve or refuse time off: do that in Odoo itself')

        def act():
            self._approval_check(leave, action)
            getattr(leave, action)()
            return {'ok': True, 'state': leave.state}
        return self._single(act)

    def leave_refuse(self, body):
        return self._leave_action('leave/refuse', body)

    def leave_reopen(self, body):
        return self._leave_action('leave/reopen', body)

    def leave_withdraw(self, body):
        """unlink; Odoo allows a Time Off Officer that only for leave to approve or cancelled."""
        leave = self._own_leave(body)

        def unlink():
            leave.unlink()
            return {'ok': True}
        return self._single(unlink)

    def rotate(self, body):
        key_id, secret = self.connection._rotate()
        return {'key_id': key_id, 'secret': secret}

    def revoke(self, body):
        self.connection._wipe(_('Unpaired by {{PRODUCT_NAME}}'))
        return {'ok': True}


ENDPOINTS = {
    'ping': ConnectorService.ping,
    'companies': ConnectorService.companies,
    'employees': ConnectorService.employees,
    'projects': ConnectorService.projects,
    'tasks': ConnectorService.tasks,
    'timesheets/search': ConnectorService.timesheets_search,
    'timesheets/exists': ConnectorService.timesheets_exists,
    'timesheets/create': ConnectorService.timesheets_create,
    'timesheets/write': ConnectorService.timesheets_write,
    'timesheets/delete': ConnectorService.timesheets_delete,
    'links': ConnectorService.links,
    # rooster en verlof (SPEC 9 §8.3, taak 9.5)
    'calendars': ConnectorService.calendars,
    'calendars/attendances': ConnectorService.calendar_attendances,
    'calendars/dates': ConnectorService.calendar_dates,
    'calendars/employees': ConnectorService.calendar_employees,
    'calendars/versions': ConnectorService.calendar_versions,
    'calendars/contracts': ConnectorService.calendar_contracts,
    'holidays': ConnectorService.holidays,
    'leave/types': ConnectorService.leave_types,
    'leave/allocations': ConnectorService.leave_allocations,
    'leave/balance': ConnectorService.leave_balance,
    'leave/search': ConnectorService.leave_search,
    'leave/exists': ConnectorService.leave_exists,
    'leave/create': ConnectorService.leave_create,
    'leave/write': ConnectorService.leave_write,
    'leave/refuse': ConnectorService.leave_refuse,
    'leave/reopen': ConnectorService.leave_reopen,
    'leave/withdraw': ConnectorService.leave_withdraw,
    'rotate': ConnectorService.rotate,
    'revoke': ConnectorService.revoke,
}


def run(env, connection, endpoint, body):
    """Execute one endpoint for a verified request. Returns (status, payload)."""
    handler = ENDPOINTS.get(endpoint)
    if handler is None:
        return 404, {'error': 'not_found', 'message': 'Unknown endpoint'}
    try:
        return 200, handler(ConnectorService(env, connection), body)
    except ServiceError as e:
        payload = {'error': e.code, 'message': e.message}
        if e.details:
            payload['details'] = e.details
        return e.status, payload

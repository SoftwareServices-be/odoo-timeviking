"""The timesheet report: a simple PDF per employee and period, in the standard layout of the company.

`account.analytic.line._ss_report_sections` groups lines per employee; the PDF report (on account.analytic.line, so it
is also in the Print menu of the timesheet list), the wizard and the invoice bridge
(softwareservices_timesheets_connector_sale) all use it.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date


def hours_str(hours):
    """1.5 -> '1:30'."""
    minutes = int(round((hours or 0.0) * 60))
    sign = '-' if minutes < 0 else ''
    minutes = abs(minutes)
    return f'{sign}{minutes // 60}:{minutes % 60:02d}'


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    def _ss_report_sections(self):
        """[{'employee', 'company', 'lines', 'rows', 'period', 'hours_str', 'via_count', 'count'}] per employee, by name.

        Read with sudo: the caller decided which lines may be printed (the user's own search, or an invoice)."""
        lines = self.sudo().filtered('employee_id').sorted(lambda l: (l.employee_id.name or '', l.date, l.id))
        sections = []
        for employee in lines.mapped('employee_id'):
            mine = lines.filtered(lambda l, e=employee: l.employee_id == e)
            dates = mine.mapped('date')
            sections.append({
                'employee': employee.name,
                'job': employee.job_title or '',
                'company': mine[0].company_id or self.env.company,
                'lines': mine,
                'rows': [{
                    'date': format_date(self.env, line.date),
                    'project': line.project_id.display_name or '',
                    'task': line.task_id.name or '',
                    'description': line.name if line.name and line.name != '/' else '',
                    'hours_str': hours_str(line.unit_amount),
                    'via': bool(line.ss_client_ref),
                } for line in mine],
                'period': f'{format_date(self.env, min(dates))} - {format_date(self.env, max(dates))}',
                'hours_str': hours_str(sum(mine.mapped('unit_amount'))),
                'count': len(mine),
                'via_count': len(mine.filtered('ss_client_ref')),
            })
        return sections


class ReportTimesheet(models.AbstractModel):
    _name = 'report.softwareservices_timesheets_connector.report_timesheet'
    _description = 'Timesheet report per employee'

    @api.model
    def _get_report_values(self, docids, data=None):
        lines = self.env['account.analytic.line'].browse(docids)
        lines.check_access('read')
        return {
            'doc_ids': docids,
            'doc_model': 'account.analytic.line',
            'docs': lines,
            'title': (data or {}).get('title') or _('Timesheet report'),
            'sections': lines._ss_report_sections(),
        }


class ReportWizard(models.TransientModel):
    _name = 'ss_timesheets.report_wizard'
    _description = 'Print a timesheet report'

    date_from = fields.Date('From', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date('To', required=True, default=fields.Date.context_today)
    employee_ids = fields.Many2many('hr.employee.public', 'ss_timesheets_report_wizard_employee_rel', 'wizard_id',
                                    'employee_id', string='Employees',
                                    help='Leave empty for every employee whose timesheets you may see.')
    project_ids = fields.Many2many('project.project', 'ss_timesheets_report_wizard_project_rel', 'wizard_id', 'project_id',
                                   string='Projects', help='Leave empty for all projects.')

    def _domain(self):
        self.ensure_one()
        domain = [('project_id', '!=', False), ('date', '>=', self.date_from), ('date', '<=', self.date_to)]
        if self.employee_ids:
            domain.append(('employee_id', 'in', self.employee_ids.ids))
        if self.project_ids:
            domain.append(('project_id', 'in', self.project_ids.ids))
        return domain

    def _lines(self):
        """The timesheets this user may read (record rules apply: a plain user only gets their own)."""
        return self.env['account.analytic.line'].search(self._domain(), order='date, id')

    def action_print(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_('The start date must be before the end date.'))
        lines = self._lines()
        if not lines:
            raise UserError(_('No timesheets in this period.'))
        return self.env.ref('softwareservices_timesheets_connector.action_report_timesheet').report_action(lines)

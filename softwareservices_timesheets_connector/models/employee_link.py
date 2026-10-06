"""Gekoppelde werknemers (SPEC 5.1 §6.1): alleen voor deze werknemers schrijft de dienst uren. De dienst stuurt de lijst
(/links); de beheerder kan per werknemer 'Allowed' uitzetten als noodrem."""
from odoo import fields, models


class EmployeeLink(models.Model):
    _name = 'ss_timesheets.employee_link'
    _description = 'Employee linked to TimeViking'
    _order = 'employee_id'
    _rec_name = 'employee_id'

    employee_id = fields.Many2one('hr.employee', string='Employee', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='employee_id.company_id', string='Company')
    external_user = fields.Char('User in TimeViking', readonly=True)
    allowed = fields.Boolean('Allowed', default=True,
                             help='Off: the service may no longer write or change timesheets for this employee.')
    linked_at = fields.Datetime('Linked on', default=fields.Datetime.now, readonly=True)

    _employee_unique = models.Constraint('unique(employee_id)', 'This employee is already linked.')

"""Gekoppelde werknemers (SPEC 5.1 §6.1): alleen voor deze werknemers schrijft de dienst uren. De dienst stuurt de lijst
(/links); de beheerder kan per werknemer 'Toegelaten' uitzetten als noodrem."""
from odoo import fields, models


class EmployeeLink(models.Model):
    _name = 'ss_timesheets.employee_link'
    _description = 'Werknemer gekoppeld aan {{PRODUCT_NAME}}'
    _order = 'employee_id'
    _rec_name = 'employee_id'

    employee_id = fields.Many2one('hr.employee', string='Werknemer', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='employee_id.company_id', string='Bedrijf')
    external_user = fields.Char('Gebruiker in {{PRODUCT_NAME}}', readonly=True)
    allowed = fields.Boolean('Toegelaten', default=True,
                             help='Uit: de dienst mag voor deze werknemer geen uren meer schrijven of wijzigen.')
    linked_at = fields.Datetime('Gekoppeld op', default=fields.Datetime.now, readonly=True)

    _sql_constraints = [('employee_unique', 'unique(employee_id)', 'Deze werknemer is al gekoppeld.')]

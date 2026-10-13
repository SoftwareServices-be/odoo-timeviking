# Demo data for the store screenshots of the report and the invoice attachment (invented names, no personal data).
# Needs sale_timesheet and both modules. Run in `odoo-bin shell -c <conf> -d <db> --no-http < tools/seed_demo.py`.
# Prints DEMO_LINES=<ids> and DEMO_INVOICE=<id> for tools/screenshots.mjs. Meant for a throw-away databank.
import base64
import os
import random
from datetime import date, timedelta

random.seed(11)
company = env.company
company.write({'name': 'Fjord Consulting', 'street': 'Havenlaan 12', 'city': 'Gent', 'zip': '9000',
               'country_id': env.ref('base.be').id, 'email': 'info@fjord.example', 'phone': '+32 9 000 00 00',
               'external_report_layout_id': env.ref('web.external_layout_standard').id})
# invented logo of the fictional demo company (assets/demo_logo.svg -> .png), instead of Odoo's "Your logo"
_logo = os.path.join(os.environ.get('DEMO_ROOT', '.'), 'assets', 'demo_logo.png')
if os.path.exists(_logo):
    company.logo = base64.b64encode(open(_logo, 'rb').read())
env['res.users'].browse(2).write({'password': 'admin'})
Partner, Project, Task, Employee = env['res.partner'], env['project.project'], env['project.task'], env['hr.employee']
harbor = Partner.create({'name': 'Harbor Logistics', 'is_company': True, 'email': 'ap@harbor.example',
                         'street': 'Kaai 4', 'city': 'Antwerpen', 'zip': '2000', 'country_id': env.ref('base.be').id})
people = [Employee.create({'name': n, 'job_title': j}) for n, j in
          (('Astrid Berg', 'Consultant'), ('Leif Hansen', 'Developer'))]
project = Project.create({'name': 'Website relaunch', 'partner_id': harbor.id, 'allow_timesheets': True})
tasks = [Task.create({'name': t, 'project_id': project.id}) for t in ('Design', 'Development', 'Content')]
words = ['Workshop', 'Wireframes', 'Review with customer', 'Implementation', 'Testing', 'Copywriting']
Line = env['account.analytic.line']
lines = Line
day = date(2026, 9, 1)
while day <= date(2026, 9, 30):
    if day.weekday() < 5:
        for emp in people:
            task = random.choice(tasks)
            vals = {'employee_id': emp.id, 'project_id': project.id, 'task_id': task.id, 'date': day,
                    'unit_amount': random.choice([1, 1.5, 2, 2.5, 3, 4]), 'name': random.choice(words)}
            if random.random() < 0.6:
                vals['ss_client_ref'] = f'demo-{day.isoformat()}-{emp.id}'      # logged in TimeViking
            lines |= Line.create(vals)
    day += timedelta(days=1)
product = env['product.product'].create({'name': 'Consulting (hours)', 'type': 'service', 'list_price': 95.0})
hours = sum(lines.mapped('unit_amount'))
invoice = env['account.move'].create({
    'move_type': 'out_invoice', 'partner_id': harbor.id, 'invoice_date': date(2026, 9, 30),
    'invoice_line_ids': [(0, 0, {'product_id': product.id, 'name': 'Website relaunch, September 2026',
                                 'quantity': hours, 'price_unit': 95.0})],
})
inverse = env['account.move']._fields['timesheet_ids'].inverse_name
lines.write({inverse: invoice.id})
invoice.action_post()
env.cr.commit()
astrid = lines.filtered(lambda l: l.employee_id == people[0])
print('DEMO_LINES=' + ','.join(map(str, astrid.ids)))
print('DEMO_INVOICE=%d' % invoice.id)
print('ATTACHED=%s' % invoice.ss_timesheet_report_id.name)

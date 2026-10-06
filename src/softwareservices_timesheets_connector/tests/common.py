import json
import os

from odoo.tests import TransactionCase

with open(os.path.join(os.path.dirname(__file__), 'ssts1_vectors.json'), encoding='utf-8') as f:
    VECTORS = json.load(f)

SECRET_B64 = VECTORS['secret_b64']
KEY_ID = 'k_test0001'


class ConnectorCase(TransactionCase):
    """Two companies, employees, projects and a paired connection that covers company A only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company_a = env.company
        cls.company_b = env['res.company'].create({'name': 'SS Bedrijf B'})
        Employee = env['hr.employee']
        cls.user_an = env['res.users'].create({'name': 'SS An', 'login': 'ss_an@example.com', 'email': 'ss_an@example.com',
                                               'groups_id': [(6, 0, [env.ref('base.group_user').id])]})
        cls.emp_an = Employee.create({'name': 'SS An', 'company_id': cls.company_a.id, 'user_id': cls.user_an.id,
                                      'work_email': 'ss_an@example.com'})
        cls.emp_bo = Employee.create({'name': 'SS Bo', 'company_id': cls.company_a.id, 'work_email': 'ss_bo@example.com'})
        cls.emp_cas = Employee.create({'name': 'SS Cas (niet gekoppeld)', 'company_id': cls.company_a.id})
        cls.emp_b = Employee.create({'name': 'SS Dirk B', 'company_id': cls.company_b.id})
        Project = env['project.project']
        cls.project = Project.create({'name': 'SS Web', 'company_id': cls.company_a.id, 'allow_timesheets': True,
                                      'privacy_visibility': 'followers'})
        cls.project_no_ts = Project.create({'name': 'SS Geen uren', 'company_id': cls.company_a.id, 'allow_timesheets': False})
        cls.project_b = Project.create({'name': 'SS Intern B', 'company_id': cls.company_b.id, 'allow_timesheets': True})
        cls.task = env['project.task'].create({'name': 'SS Ontwerp', 'project_id': cls.project.id})
        cls.task_b = env['project.task'].create({'name': 'SS Taak B', 'project_id': cls.project_b.id})
        Link = env['ss_timesheets.employee_link']
        cls.link_an = Link.create({'employee_id': cls.emp_an.id, 'external_user': '1 an@example.com'})
        cls.link_bo = Link.create({'employee_id': cls.emp_bo.id, 'external_user': '2 bo@example.com', 'allowed': False})
        cls.link_b = Link.create({'employee_id': cls.emp_b.id, 'external_user': '3 dirk@example.com'})
        cls.connection = env['ss_timesheets.connection'].get_connection()
        cls.connection.write({'state': 'paired', 'key_id': KEY_ID, 'secret': SECRET_B64, 'service_url': 'https://dienst.example.com',
                              'company_ids': [(6, 0, [cls.company_a.id])]})
        cls.connector = env.ref('softwareservices_timesheets_connector.user_connector')

    def service(self):
        from ..service import ConnectorService
        return ConnectorService(self.env, self.connection)

    def run_endpoint(self, endpoint, body):
        from .. import service
        return service.run(self.env, self.connection, endpoint, body)

    def line_body(self, **extra):
        return {'client_ref': '1001', 'employee_id': self.emp_an.id, 'project_id': self.project.id, 'task_id': self.task.id,
                'date': '2026-09-21', 'hours': 1.5, 'name': 'Overleg', 'company_id': self.company_a.id, **extra}

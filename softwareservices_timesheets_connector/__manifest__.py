# The {{...}} placeholders are filled in by tools/build.py from product.json (the one place for names and links).
{
    'name': 'TimeViking Timesheet Sync',
    'summary': 'Sync timesheets, projects, tasks and time off with TimeViking, the fast timesheet app',
    'description': '',                     # the Apps store and the Apps menu show static/description/index.html
    'version': '18.0.1.2.0',
    'category': 'Services/Timesheets',
    'author': 'Software Services BV',
    'maintainer': 'Software Services BV',
    'website': 'https://timeviking.com',
    'support': 'support@timeviking.com',
    'license': 'LGPL-3',
    'price': 0,
    'currency': 'EUR',
    'images': ['static/description/banner.png'],
    'depends': ['hr_timesheet'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/cron.xml',
        'data/connector.xml',
        'views/connection_views.xml',
        'views/analytic_line_views.xml',
    ],
    'installable': True,
    'application': False,
}

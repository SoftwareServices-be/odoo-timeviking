# The {{...}} placeholders are filled in by tools/build.py from product.json (the one place for names and links).
{
    'name': '{{APP_NAME}}',
    'summary': '{{SUMMARY}}',
    'description': '',                     # the Apps store and the Apps menu show static/description/index.html
    'version': '{{VERSION}}',
    'category': '{{CATEGORY}}',
    'author': '{{AUTHOR}}',
    'maintainer': '{{AUTHOR}}',
    'website': '{{WEBSITE}}',
    'support': '{{SUPPORT_EMAIL}}',
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

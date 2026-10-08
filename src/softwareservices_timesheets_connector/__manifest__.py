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
    # Store carousel: the cover (banner) first, then the main screenshots.
    'images': [
        'static/description/banner.png',
        'static/description/screenshot_en_calendar.jpg',
        'static/description/screenshot_en_grid.jpg',
        'static/description/screenshot_en_mobile.jpg',
        'static/description/screenshot_en_odoo_connection.png',
        'static/description/screenshot_en_odoo_employees.png',
        'static/description/screenshot_en_pdf_template.jpg',
    ],
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

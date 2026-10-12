# Installed automatically when both {{APP_NAME}} and Sales Timesheet are installed. The {{...}} placeholders are
# filled in by tools/build.py from product.json.
{
    'name': 'Timesheets on Invoices',
    'summary': '{{PRODUCT_NAME}}: attach a timesheet report (PDF) to customer invoices and their email. '
               'Bridge between {{APP_NAME}} and Sales Timesheet',
    'description': '',
    'version': '{{VERSION}}',
    'category': '{{CATEGORY}}',
    'author': '{{AUTHOR}}',
    'maintainer': '{{AUTHOR}}',
    'website': '{{WEBSITE}}',
    'support': '{{SUPPORT_EMAIL}}',
    'license': 'LGPL-3',
    'price': 0,
    'currency': 'EUR',
    'depends': ['softwareservices_timesheets_connector', 'sale_timesheet'],
    'data': [
        'report/report_invoice_timesheet.xml',
        'views/account_move_views.xml',
        'views/connection_views.xml',
    ],
    'auto_install': True,
    'installable': True,
    'application': False,
}

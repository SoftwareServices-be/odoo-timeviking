# Installed automatically when both TimeViking Timesheet Sync and Sales Timesheet are installed. The {{...}} placeholders are
# filled in by tools/build.py from product.json.
{
    'name': 'Timesheets on Invoices',
    'summary': 'TimeViking: attach a timesheet report (PDF) to customer invoices and their email. '
               'Bridge between TimeViking Timesheet Sync and Sales Timesheet',
    'description': '',
    'version': '20.0.1.3.0',
    'category': 'Services/Timesheets',
    'author': 'Software Services BV',
    'maintainer': 'Software Services BV',
    'website': 'https://timeviking.com',
    'support': 'support@timeviking.com',
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

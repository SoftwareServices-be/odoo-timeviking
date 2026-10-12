# Changelog

The module version is `<odoo version>.<module version>`, for example `19.0.1.2.0`. The module version is the same for all
Odoo versions and lives in `product.json` (`MODULE_VERSION`).

## 1.3.0 (2026-10-12)

- Timesheet report (PDF): Timesheets › Reporting › Timesheet Report (PDF), and Print › Timesheet report (PDF) on the
  timesheet list. One page per employee in the company's document layout; hours logged in TimeViking are marked.
- Timesheets on invoices: new bridge module `softwareservices_timesheets_connector_sale` (installed automatically with
  Sales Timesheet). Confirming a customer invoice attaches a PDF with the hours it bills, and the invoice email sends it
  along. On by default (Settings › TimeViking › Connection), plus an "Attach Timesheets" button on the invoice.
- Store listing: report and invoice attachment, new screenshots, summary with the main search terms.

## 1.2.2 (2026-10-12)

- Store listing: TimeViking in 6 languages (Spanish and Italian added), link to the demo video, a clear Odoo Online
  section (no module there, but the API connection works on the Custom plan), sharper summary, more FAQ.

## 1.2.1 (2026-10-06)

- English is now the source language of all screens, menus, field labels, help texts, messages, the scheduled action and the
  security group. New `i18n/` with the template and Dutch (`nl`), French (`fr`) and German (`de`) translations.
- English store screenshots.

## 1.2.0 (2026-10-06)

- Own repository, one source for Odoo 18, 19 and 20 (`tools/build.py`), version branches for the Odoo Apps store.
- Apps store listing: English description, icon, cover image and screenshots; product name, store name, website and
  support address in one place (`product.json`). Product name TimeViking, store name "TimeViking Timesheet Sync".
- The service can no longer approve time off: approving and refusing stay in Odoo. Refuse and reopen only work on
  approved time off, to withdraw it.

## 1.1.0 (2026-10-04)

- Work schedules, public holidays, time-off types, allocations and balances; request, change and withdraw time off for
  the allowed employees. The technical user gets Time Off: Officer and stays inactive.
- The signature is checked before the nonce is stored.

## 1.0.0 (2026-10-03)

- Pairing with a code, signed requests (HMAC-SHA256, timestamp and nonce), projects, tasks, employees and timesheet
  lines for allowed employees, per company.

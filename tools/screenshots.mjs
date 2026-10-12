#!/usr/bin/env node
// Store screenshots of the timesheet report and the invoice attachment, from a running test Odoo 19 with the demo
// data of tools/seed_demo.py.
//
//   (ln -s ../softwareservices-odoo-timesheets/node_modules node_modules) ODOO_URL=http://127.0.0.1:8119 \
//   DEMO_LINES=1,2,3 DEMO_INVOICE=7 node tools/screenshots.mjs
//
// The report PDF comes from Odoo (/report/pdf/..., wkhtmltopdf); page 1 becomes a PNG with PyMuPDF.
import { chromium } from 'playwright-core';
import { execFileSync, execSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const OUT = path.join(ROOT, 'src', 'softwareservices_timesheets_connector', 'static/description');
const ODOO = (process.env.ODOO_URL || 'http://127.0.0.1:8119').replace(/\/$/, '');
const B = process.env.BROWSER_DIR || path.join(ROOT, '..', 'softwareservices-odoo-timesheets', 'var/browser');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'shots-'));
const env = process.env;

const exe = execSync(`find ${os.homedir()}/.cache/ms-playwright -path '*chrome-linux64/chrome' -type f | head -1`).toString().trim();
const libs = execSync(`find ${B}/root -name "*.so*" -printf '%h\\n' | sort -u | tr '\\n' ':'`).toString();
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox', '--font-render-hinting=none', '--lang=en-US'],
    env: { ...env, LD_LIBRARY_PATH: libs, FONTCONFIG_FILE: path.join(B, 'fonts.conf') } });
const page = await browser.newPage({ viewport: { width: 1440, height: 860 }, locale: 'en-US' });
await page.goto(`${ODOO}/web/login`);
await page.fill('input[name=login]', 'admin');
await page.fill('input[name=password]', 'admin');
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

const res = await page.request.get(`${ODOO}/report/pdf/softwareservices_timesheets_connector.report_timesheet/${env.DEMO_LINES}`);
const pdf = path.join(TMP, 'report.pdf');
fs.writeFileSync(pdf, await res.body());
execFileSync('python3', ['-c', `import sys, pymupdf
doc = pymupdf.open(sys.argv[1]); page = doc[0]; r = page.rect
clip = pymupdf.Rect(0, 0, r.width, r.width * 1.1)
page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), clip=clip).save(sys.argv[2])`, pdf, path.join(OUT, 'screenshot_en_odoo_report.png')]);
console.log('report');

// attach again now that the server runs (the seed rendered it without a server, so without styles)
await page.request.post(`${ODOO}/web/dataset/call_kw/account.move/action_ss_attach_timesheet_report`, {
    data: { jsonrpc: '2.0', method: 'call', params: { model: 'account.move', method: 'action_ss_attach_timesheet_report',
        args: [[Number(env.DEMO_INVOICE)]], kwargs: {} } } });
await page.goto(`${ODOO}/odoo/action-account.action_move_out_invoice_type/${env.DEMO_INVOICE}`);
await page.waitForSelector('.o_form_view', { timeout: 60000 });
await page.waitForTimeout(9000);
await page.screenshot({ path: path.join(OUT, 'screenshot_en_odoo_invoice.png') });
console.log('invoice');
await browser.close();

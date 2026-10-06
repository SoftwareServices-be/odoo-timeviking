# TimeViking Timesheet Sync

The Odoo module `softwareservices_timesheets_connector`: a free connector between Odoo Timesheets and TimeViking, our
timesheet service (a paid SaaS). One source, built for Odoo 18, 19 and 20, and published on the
Odoo Apps store.

## Layout

```
main branch (this one)                      18.0 / 19.0 / 20.0 branches (generated, never edit)
├── product.json      names and links       ├── softwareservices_timesheets_connector/   the built module
├── src/softwareservices_timesheets_connector/   ├── README.md
│     the module, written for Odoo 18       └── LICENSE
├── tools/build.py    build, check, release
├── tools/render_assets.py  icon and banner
├── assets/           HTML sources of icon and banner
├── tests/            tests of the build
└── dist/             local build output (not in git)
```

**Why a branch per Odoo version.** The Odoo Apps store registers a repository per Odoo version with an SSH URL that ends in
the branch, for example `ssh://git@github.com/<owner>/softwareservices-timesheets-odoo#19.0`, and scans the root of that
branch for module folders. That is the same layout as the Odoo and OCA repositories. Odoo.sh and plain `git clone -b 19.0`
users get a ready-to-use addons folder too.

**Why one source.** The three versions differ in a handful of mechanical points (`groups_id` → `group_ids` and
`models.Constraint` from 19, `ir.access.csv` and `get_str` from 20). `tools/build.py` applies them, so a fix is made once,
in `src/`, and `release` writes all three branches. Each rule in `convert()` has a test.

**One place for names.** `product.json` holds the product name, the store name of
the app, the summary, website, support address, author, category and the module version. `src/` only contains
placeholders such as `{{PRODUCT_NAME}}`; the build fills them in (a value may use another placeholder) and fails on an
unknown one. While `NAME_FINAL` is `false`, `release` refuses to write the store branches. To rename: set `PRODUCT_NAME`
(and `APP_NAME` if needed), run `tools/render_assets.py` (the banner shows the name), retake the screenshots, test,
release.

### Store name and the Odoo trademark

- The store name is **TimeViking Timesheet Sync** (25 characters, the maximum), not "TimeViking Connector": the store
  searches the name first and visitors type "timesheet", and "Sync" says what the module does. The vendor guidelines ask for an explicit name of at most 25
  characters, without adjectives or the vendor's company name; the build checks the length, "Odoo" and the company name.
  "Timesheet" in the name and the summary is what store visitors search for.
- "Odoo" is a registered trademark. Odoo publishes no naming rules for third-party apps; its brand page
  (odoo.com/page/brand-assets) only says how to write the word (capital O, never "ODOO" or "Odoos") and reserves the partner
  logos for official partners. So the name does not contain "Odoo" (every app on the store is for Odoo anyway, and a
  product name built on someone else's mark is a risk), while the summary and description use "Odoo Timesheets"
  descriptively, to say what the module connects to. No partner claims, no Odoo logo.
- The technical name `softwareservices_timesheets_connector` stays: changing it would mean uninstalling and reinstalling
  for every existing install, and store visitors never see it.

## Develop

Requirements: Python 3.8+ (standard library only) and `pytest` for the tests.

```sh
python3 tools/build.py dist            # dist/18.0, dist/19.0, dist/20.0 (an addons folder per version)
python3 tools/build.py zip 19          # dist/softwareservices_timesheets_connector-19.0.zip
python3 tools/build.py check           # store checks for every version, and are the branches up to date?
python3 -m pytest                      # tests of the build
```

Change only `src/`, `product.json`, `tools/` and `assets/`. A difference between Odoo versions that the build does not
know yet goes into `convert()` in `tools/build.py`, with a test in `tests/test_build.py`.

Optional: `pip install pre-commit && pre-commit install` (whitespace, JSON/XML, flake8, `build.py check`).

## Test on every Odoo version

The main project (`softwareservices-odoo-timesheets`, next to this repo) has test Odoo's for 18, 19 and 20 without Docker
(`scripts/odoo_dev.sh`, see its README and `docs/odoo-testomgeving.md`). It builds `dist/<version>.0` from this repo and puts it
in the addons path. Run the module's own Odoo tests (Odoo must be stopped; one Odoo at a time, hence the lock):

```sh
cd ../softwareservices-odoo-timesheets
for v in 18 19 20; do
  flock var/odoo/.lock bash -c "scripts/odoo_dev.sh $v module-test"
done
```

Then the end-to-end test with the service against a running Odoo:

```sh
scripts/odoo_dev.sh 19 start
ODOO_REAL=19 scripts/test.sh tests/real_odoo/test_module_real.py
scripts/odoo_dev.sh 19 stop
```

The main project finds this repo through `ODOO_MODULE_REPO`, `odoo_module/` inside the main project (its Docker image), or the
sibling folder `../softwareservices-timesheets-odoo`. It builds the downloadable zips with `tools/build.py` at runtime, so a
change here is in the app's download after a restart.

## Store images

- `static/description/icon.png`, 256×256: from `assets/icon.html`, the TimeViking app tile (`assets/brand/`, copied from
  the main project's `docs/merk/`).
- `static/description/banner.png`, 560×280, the cover image (`images` in the manifest): from `assets/banner.html`, with
  the TimeViking wordmark and palette (Fjord, Staal, Koper).
- `static/description/screenshot_1..3.png`: the week calendar and the PDF template editor of the service, and the
  connection screen in Odoo 19. Take them again with a headless browser after UI changes (1440×900).

```sh
SHOT=../softwareservices-odoo-timesheets/var/browser/shot.sh python3 tools/render_assets.py   # or CHROME=/path/to/chromium
```

The description (`static/description/index.html`) must stay in English, without JavaScript, forms, external images or
external links (only `mailto:`); the link to the service is the `website` field of the manifest. No claims about Odoo
licences or prices, no "official", "certified" or "partner".

## Release

1. Raise `MODULE_VERSION` in `product.json` and add a section to `CHANGELOG.md`. The product name must be final
   (`NAME_FINAL`); `--allow-working-name` is only for test branches in a scratch clone.
2. `python3 -m pytest`, `python3 tools/build.py check --no-branches` and the module tests on 18, 19 and 20.
3. Commit on `main`, then write the version branches:

   ```sh
   python3 tools/build.py release          # one commit per changed branch: "19.0.1.2.0: build from main <sha>"
   python3 tools/build.py check            # now also "branch up to date"
   git push origin main 18.0 19.0 20.0
   git tag v1.2.0 && git push origin v1.2.0
   ```

`release` uses git plumbing: it never checks out a branch or touches your working tree.

## Publish on the Odoo Apps store

1. Push this repository to GitHub (public, or private with read access for the GitHub user `online-odoo`).
2. Sign in on [apps.odoo.com](https://apps.odoo.com/apps/upload) with the Software Services odoo.com account.
3. Register one URL per version: `ssh://git@github.com/<owner>/softwareservices-timesheets-odoo#18.0`, then `#19.0` and `#20.0`.
4. The store scans the branch and shows the app with the manifest, `index.html`, the icon and the cover image. It rescans
   after every push to a registered branch.
5. The module is free (`'price': 0`). Check the vendor guidelines before publishing: description and screenshots in English,
   the external service clearly announced (done in the summary and description), opt-in before data leaves Odoo (pairing).

Before the first publication: set the production URL (`WEBSITE`) in `product.json`, translate the module's interface (now Dutch) to English with `i18n/nl.po`, and retake the screenshots in
English with the new name.

## License

LGPL-3, see `LICENSE`. © Software Services BV.

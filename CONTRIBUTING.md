# Contributing

1. Change only `src/` (the Odoo 18 source), `product.json` or `tools/`. Never edit the `18.0`, `19.0` or `20.0` branches by
   hand: `tools/build.py release` writes them.
2. Something that differs between Odoo versions? Add a rule to `convert()` in `tools/build.py` and a test in
   `tests/test_build.py`.
3. Run `python3 -m pytest` and `python3 tools/build.py check`, and the module tests on every Odoo version (see README).
4. Raise `MODULE_VERSION` in `product.json` for every change that reaches users, and add a line to `CHANGELOG.md`.
5. Keep the store description (`static/description/index.html`) in English, without JavaScript, forms or external images.

Questions: info@softwareservices.be.

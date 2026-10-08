#!/usr/bin/env python3
"""Render the store images from the HTML sources in assets/ (with the names from product.json).

  tools/render_assets.py                  icon.png (256x256) and banner.png (560x280) into src/<module>/static/description/

It needs a headless Chromium. Set one of:
  SHOT=/path/to/shot.sh     a script called as `shot.sh <url> <png> <width,height>` (the main project has var/browser/shot.sh)
  CHROME=/path/to/chrome    a Chromium or chrome-headless-shell binary
Without either, the sibling main project's var/browser/shot.sh is used when it exists.
Screenshots (screenshot_<lang>_*.png/jpg) come from a test Odoo and the main project's site images; see README.md.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('build', ROOT / 'tools' / 'build.py')
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

TARGETS = {'icon.html': ('icon.png', 256, 256), 'banner.html': ('banner.png', 560, 280)}
DEFAULT_SHOT = ROOT.parent / 'softwareservices-odoo-timesheets' / 'var' / 'browser' / 'shot.sh'


def screenshot(url, png, width, height):
    shot, chrome = os.environ.get('SHOT'), os.environ.get('CHROME')
    if not shot and not chrome and DEFAULT_SHOT.exists():
        shot = str(DEFAULT_SHOT)
    if shot:
        cmd = [shot, url, str(png), f'{width},{height}']
    elif chrome:
        cmd = [chrome, '--headless', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
               f'--screenshot={png}', f'--window-size={width},{height}', url]
    else:
        raise SystemExit('No headless Chromium: set SHOT or CHROME (see the docstring).')
    subprocess.run(cmd, check=False)
    if not Path(png).exists():
        raise SystemExit(f'screenshot failed: {png}')


def main():
    out = build.source_dir() / 'static' / 'description'
    out.mkdir(parents=True, exist_ok=True)
    for name, (png, w, h) in TARGETS.items():
        html = build.render((ROOT / 'assets' / name).read_text(encoding='utf-8'), build.SOURCE_VERSION)
        page = ROOT / 'assets' / f'_render_{name}'          # next to the source, so brand/... resolves
        page.write_text(html, encoding='utf-8')
        target = out / png
        try:
            if target.exists():
                target.unlink()
            screenshot(page.as_uri(), target, w, h)
        finally:
            page.unlink()
        print(target)
    return 0


if __name__ == '__main__':
    sys.exit(main())

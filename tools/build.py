#!/usr/bin/env python3
"""Build the Odoo module for every supported Odoo version from one source.

The source in ``src/`` is written for Odoo 18. Odoo 19 and 20 versions are derived from it mechanically
(see ``convert``), and the ``{{...}}`` placeholders are filled in from ``product.json``.

  tools/build.py dist [18 19 20]          write dist/<version>.0/<module>/ (for an Odoo addons path)
  tools/build.py zip [--out DIR] [...]    write <module>-<version>.0.zip files (default: dist/)
  tools/build.py check [--no-branches]    validate the build; compare the version branches if they exist
  tools/build.py release [18 19 20]       commit the build to the branches 18.0, 19.0 and 20.0

``release`` never touches your working tree: it writes the branch commits with git plumbing.
The main project imports this file (``build_tree``, ``build_zip``) to serve the module as a download.
Only the standard library is used, so any Python 3.8+ can run it.
"""
import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = 'softwareservices_timesheets_connector'
SOURCE_VERSION = '18'
VERSIONS = ('18', '19', '20')
SKIP_DIRS = {'__pycache__'}
SKIP_SUFFIXES = {'.pyc', '.pyo'}
RENDER_SUFFIXES = {'.py', '.xml', '.csv', '.html', '.rst', '.txt', '.md'}
CONVERT_SUFFIXES = {'.py', '.xml', '.csv'}
ZIP_DATE = (2026, 10, 4, 0, 0, 0)        # fixed, so the same source always gives the same zip
TOKEN = re.compile(r'\{\{([A-Z_]+)\}\}')
APP_NAME_MAX = 25                         # Odoo Apps vendor guidelines: "no more than 25 characters"
REQUIRED_FILES = ('__manifest__.py', 'static/description/icon.png', 'static/description/index.html')


def source_dir():
    return ROOT / 'src' / MODULE


def product():
    """product.json without comments: the values for the {{...}} placeholders."""
    with open(ROOT / 'product.json', encoding='utf-8') as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith('_')}


def module_version():
    return product()['MODULE_VERSION']


def full_version(version):
    return f'{version}.0.{module_version()}'


def zip_name(version):
    return f'{MODULE}-{version}.0.zip'


def _check_version(version):
    version = str(version).split('.')[0]
    if version not in VERSIONS:
        raise ValueError(f'unknown Odoo version: {version} (supported: {", ".join(VERSIONS)})')
    return version


# --- placeholders ---------------------------------------------------------------------------------------------

def render(text, version, values=None):
    """Fill in {{KEY}} from product.json (plus VERSION). An unknown placeholder is an error, never left behind."""
    values = dict(values or product())
    values['VERSION'] = f'{version}.0.{values["MODULE_VERSION"]}'

    def repl(m):
        if m.group(1) not in values:
            raise KeyError(f'unknown placeholder {{{{{m.group(1)}}}}}')
        return str(values[m.group(1)])
    for _ in range(5):                    # a value may itself use placeholders, e.g. APP_NAME = '{{PRODUCT_NAME}} Timesheet Sync'
        text, n = TOKEN.subn(repl, text)
        if not n:
            break
    return text


# --- Odoo 18 -> 19 / 20 ---------------------------------------------------------------------------------------

def rename(rel, version):
    if int(version) >= 20 and rel == 'security/ir.model.access.csv':
        return 'security/ir.access.csv'
    return rel


def _access_csv_20(text):
    """ir.model.access.csv (18/19) -> ir.access.csv (20): one `operation` column instead of four flags, model by name."""
    rows = [line.split(',') for line in text.strip().splitlines()]
    out = ['id,name,model_id,group_id/id,operation,domain']
    for rid, name, model, group, rd, wr, cr, ul in rows[1:]:
        ops = ''.join(letter for letter, flag in (('c', cr), ('r', rd), ('u', wr), ('d', ul)) if flag == '1')
        out.append(f"{rid},{name},{model.replace('model_ss_timesheets_', 'ss_timesheets.')},{group},{ops},")
    return '\n'.join(out) + '\n'


def convert(rel, text, version):
    """The known API differences with Odoo 18. Add a rule here when the source needs something new."""
    v, n = version, int(version)
    if v == SOURCE_VERSION:
        return text
    text = text.replace(f'Odoo {SOURCE_VERSION} standaard', f'Odoo {v} standaard')                 # comment in the controller
    text = text.replace(f"startswith('{SOURCE_VERSION}')", f"startswith('{v}')")                    # tests
    text = text.replace(f"startswith('{SOURCE_VERSION}.0.')", f"startswith('{v}.0.')")
    text = text.replace(f"[:5], '{SOURCE_VERSION}.0.'", f"[:5], '{v}.0.'")
    if n >= 19:
        text = text.replace('groups_id', 'group_ids')
        text = re.sub(r"_sql_constraints = \[\('(\w+)', ('[^']*'), ('[^']*')\)\]", r"_\1 = models.Constraint(\2, \3)", text)
    if n >= 20:
        text = text.replace('security/ir.model.access.csv', 'security/ir.access.csv')
        if rel == 'security/ir.model.access.csv':
            text = _access_csv_20(text)
        text = text.replace('.get_param(', '.get_str(')
    return text


# --- build ----------------------------------------------------------------------------------------------------

def source_files(src=None):
    src = Path(src or source_dir())
    for path in sorted(src.rglob('*')):
        rel = path.relative_to(src)
        if path.is_file() and not (SKIP_DIRS & set(rel.parts)) and path.suffix not in SKIP_SUFFIXES:
            yield rel.as_posix(), path


def build_tree(version, src=None, values=None):
    """{path inside the module: bytes} for one Odoo version."""
    version = _check_version(version)
    values = values or product()
    files = {}
    for rel, path in source_files(src):
        data = path.read_bytes()
        if path.suffix in RENDER_SUFFIXES:
            text = render(data.decode('utf-8'), version, values)
            if path.suffix in CONVERT_SUFFIXES:
                text = convert(rel, text, version)
            data = text.encode('utf-8')
        files[rename(rel, version)] = data
    return files


def manifest(tree):
    return ast.literal_eval(tree['__manifest__.py'].decode('utf-8'))


def validate(version, tree=None):
    """Problems that would break an install or the Odoo Apps listing (empty list = fine)."""
    version = _check_version(version)
    tree = tree if tree is not None else build_tree(version)
    problems = [f'missing {rel}' for rel in REQUIRED_FILES if rel not in tree]
    if '__manifest__.py' not in tree:
        return problems
    man = manifest(tree)
    if man.get('version') != full_version(version):
        problems.append(f"manifest version {man.get('version')!r} != {full_version(version)!r}")
    name = man.get('name', '')
    if not name or len(name) > APP_NAME_MAX:
        problems.append(f'app name {name!r} must have 1-{APP_NAME_MAX} characters (vendor guidelines)')
    if 'odoo' in name.lower():
        problems.append(f'app name {name!r} must not contain the Odoo trademark')
    if 'software services' in name.lower():
        problems.append(f'app name {name!r} must not contain the company name (vendor guidelines)')
    if man.get('license') != 'LGPL-3':
        problems.append('license must be LGPL-3')
    for key in ('summary', 'author', 'website', 'support', 'category'):
        if not man.get(key):
            problems.append(f'manifest has no {key}')
    for img in man.get('images', []):
        if img not in tree:
            problems.append(f'manifest image {img} is missing')
    for rel in man.get('data', []):
        if rel not in tree:
            problems.append(f'manifest data file {rel} is missing')
    for rel, data in tree.items():
        if Path(rel).suffix in RENDER_SUFFIXES and TOKEN.search(data.decode('utf-8')):
            problems.append(f'placeholder left in {rel}')
    return problems


def build_zip(version):
    """The zip as bytes: the module folder as the only top-level entry, no bytecode, reproducible."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel, data in build_tree(version).items():
            info = zipfile.ZipInfo(f'{MODULE}/{rel}', ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def write_dist(version, out=None):
    """dist/<version>.0/<module>/, rebuilt from scratch (stale files removed)."""
    version = _check_version(version)
    base = Path(out or ROOT / 'dist') / f'{version}.0' / MODULE
    tree = build_tree(version)
    if base.exists():
        for p in sorted(base.rglob('*'), reverse=True):
            rel = p.relative_to(base).as_posix()
            if p.is_file() and rel not in tree:
                p.unlink()
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()
    for rel, data in tree.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
    return base


# --- version branches -----------------------------------------------------------------------------------------

def branch_name(version):
    return f'{_check_version(version)}.0'


def branch_readme(version):
    values = product()
    return (f"# {values['APP_NAME']} for Odoo {version}.0\n\n"
            f"This branch is generated by `tools/build.py release` from the `main` branch. Do not edit it by hand.\n\n"
            f"Copy the folder `{MODULE}` into your Odoo addons path, update the apps list and install "
            f"\"{values['APP_NAME']}\". It connects Odoo to {values['PRODUCT_NAME']} ({values['WEBSITE']}).\n\n"
            f"Support: {values['SUPPORT_EMAIL']}. License: LGPL-3 (see LICENSE).\n").encode('utf-8')


def branch_tree(version):
    """{path in the branch: bytes}: the module folder at the root (as the Odoo Apps store reads it), README and LICENSE."""
    files = {f'{MODULE}/{rel}': data for rel, data in build_tree(version).items()}
    files['README.md'] = branch_readme(version)
    files['LICENSE'] = (ROOT / 'LICENSE').read_bytes()
    return files


def _git(repo, *args, env=None, data=None):
    res = subprocess.run(['git', '-C', str(repo), *args], input=data, capture_output=True,
                         env={**os.environ, **(env or {})}, check=True)
    return res.stdout.decode().strip()


def _write_git_tree(repo, files):
    with tempfile.TemporaryDirectory() as tmp:
        env = {'GIT_INDEX_FILE': os.path.join(tmp, 'index')}
        _git(repo, 'read-tree', '--empty', env=env)
        lines = []
        for rel, data in sorted(files.items()):
            blob = _git(repo, 'hash-object', '-w', '--stdin', data=data)
            lines.append(f'100644 {blob}\t{rel}')
        _git(repo, 'update-index', '--add', '--index-info', env=env, data=('\n'.join(lines) + '\n').encode())
        return _git(repo, 'write-tree', env=env)


def _branch_head(repo, branch):
    try:
        return _git(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}')
    except subprocess.CalledProcessError:
        return None


def branch_differs(version, repo=None):
    """None if the branch does not exist, else True/False: does the branch hold something other than the build?"""
    repo = repo or ROOT
    head = _branch_head(repo, branch_name(version))
    if head is None:
        return None
    return _git(repo, 'rev-parse', f'{head}^{{tree}}') != _write_git_tree(repo, branch_tree(version))


def release(version, repo=None, message=None, allow_working_name=False):
    """Commit the build to branch <version>.0 (created when missing). Returns the new commit, or None if unchanged.

    The branches are what the Odoo Apps store publishes, so this refuses while product.json has NAME_FINAL false."""
    repo = repo or ROOT
    version = _check_version(version)
    branch = branch_name(version)
    if not (product().get('NAME_FINAL') or allow_working_name):
        raise SystemExit(f'{branch}: not released, the product name is a working name (NAME_FINAL in product.json)')
    problems = validate(version)
    if problems:
        raise SystemExit(f'{branch}: not released, ' + '; '.join(problems))
    tree = _write_git_tree(repo, branch_tree(version))
    head = _branch_head(repo, branch)
    if head and _git(repo, 'rev-parse', f'{head}^{{tree}}') == tree:
        return None
    try:
        source = _git(repo, 'rev-parse', '--short', 'HEAD')
    except subprocess.CalledProcessError:
        source = 'uncommitted'
    msg = message or f'{full_version(version)}: build from main {source}'
    args = ['commit-tree', tree, '-m', msg] + (['-p', head] if head else [])
    commit = _git(repo, *args)
    _git(repo, 'update-ref', f'refs/heads/{branch}', commit, *([head] if head else []))
    return commit


# --- command line ---------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('dist', 'zip', 'check', 'release'))
    ap.add_argument('versions', nargs='*', default=list(VERSIONS))
    ap.add_argument('--out', help='output directory (dist and zip)')
    ap.add_argument('-m', '--message', help='commit message for release')
    ap.add_argument('--no-branches', action='store_true', help='check: do not compare the version branches')
    ap.add_argument('--allow-working-name', action='store_true', help='release: also while NAME_FINAL is false (test branches)')
    args = ap.parse_args(argv)
    versions = [_check_version(v) for v in args.versions]
    status = 0
    for v in versions:
        if args.command == 'dist':
            print(write_dist(v, args.out))
        elif args.command == 'zip':
            out = Path(args.out or ROOT / 'dist')
            out.mkdir(parents=True, exist_ok=True)
            (out / zip_name(v)).write_bytes(build_zip(v))
            print(out / zip_name(v))
        elif args.command == 'check':
            problems = validate(v)
            differs = None if args.no_branches else branch_differs(v)
            if differs:
                problems.append(f'branch {branch_name(v)} is behind main (run tools/build.py release)')
            state = 'no branch yet' if differs is None else 'branch up to date' if not differs else 'branch differs'
            print(f'{v}.0: ' + ('ok, ' + state if not problems else '; '.join(problems)))
            status |= bool(problems)
        elif args.command == 'release':
            commit = release(v, message=args.message, allow_working_name=args.allow_working_name)
            print(f'{branch_name(v)}: ' + (f'committed {commit[:10]}' if commit else 'unchanged'))
    return status


if __name__ == '__main__':
    sys.exit(main())

"""Tests for tools/build.py: one source, three Odoo versions, Apps store metadata and the version branches."""
import ast
import importlib.util
import io
import json
import re
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('build', ROOT / 'tools' / 'build.py')
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

MODULE = build.MODULE
PREFIX = MODULE + '/'


def names(version):
    return zipfile.ZipFile(io.BytesIO(build.build_zip(version))).namelist()


def read(version, rel):
    return build.build_tree(version)[rel].decode()


def png_size(data):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    return struct.unpack('>II', data[16:24])


# --- the build ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('version', build.VERSIONS)
def test_build_is_valid_for_the_store(version):
    assert build.validate(version) == []


@pytest.mark.parametrize('version', build.VERSIONS)
def test_zip_has_one_folder_and_no_bytecode(version):
    entries = names(version)
    assert {n.split('/')[0] for n in entries} == {MODULE}
    assert PREFIX + '__manifest__.py' in entries and PREFIX + 'tests/test_service.py' in entries
    assert PREFIX + 'static/description/icon.png' in entries and PREFIX + 'static/description/index.html' in entries
    assert not [n for n in entries if '__pycache__' in n or n.endswith(('.pyc', '.pyo'))]
    assert build.zip_name(version) == f'{MODULE}-{version}.0.zip'


def test_zip_is_reproducible():
    assert build.build_zip('19') == build.build_zip('19')


def test_known_differences_per_version():
    mv = build.module_version()
    assert re.fullmatch(r'\d+\.\d+\.\d+', mv)
    for v in build.VERSIONS:
        assert build.manifest(build.build_tree(v))['version'] == f'{v}.0.{mv}'
    assert 'security/ir.model.access.csv' in read('18', '__manifest__.py')
    assert 'groups_id' in read('18', 'security/security.xml') and '_sql_constraints' in read('18', 'models/nonce.py')
    assert "vals['groups_id']" in read('18', 'models/connection.py')
    for v in ('19', '20'):
        assert 'group_ids' in read(v, 'security/security.xml') and 'groups_id' not in read(v, 'security/security.xml')
        assert 'models.Constraint(' in read(v, 'models/nonce.py') and '_sql_constraints' not in read(v, 'models/nonce.py')
        assert "vals['group_ids']" in read(v, 'models/connection.py') and 'groups_id' not in read(v, 'models/connection.py')
    assert 'security/ir.model.access.csv' in build.build_tree('19') and 'security/ir.access.csv' not in build.build_tree('19')
    assert 'security/ir.access.csv' in build.build_tree('20') and 'security/ir.model.access.csv' not in build.build_tree('20')
    assert 'ir.access.csv' in read('20', '__manifest__.py')
    assert 'operation' in read('20', 'security/ir.access.csv').splitlines()[0]
    assert '.get_str(' in read('20', 'models/connection.py') and '.get_param(' in read('19', 'models/connection.py')


@pytest.mark.parametrize('version', build.VERSIONS)
def test_no_other_version_numbers_leak(version):
    other = [v for v in build.VERSIONS if v != version]
    for rel, data in build.build_tree(version).items():
        if rel.endswith('.py'):
            assert not re.search(rf"startswith\('({'|'.join(other)})", data.decode()), rel


def test_unknown_version_is_refused():
    with pytest.raises(ValueError):
        build.build_zip('17')


def test_write_dist_removes_stale_files(tmp_path):
    base = build.write_dist('19', tmp_path)
    (base / 'stale.py').write_text('x')
    build.write_dist('19', tmp_path)
    assert not (base / 'stale.py').exists() and (base / '__manifest__.py').is_file()
    assert (tmp_path / '19.0' / MODULE / 'security' / 'ir.model.access.csv').is_file()


# --- the one place for names (product.json) -------------------------------------------------------------------

def test_product_name_lives_only_in_product_json():
    values = build.product()
    for rel, path in build.source_files():
        if path.suffix in build.RENDER_SUFFIXES:
            assert values['PRODUCT_NAME'] not in path.read_text(encoding='utf-8'), rel
    tree = build.build_tree('18')
    assert values['PRODUCT_NAME'] in read('18', 'views/connection_views.xml')
    assert values['PRODUCT_NAME'] in read('18', 'static/description/index.html')
    assert build.manifest(tree)['name'] == build.render(values['APP_NAME'], '18')


def test_renaming_the_product_changes_every_file():
    values = {**build.product(), 'PRODUCT_NAME': 'Acme Hours', 'APP_NAME': 'Acme Hours Connector'}
    tree = build.build_tree('20', values=values)
    assert build.manifest(tree)['name'] == 'Acme Hours Connector'
    assert 'Acme Hours' in tree['views/connection_views.xml'].decode()
    assert 'Acme Hours' in tree['static/description/index.html'].decode()
    assert not [rel for rel, d in tree.items() if rel.endswith('.py') and build.product()['PRODUCT_NAME'] in d.decode()]


def test_values_may_use_placeholders():
    values = {**build.product(), 'PRODUCT_NAME': 'Hourjot', 'APP_NAME': '{{PRODUCT_NAME}} Timesheet Sync'}
    man = build.manifest(build.build_tree('19', values=values))
    assert man['name'] == 'Hourjot Timesheet Sync' and 'Hourjot' in man['summary']
    assert build.validate('19', build.build_tree('19', values=values)) == []


def test_unknown_placeholder_is_an_error():
    with pytest.raises(KeyError):
        build.render('{{NOPE}}', '18')


def test_validate_catches_store_problems():
    tree = build.build_tree('18')
    man = build.manifest(tree)
    bad = dict(tree)
    bad['__manifest__.py'] = repr({**man, 'name': 'Odoo Timesheets Super Connector Pro'}).encode()
    problems = build.validate('18', bad)
    assert any('25 characters' in p for p in problems) and any('trademark' in p for p in problems)
    bad['__manifest__.py'] = repr({**man, 'name': 'Software Services Sync'}).encode()
    assert any('company name' in p for p in build.validate('18', bad))
    del bad['static/description/icon.png']
    assert 'missing static/description/icon.png' in build.validate('18', bad)


# --- Apps store metadata --------------------------------------------------------------------------------------

def test_manifest_metadata():
    man = build.manifest(build.build_tree('18'))
    assert man['license'] == 'LGPL-3' and man['price'] == 0 and man['currency'] == 'EUR'
    assert man['images'] == ['static/description/banner.png']
    assert man['category'] == 'Services/Timesheets' and man['author'] == 'Software Services BV'
    assert man['support'] == __import__('json').loads((ROOT / 'product.json').read_text())['SUPPORT_EMAIL'] and man['website'].startswith('https://')
    assert len(man['name']) <= build.APP_NAME_MAX and 'odoo' not in man['name'].lower()
    assert 'software services' not in man['name'].lower() and 'imesheet' in man['name']
    assert 'imesheet' in man['summary'] and man['application'] is False
    assert ast.literal_eval(repr(man)) == man


def test_images_have_store_sizes():
    tree = build.build_tree('18')
    assert png_size(tree['static/description/icon.png']) == (256, 256)
    assert png_size(tree['static/description/banner.png']) == (560, 280)
    shots = [rel for rel in tree if re.fullmatch(r'static/description/screenshot_\d+\.png', rel)]
    assert len(shots) >= 2


def test_index_html_follows_the_vendor_guidelines():
    html = read('18', 'static/description/index.html')
    assert '<script' not in html.lower() and '<form' not in html.lower() and '<iframe' not in html.lower()
    assert [h for h in re.findall(r'href="([^"]+)"', html) if not h.startswith('mailto:')] == []   # no external links
    tree = build.build_tree('18')
    images = re.findall(r'src="([^"]+)"', html)
    assert images
    for src in images:
        assert '://' not in src and f'static/description/{src}' in tree, src
    lower = html.lower()
    for text in (lower, build.manifest(build.build_tree('18'))['description'].lower()):
        assert 'licen' not in text and 'paid user' not in text        # no claim about saving Odoo licences
        assert 'partner' not in text                                   # no Odoo partnership claim


# --- version branches -----------------------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=True, text=True).stdout.strip()


def test_release_writes_branches_without_touching_the_worktree(tmp_path, monkeypatch):
    for key in ('GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL'):
        monkeypatch.setenv(key, 'test@example.com' if 'EMAIL' in key else 'Test')
    _git(tmp_path, 'init', '-q', '-b', 'main')
    _git(tmp_path, 'commit', '-q', '--allow-empty', '-m', 'start')
    assert build.branch_differs('19', tmp_path) is None
    if not build.product()['NAME_FINAL']:
        with pytest.raises(SystemExit):                                # no store branches with a working name
            build.release('19', repo=tmp_path)
    first = build.release('19', repo=tmp_path, message='test build', allow_working_name=True)
    assert first and build.branch_differs('19', tmp_path) is False
    files = _git(tmp_path, 'ls-tree', '-r', '--name-only', '19.0').splitlines()
    assert f'{MODULE}/__manifest__.py' in files and 'README.md' in files and 'LICENSE' in files
    assert f'{MODULE}/static/description/index.html' in files
    assert build.release('19', repo=tmp_path, allow_working_name=True) is None   # unchanged: no new commit
    assert _git(tmp_path, 'status', '--porcelain') == ''  # working tree untouched
    assert _git(tmp_path, 'rev-parse', '--abbrev-ref', 'HEAD') == 'main'
    manifest = _git(tmp_path, 'show', f'19.0:{MODULE}/__manifest__.py')
    assert ast.literal_eval(manifest)['version'] == build.full_version('19')

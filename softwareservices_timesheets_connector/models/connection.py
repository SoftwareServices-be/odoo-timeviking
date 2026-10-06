"""
De koppeling met TimeViking (SPEC 5.1 §4.2, §8): één record met de service-URL, de koppelcode, de status
en het gedeelde geheim. Het geheim staat alleen in velden met groups='base.group_system' en komt nooit in een log.
"""
import json
import logging
import secrets
from datetime import timedelta

import requests

from odoo import _, api, fields, models, release
from odoo.exceptions import UserError

from .. import ssts

_logger = logging.getLogger(__name__)

MODULE = 'softwareservices_timesheets_connector'
CONNECTOR_XMLID = f'{MODULE}.user_connector'
# Rooster en verlof (SPEC 9 §8.3, taak 9.5): de technische gebruiker krijgt deze groepen erbij zodra de app er is (geen harde
# afhankelijkheid van Time Off). Werknemers: Officer en Time Off: Officer, net als de bot van de API-koppeling.
OPTIONAL_GROUPS = ('hr.group_hr_user', 'hr_holidays.group_hr_holidays_user')
# wat /ping meldt (ModuleTransport.has_group): de groepen die de technische gebruiker echt heeft
KNOWN_GROUPS = ('project.group_project_user', 'hr_timesheet.group_hr_timesheet_approver', 'hr_timesheet.group_timesheet_manager',
                'hr.group_hr_user', 'hr_holidays.group_hr_holidays_user', 'planning.group_planning_manager')
# de contracten (18) leest de module zelf (sudo, alleen de rooster-velden van gekoppelde werknemers): dat telt als die rol
CONTRACT_GROUPS = ('hr_contract.group_hr_contract_employee_manager', 'hr_contract.group_hr_contract_manager')
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
TIMEOUT = 15
GRACE_MINUTES = 10                      # na een rotatie aanvaardt de module het oude geheim nog zo lang (SPEC 5.1 §8)
SYSTEM = 'base.group_system'


def normalize_code(code):
    return ''.join(ch for ch in (code or '').upper() if ch in ALPHABET)


class ServiceError(Exception):
    """De dienst kon niet bereikt worden of antwoordde met een fout; de tekst is voor de beheerder."""


class Connection(models.Model):
    _name = 'ss_timesheets.connection'
    _description = 'Koppeling met TimeViking'
    _rec_name = 'service_url'

    service_url = fields.Char('Service-URL', required=True, default=lambda self: self._default_service_url(),
                              help='Het adres van TimeViking, bijvoorbeeld https://app.voorbeeld.be')
    odoo_url = fields.Char('Adres van deze Odoo', default=lambda self: self._default_odoo_url(),
                           help='Waarop de dienst deze Odoo bereikt. Standaard web.base.url.')
    pair_code = fields.Char('Koppelcode', copy=False, groups=SYSTEM)
    mode = fields.Selection([('push', 'Push: de dienst roept Odoo aan')], default='push', required=True, string='Modus')
    state = fields.Selection([('unpaired', 'Niet gekoppeld'), ('paired', 'Gekoppeld')], default='unpaired', required=True,
                             readonly=True, string='Status')
    company_ids = fields.Many2many('res.company', string='Bedrijven', default=lambda self: self.env['res.company'].search([]),
                                   help='De bedrijven waarvan de dienst projecten, werknemers en uren mag zien.')
    paired_at = fields.Datetime('Gekoppeld sinds', readonly=True, copy=False)
    connection_ref = fields.Char('Verbinding', readonly=True, copy=False)
    last_message = fields.Char('Laatste melding', readonly=True, copy=False)
    # het geheim: alleen voor de systeemgroep, nooit in exports of logs
    key_id = fields.Char(readonly=True, copy=False, groups=SYSTEM)
    secret = fields.Char(readonly=True, copy=False, groups=SYSTEM)
    prev_key_id = fields.Char(readonly=True, copy=False, groups=SYSTEM)
    prev_secret = fields.Char(readonly=True, copy=False, groups=SYSTEM)
    prev_valid_until = fields.Datetime(readonly=True, copy=False, groups=SYSTEM)
    link_count = fields.Integer(compute='_compute_link_count', string='Gekoppelde werknemers')

    def _default_service_url(self):
        return self.env['ir.config_parameter'].sudo().get_param('ss_timesheets.default_service_url') or 'https://'

    def _default_odoo_url(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''

    def _compute_link_count(self):
        count = self.env['ss_timesheets.employee_link'].sudo().search_count([])
        for rec in self:
            rec.link_count = count

    @api.model
    def get_connection(self):
        """The one connection record (made on first use)."""
        rec = self.sudo().search([], limit=1, order='id')
        return rec or self.sudo().create({})

    @api.model
    def action_open(self):
        rec = self.get_connection()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': rec.id, 'view_mode': 'form',
                'target': 'current', 'name': _('TimeViking')}

    # ── sleutels ──

    def _keys(self):
        """{key_id: secret_b64} that are valid now: the current one, and the previous one during the grace period."""
        self.ensure_one()
        rec = self.sudo()
        keys = {}
        if rec.state == 'paired' and rec.key_id and rec.secret:
            keys[rec.key_id] = rec.secret
        if rec.prev_key_id and rec.prev_secret and rec.prev_valid_until and rec.prev_valid_until > fields.Datetime.now():
            keys[rec.prev_key_id] = rec.prev_secret
        return keys

    @api.model
    def _find_secret(self, key_id):
        """(connection, secret bytes) for a key_id, or (None, None)."""
        if not key_id:
            return None, None
        for rec in self.sudo().search([('state', '=', 'paired')]):
            secret = rec._keys().get(key_id)
            if secret:
                return rec, ssts.secret_bytes(secret)
        return None, None

    def _rotate(self):
        """A new key_id and secret; the old pair stays valid for GRACE_MINUTES (SPEC 5.1 §8)."""
        self.ensure_one()
        rec = self.sudo()
        key_id, secret = 'k_' + secrets.token_hex(8), ssts.new_secret_b64()
        rec.write({'prev_key_id': rec.key_id, 'prev_secret': rec.secret,
                   'prev_valid_until': fields.Datetime.now() + timedelta(minutes=GRACE_MINUTES),
                   'key_id': key_id, 'secret': secret})
        _logger.info('ss_timesheets: geheim vernieuwd (nieuwe key_id %s)', key_id)
        return key_id, secret

    def _wipe(self, message):
        self.sudo().write({'state': 'unpaired', 'key_id': False, 'secret': False, 'prev_key_id': False, 'prev_secret': False,
                           'prev_valid_until': False, 'paired_at': False, 'connection_ref': False, 'last_message': message})

    # ── verzoeken naar de dienst ──

    def _check_url(self, url):
        url = (url or '').strip().rstrip('/')
        insecure = self.env['ir.config_parameter'].sudo().get_param('ss_timesheets.allow_insecure') in ('1', 'True', 'true')
        if not (url.startswith('https://') or (insecure and url.startswith('http://'))) or len(url) <= len('https://'):
            raise UserError(_('De service-URL moet met https:// beginnen.'))
        return url

    def _post(self, path, body, signed=True):
        """POST JSON to the service. Signed requests also need a signed answer. Returns (status, data)."""
        self.ensure_one()
        rec = self.sudo()
        url = self._check_url(rec.service_url)
        raw = json.dumps(body, separators=(',', ':')).encode()
        hdrs = {'Content-Type': 'application/json'}
        secret = None
        if signed:
            if not (rec.key_id and rec.secret):
                raise UserError(_('Deze Odoo is niet gekoppeld.'))
            secret = ssts.secret_bytes(rec.secret)
            hdrs.update(ssts.headers(secret, rec.key_id, ssts.TO_APP, path, raw))
        try:
            res = self._http_post(url + path, raw, hdrs)
        except requests.RequestException as e:
            raise ServiceError(_('Kan %(url)s niet bereiken: %(error)s', url=url, error=type(e).__name__)) from None
        if signed and res.status_code != 401:
            try:
                ssts.verify(secret, ssts.TO_ODOO, path, res.content or b'', res.headers)
            except ssts.SignatureError:
                raise ServiceError(_('Het antwoord van %(url)s is niet geldig ondertekend.', url=url)) from None
        try:
            data = res.json()
        except ValueError:
            data = {}
        return res.status_code, data if isinstance(data, dict) else {}

    def _http_post(self, url, raw, hdrs):
        """The one place that goes over the wire (tests replace it)."""
        return requests.post(url, data=raw, headers=hdrs, timeout=TIMEOUT, allow_redirects=False)

    def _server_info(self):
        module = self.env['ir.module.module'].sudo().search([('name', '=', MODULE)], limit=1)
        return {'server_version': release.version, 'module_version': module.installed_version or module.latest_version,
                'db_uuid': self.env['ir.config_parameter'].sudo().get_param('database.uuid')}

    # ── de technische gebruiker (SPEC 9 §8.3) ──

    @api.model
    def _ensure_connector(self):
        """Give the technical user the optional groups that exist in this Odoo (Time Off: Officer when Time Off is installed) and
        every company, so that Odoo's own approval rules (_check_approval_update, run as this user) see an Officer in the right
        companies. Idempotent; called at install/update (data/connector.xml), when pairing and on every /ping, so a Time Off
        installed later is picked up too. Returns the user."""
        connector = self.env.ref(CONNECTOR_XMLID, raise_if_not_found=False)
        if not connector:
            return connector
        connector = connector.sudo().with_context(active_test=False)
        vals = {}
        missing = []
        for xmlid in OPTIONAL_GROUPS:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group and not connector.has_group(xmlid):
                missing.append(group.id)
        if missing:
            vals['groups_id'] = [(4, gid) for gid in missing]
        companies = self.env['res.company'].sudo().search([])
        if set(companies.ids) - set(connector.company_ids.ids):
            vals['company_ids'] = [(6, 0, companies.ids)]
        if vals:
            connector.write(vals)
            _logger.info('ss_timesheets: rechten van de technische gebruiker bijgewerkt (%s)', ', '.join(sorted(vals)))
        return connector

    @api.model
    def _connector_groups(self):
        """The groups of KNOWN_GROUPS the technical user has (for /ping), plus the contract role when hr.contract exists."""
        connector = self.env.ref(CONNECTOR_XMLID, raise_if_not_found=False)
        if not connector:
            return []
        connector = connector.sudo()
        out = [x for x in KNOWN_GROUPS if self.env.ref(x, raise_if_not_found=False) and connector.has_group(x)]
        if 'hr.contract' in self.env:
            out += list(CONTRACT_GROUPS)
        return out

    # ── knoppen ──

    def action_pair(self):
        self.ensure_one()
        rec = self.sudo()
        code = normalize_code(rec.pair_code)
        if len(code) != 12:
            raise UserError(_('Plak de koppelcode van 12 tekens uit TimeViking.'))
        companies = rec.company_ids or self.env['res.company'].sudo().search([])
        body = {'code': f'{code[:4]}-{code[4:8]}-{code[8:]}', 'odoo_url': (rec.odoo_url or self._default_odoo_url()).rstrip('/'),
                **self._server_info(), 'companies': [{'id': c.id, 'name': c.name} for c in companies], 'mode': rec.mode,
                'public_key_hint': None}
        try:
            status, data = self._post('/api/module/v1/pair', body, signed=False)
        except ServiceError as e:
            raise UserError(str(e)) from None
        if status == 404:
            raise UserError(_('De koppelcode is ongeldig of verlopen. Maak een nieuwe in TimeViking.'))
        if status != 200 or not (data.get('key_id') and data.get('secret')):
            raise UserError(_('Koppelen mislukt: %(error)s', error=data.get('message') or data.get('error') or f'HTTP {status}'))
        ssts.secret_bytes(data['secret'])                                    # moet geldige base64 zijn
        self._ensure_connector()
        rec.write({'state': 'paired', 'key_id': data['key_id'], 'secret': data['secret'], 'pair_code': False,
                   'connection_ref': data.get('connection_id'), 'paired_at': fields.Datetime.now(), 'company_ids': [(6, 0, companies.ids)],
                   'prev_key_id': False, 'prev_secret': False, 'prev_valid_until': False,
                   'last_message': _('Gekoppeld met %(url)s', url=rec.service_url)})
        _logger.info('ss_timesheets: gekoppeld met %s (key_id %s)', rec.service_url, data['key_id'])
        return True

    def action_test(self):
        self.ensure_one()
        try:
            status, data = self._post('/api/module/v1/ping', {})
        except ServiceError as e:
            raise UserError(str(e)) from None
        if status != 200 or not data.get('ok'):
            raise UserError(_('De dienst weigerde de test: %(error)s', error=data.get('error') or f'HTTP {status}'))
        message = _('Verbinding in orde met %(name)s', name=data.get('tenant_name') or self.sudo().service_url)
        self.sudo().last_message = message
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('TimeViking'), 'message': message, 'type': 'success', 'sticky': False}}

    def action_unpair(self):
        """Wipe the secret here and tell the service (best effort, SPEC 5.1 §8)."""
        self.ensure_one()
        message = _('Ontkoppeld')
        if self.sudo().key_id and self.sudo().secret:
            try:
                status, _data = self._post('/api/module/v1/revoke', {})
                if status != 200:
                    message = _('Ontkoppeld; de dienst antwoordde HTTP %(status)s en merkt het bij het volgende verzoek', status=status)
            except (ServiceError, UserError) as e:
                message = _('Ontkoppeld; de dienst was niet bereikbaar (%(error)s) en merkt het bij het volgende verzoek', error=str(e))
        self._wipe(message)
        _logger.info('ss_timesheets: ontkoppeld')
        return True

    def action_open_links(self):
        return {'type': 'ir.actions.act_window', 'res_model': 'ss_timesheets.employee_link', 'view_mode': 'list',
                'name': _('Gekoppelde werknemers'), 'target': 'current'}

    @api.model
    def _cron_cleanup(self):
        """Nonces older than 15 minutes, and previous keys past their grace period."""
        self.env['ss_timesheets.nonce'].sudo()._cleanup()
        self.sudo().search([('prev_valid_until', '<', fields.Datetime.now())]).write(
            {'prev_key_id': False, 'prev_secret': False, 'prev_valid_until': False})

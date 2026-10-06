"""
De ingang voor TimeViking (SPEC 5.1 §5, §7): POST /ss_timesheets/v1/<endpoint>, auth='none'.

Er gebeurt niets vóór de handtekening klopt: onbekend endpoint → 404, te grote body → 413, dan key_id, tijd, HMAC en pas dan
de nonce (401 met één foutcode; I7: wie het geheim niet kent, laat geen nonce achter), en daarna de service-laag. Elk
antwoord na een geldige handtekening is zelf ondertekend (richting odoo->app, zelfde pad). Het geheim komt nooit in een log; mislukte pogingen wel, met IP en key_id.
"""
import json
import logging
import time

from odoo import SUPERUSER_ID, http
from odoo.http import request

from .. import service, ssts

_logger = logging.getLogger(__name__)

PREFIX = '/ss_timesheets/v1/'
MAX_BODY = 2 * 1024 * 1024
BASE_HEADERS = [('Content-Type', 'application/json'), ('Cache-Control', 'no-store')]


def _response(payload, status, secret=None, path=None):
    raw = json.dumps(payload, separators=(',', ':'), default=str).encode()
    headers = list(BASE_HEADERS)
    if secret:
        headers += list(ssts.headers(secret, None, ssts.TO_APP, path, raw).items())
    return request.make_response(raw, headers=headers, status=status)


def _log_refusal(env, code, key_id):
    ip = request.httprequest.remote_addr
    _logger.warning('ss_timesheets: verzoek geweigerd (%s) van %s, key_id %s', code, ip, (key_id or '-')[:40])
    try:
        env['ir.logging'].sudo().create({'name': 'ss_timesheets', 'type': 'server', 'level': 'WARNING', 'path': PREFIX,
                                         'func': 'verify', 'line': '0',
                                         'message': f'Request refused ({code}) from {ip}, key_id {(key_id or "-")[:40]}'})
    except Exception:                                                  # het loggen mag de weigering nooit laten falen
        _logger.exception('ss_timesheets: kon de weigering niet in ir.logging zetten')


class ConnectorController(http.Controller):

    # readonly=False: met auth='none' kiest Odoo 18 standaard een alleen-lezen cursor (en probeert dan opnieuw); wij schrijven
    # altijd (de nonce vóór de verwerking), dus meteen lezen en schrijven.
    @http.route(PREFIX + '<path:endpoint>', type='http', auth='none', methods=['POST'], csrf=False, save_session=False,
                readonly=False)
    def dispatch(self, endpoint, **_kw):
        httpreq = request.httprequest
        path = httpreq.path
        if endpoint not in service.ENDPOINTS:
            return _response({'error': 'not_found'}, 404)
        if (httpreq.content_length or 0) > MAX_BODY:
            return _response({'error': 'too_large'}, 413)
        env = request.env(user=SUPERUSER_ID)
        key_id = httpreq.headers.get('X-SS-Key-Id') or ''
        raw = httpreq.get_data(cache=True)
        connection, secret = env['ss_timesheets.connection']._find_secret(key_id)
        try:
            if not secret:
                raise ssts.SignatureError('unknown_key')
            ssts.verify(secret, ssts.TO_ODOO, path, raw, httpreq.headers,
                        seen_nonce=lambda nonce: env['ss_timesheets.nonce']._seen(key_id, nonce))
        except ssts.SignatureError as e:
            _log_refusal(env, e.code, key_id)
            body = {'error': e.code}
            if e.code == 'clock_skew':
                body['server_time'] = int(time.time())
            return _response(body, 401)
        if len(raw) > MAX_BODY:
            return _response({'error': 'too_large'}, 413, secret, path)
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return _response({'error': 'bad_request', 'message': 'JSON object expected'}, 400, secret, path)
        status, payload = service.run(env, connection, endpoint, body)
        return _response(payload, status, secret, path)

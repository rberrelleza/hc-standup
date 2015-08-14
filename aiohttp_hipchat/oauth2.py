import jwt
import logging
import asyncio
import json
from aiohttp_hipchat.util import http_request

_log = logging.getLogger(__name__)

ACCESS_TOKEN_CACHE = "hipchat-tokens:{oauth_id}"


class Oauth2Client:

    def __init__(self, id, secret=None, homepage=None, capabilities_url=None, room_id=None, token_url=None,
                 group_id=None, group_name=None, capdoc=None):
        self.id = id
        self.room_id = room_id
        self.secret = secret
        self.group_id = group_id
        self.group_name = None if not group_name else group_name
        self.homepage = homepage or None if not capdoc else capdoc['links']['homepage']
        self.token_url = token_url or None if not capdoc else capdoc['capabilities']['oauth2Provider']['tokenUrl']
        self.capabilities_url = capabilities_url or None if not capdoc else capdoc['links']['self']

    def to_map(self):
        return {
            "id": self.id,
            "secret": self.secret,
            "room_id": self.room_id,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "homepage": self.homepage,
            "token_url": self.token_url,
            "capabilities_url": self.capabilities_url
        }

    @staticmethod
    def from_map(data):
        filtered = {key: val for key, val in data.items() if not key.startswith('_')}
        return Oauth2Client(**filtered)

    @property
    def id_query(self):
        return {"id": self.id}

    @property
    def api_base_url(self):
        return self.capabilities_url[0:self.capabilities_url.rfind('/')]

    @property
    def room_base_url(self):
        return "{base_url}/room/{room_id}".format(base_url=self.api_base_url, room_id=self.room_id)

    @asyncio.coroutine
    def get_token(self, redis_pool, token_only=True, scopes=None):
        if scopes is None:
            scopes = ["send_notification"]

        cache_key = ACCESS_TOKEN_CACHE.format(oauth_id=self.id)
        cache_key += ":" + ",".join(scopes)

        @asyncio.coroutine
        def gen_token():
            with (yield from http_request('POST', self.token_url, data={
                "grant_type": "client_credentials", "scope": " ".join(scopes)},
                    auth=(self.id, self.secret), timeout=10)) as resp:
                if resp.status == 200:
                    _log.debug("Token request response: %s" % (yield from resp.read()))
                    return (yield from resp.read(decode=True))
                elif resp.status == 401:
                    _log.error("Client %s is invalid but we weren't notified.  Uninstalling" % self.id)
                    raise OauthClientInvalidError(self)
                else:
                    raise Exception("Invalid token: %s" % (yield from resp.read()))

        if token_only:
            with (yield from redis_pool) as redis:
                token = yield from redis.get(cache_key)
                if not token:
                    data = yield from gen_token()
                    token = data['access_token']
                    yield from redis.setex(key=cache_key, value=token, seconds=data['expires_in'] - 20)
                else:
                    token = token.decode(encoding="utf-8")
            return token
        else:
            return (yield from gen_token())

    def sign_jwt(self, user_id, data=None):
        if data is None:
            data = {}
        data.update({
            'iss': self.id,
            'prn': user_id
        })
        return jwt.encode(data, self.secret)

    @asyncio.coroutine
    def send_notification(self, addon, from_mention=None, text=None, html=None, room_id_or_name=None):
        if room_id_or_name is None:
            room_id_or_name = self.room_id

        token = yield from self.get_token(addon.redis_pool)

        if html:
            data = {"message": html,
                    "message_format": "html"}
        elif text:
            msg = text
            if from_mention:
                msg = "@%s %s" % (from_mention, text)
            data = {"message": msg,
                    "message_format": "text"}
        else:
            raise Exception("'html' or 'text' must be specified")

        with (yield from http_request('POST', "%s/room/%s/notification" % (self.api_base_url, room_id_or_name),
                                      headers={'content-type': 'application/json',
                                               'authorization': 'Bearer %s' % token},
                                      data=json.dumps(data),
                                      timeout=10)) as resp:
            if resp.status != 204:
                body = yield from resp.read()
                _log.error("Cannot send notification: %s - %s" % (resp.status, body))

    @asyncio.coroutine
    def post_webhook(self, addon, url, event='room_message', pattern=None, room_id_or_name=None, name=''):
        if room_id_or_name is None:
            room_id_or_name = self.room_id

        data = {
            "url": url,
            "event": event,
            "name": name
        }
        if pattern is not None:
            data['pattern'] = pattern

        token = yield from self.get_token(addon.redis, scopes=['admin_room'])
        with (yield from http_request('POST', "{base_url}/room/{room_id}/webhook".format(base_url=self.api_base_url,
                                                                                         room_id=room_id_or_name),
                                      headers={'content-type': 'application/json',
                                               'authorization': 'Bearer %s' % token},
                                      data=json.dumps(data),
                                      timeout=10)) as resp:
            if resp.status != 201:
                body = yield from resp.read()
                _log.error("Cannot register webhook: %s - %s" % (resp.status, body))
                return False

        return resp['LOCATION']

    @asyncio.coroutine
    def delete_webhook(self, addon, url):

        token = yield from self.get_token(addon.redis, scopes=['admin_room'])
        with (yield from http_request('DELETE', url,
                                      headers={'authorization': 'Bearer %s' % token},
                                      timeout=10)) as resp:
            if resp.status != 204:
                body = yield from resp.read()
                _log.error("Cannot unregister webhook: %s - %s" % (resp.status, body))
                return False

        return True


class OauthClientInvalidError(Exception):
    def __init__(self, client, *args, **kwargs):
        super(OauthClientInvalidError, self).__init__(*args, **kwargs)
        self.client = client
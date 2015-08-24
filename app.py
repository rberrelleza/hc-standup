import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
import logging
from urllib.parse import urlparse
import aiohttp
from aiohttp import web
from aiohttp_ac_hipchat.addon import create_addon_app, validate_jwt
from aiohttp_ac_hipchat.addon import require_jwt
import json
from aiohttp_ac_hipchat.util import http_request, allow_cross_origin
import aiohttp_jinja2
import aioredis
import arrow
import bleach
import jinja2
import markdown
import os

GLANCE_MODULE_KEY = "hcstandup.glance"
AVATAR_CACHE_KEY = "hipchat-avatar:{user_id}"
USER_CACHE_KEY = "hipchat-user:{user_id}"

log = logging.getLogger(__name__)

SCOPES_V2 = ["view_group", "send_notification", "view_room"]

app = create_addon_app(plugin_key="hc-standup",
                       addon_name="HC Standup",
                       from_name="Standup",
                       scopes=SCOPES_V2)

aiohttp_jinja2.setup(app, autoescape=True, loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(__file__), 'views')))


@asyncio.coroutine
def init_pub_sub():
    redis_url = app['config'].get('REDIS_URL')
    if not redis_url:
        redis_url = 'redis://localhost:6379'

    url = urlparse(redis_url)

    db = 0
    try:
        if url.path:
            db = int(url.path.replace('/', ''))
    except (AttributeError, ValueError):
        pass

    sub = yield from aioredis.create_redis((url.hostname, url.port), db=db, password=url.password)
    app["redis_sub"] = sub
    res = yield from sub.subscribe("updates:1")

    tsk = asyncio.async(reader(res[0]))
    # yield from tsk
    # return reader(res[0])

@asyncio.coroutine
def subscribe_new_client(client_id, room_id):
    chanel_key = "updates:{client_id}:{room_id}".format(client_id=client_id, room_id=room_id)
    log.debug("Subscribe to {0}".format(chanel_key))
    res = yield from app["redis_sub"].subscribe(chanel_key)
    asyncio.async(reader(res[0]))

@asyncio.coroutine
def unsubscribe_client(client_id, room_id):
    chanel_key = "updates:{client_id}:{room_id}".format(client_id=client_id, room_id=room_id)
    log.debug("Unsubscribe to {0}".format(chanel_key))
    res = yield from app["redis_sub"].unsubscribe(chanel_key)

@asyncio.coroutine
def init(app):
    @asyncio.coroutine
    def send_welcome(event):
        client = event['client']
        yield from client.send_notification(app['addon'], text="HC Standup was added to this room. Type '/standup I did *this*' to get started (yes, "
                                                    "you can use Markdown).")

    app['addon'].register_event('install', send_welcome)
    yield from init_pub_sub()

app.add_hook("before_first_request", init)

@asyncio.coroutine
def capabilities(request):

    config = request.app["config"]
    base_url = config["BASE_URL"]
    response = web.Response(text=json.dumps({
        "links": {
            "self": base_url,
            "homepage": base_url
        },
        "key": config.get("PLUGIN_KEY"),
        "name": config.get("ADDON_NAME"),
        "description": "HipChat connect add-on that supports async standups",
        "vendor": {
            "name": "Atlassian Labs",
            "url": "https://atlassian.com"
        },
        "capabilities": {
            "installable": {
                "allowGlobal": False,
                "allowRoom": True,
                "callbackUrl": base_url + "/installable"
            },
            "hipchatApiConsumer": {
                "scopes": SCOPES_V2,
                "fromName": config.get("FROM_NAME")
            },
            "webhook": [
                {
                    "url": base_url + "/standup",
                    "event": "room_message",
                    "pattern": "^/(?:status|standup)(\s|$).*"
                }
            ],
            "glance": [
                {
                    "key": GLANCE_MODULE_KEY,
                    "name": {
                        "value": "Standup"
                    },
                    "queryUrl": base_url + "/glance",
                    "target": "hcstandup.sidebar",
                    "icon": {
                        "url": base_url + "/static/info.png",
                        "url@2x": base_url + "/static/info@2x.png"
                    }
                }
            ],
            "webPanel": [
                {
                    "key": "hcstandup.sidebar",
                    "name": {
                        "value": "Standup reports"
                    },
                    "location": "hipchat.sidebar.right",
                    "url": base_url + "/report"
                },
                {
                    "key": "hcstandup.dialog",
                    "name": {
                        "value": "New report"
                    },
                    "target": {
                        "type": "dialog",
                        "title": "Create new report",
                        "hint": "",
                        "button": "Submit"
                    },
                    "location": "hipchat.sidebar.right",
                    "url": base_url + "/dialog"
                }
            ]
        }
    }))

    return response

@asyncio.coroutine
@require_jwt(app)
@allow_cross_origin
def get_glance(request):
    spec, statuses = yield from find_statuses(app, request.client)

    return web.Response(text=json.dumps(glance_json(statuses)))

def glance_json(statuses):
    return {
        "label": {
            "type": "html",
            "value": "<strong>%s</strong> Standup reports" % len(statuses.items())
        }
    }

@asyncio.coroutine
def find_statuses(app, client):
    spec = status_spec(client)
    data = yield from standup_db(app).find_one(spec)
    if not data:
        statuses = {}
    else:
        statuses = data.get('users', {})
        result = {}
        for mention_name, status in statuses.items():
            if status and status['date'].replace(tzinfo=None) > datetime.utcnow()-timedelta(days=3):
                result[mention_name] = status
            else:
                print("Filtering status from %s of date %s" % (mention_name, status.get('date')))

        statuses = result

    return spec, statuses

@asyncio.coroutine
def standup_webhook(request):
    addon = request.app['addon']
    body = yield from request.json()
    client_id = body['oauth_client_id']
    client = yield from addon.load_client(client_id)

    status = str(body['item']["message"]["message"][len("/standup"):]).strip()
    from_user = body['item']['message']['from']
    room = body['item']['room']

    if not status:
        yield from display_all_statuses(app, client)
    elif status.startswith("@") and ' ' not in status:
        yield from display_one_status(app, client, mention_name=status)
    elif status == "clear":
        yield from clear_status(app, client, from_user, room)
    else:
        yield from record_status(app, client, from_user, status, room, request)

    return web.Response(status=204)

@asyncio.coroutine
def clear_status(app, client, from_user, room):
    spec, statuses = yield from find_statuses(app, client)

    user_mention = from_user['mention_name']
    del statuses[user_mention]

    data = dict(spec)
    data['users'] = statuses

    yield from standup_db(app).update(spec, data, upsert=True)

    yield from client.send_notification(app['addon'], text="Status Cleared")
    yield from update_glance(app, client, room)
    yield from send_udpate(client, room["id"], {
        "user_id": from_user["id"],
        "html": ""
    })

@asyncio.coroutine
def display_one_status(app, client, mention_name):
    spec, statuses = yield from find_statuses(app, client)

    if mention_name.startswith("@"):
        mention_name = mention_name[1:]

    status = statuses.get(mention_name)
    if status:
        yield from client.send_notification(app['addon'], html=render_status(status))
    else:
        yield from client.send_notification(app['addon'], text="No status found. "
                                                        "Type '/standup I did this' to add your own status.")

@asyncio.coroutine
def display_all_statuses(app, client):
    spec, statuses = yield from find_statuses(app, client)

    if statuses:
        yield from client.send_notification(app['addon'], html=render_all_statuses(statuses))
    else:
        yield from client.send_notification(app['addon'], text="No status found. "
                                                        "Type '/standup I did this' to add your own status.")

@asyncio.coroutine
def record_status(app, client, from_user, status, room, request, send_notification=True):
    spec, statuses = yield from find_statuses(app, client)

    user_mention = from_user['mention_name']

    avatar_url = from_user.get('photo_url', None)
    if not avatar_url:
        avatar_url = yield from get_photo_url(client, from_user['id'], room['id'])
        from_user['photo_url'] = avatar_url

    statuses[user_mention] = {
        "user": from_user,
        "message": status,
        "date": datetime.utcnow()
    }

    data = dict(spec)
    data['users'] = statuses

    yield from standup_db(app).update(spec, data, upsert=True)

    if send_notification:
        yield from client.send_notification(app['addon'], text="Status recorded.  Type '/standup' to see the full report.")
    yield from update_glance(app, client, room)
    yield from update_sidebar(from_user, request, statuses, user_mention, client, room)


@asyncio.coroutine
def update_sidebar(from_user, request, statuses, user_mention, client, room):
    html = aiohttp_jinja2.render_string("_status.jinja2", request, {
        "status": status_to_view(statuses[user_mention])
    }, app_key="aiohttp_jinja2_environment")
    yield from send_udpate(client, room["id"], {
        "user_id": from_user["id"],
        "html": html
    })


@asyncio.coroutine
def get_photo_url(client, user_id, room_id):
    user = (yield from get_user(app, client, room_id, user_id))
    photo_url = user['photo_url'] if user else None
    if not photo_url:
        photo_url = None

    return photo_url

@asyncio.coroutine
def get_room_participants(app, client, room_id_or_name):
    redis_pool = app['redis_pool']
    if not client.has_scope("view_room"):
        return []

    token = yield from client.get_token(redis_pool, scopes=['view_room'])
    with (yield from http_request('GET', "%s/room/%s/participant?expand=items" % (client.api_base_url, room_id_or_name),
                                  headers={'content-type': 'application/json',
                                           'authorization': 'Bearer %s' % token},
                                  timeout=10)) as resp:
        if resp.status == 200:
            body = yield from resp.read(decode=True)
            room_participants = body['items']

            with (yield from redis_pool) as redis:
                for room_participant in room_participants:
                    cache_key = USER_CACHE_KEY.format(user_id=room_participant['id'])
                    redis.setex(key=cache_key, value=json.dumps(room_participant), seconds=3600)

            return room_participants

@asyncio.coroutine
def update_glance(app, client, room):
    spec, statuses = yield from find_statuses(app, client)
    yield from push_glance_update(app, client, room['id'], {
        "glance": [{
            "key": GLANCE_MODULE_KEY,
            "content": glance_json(statuses)
        }]
    })

@asyncio.coroutine
def push_glance_update(app, client, room_id_or_name, glance):
    if not client.has_scope("view_room"):
        return

    token = yield from client.get_token(app['redis_pool'], scopes=['view_room'])
    with (yield from http_request('POST', "%s/addon/ui/room/%s" % (client.api_base_url, room_id_or_name),
                                  headers={'content-type': 'application/json',
                                           'authorization': 'Bearer %s' % token},
                                  data=json.dumps(glance),
                                  timeout=10)) as resp:
        if resp.status == 200:
            body = yield from resp.read(decode=True)
            return body['items']

@asyncio.coroutine
@require_jwt(app)
@aiohttp_jinja2.template('report.jinja2')
def report_view(request):
    """
        Render the report view
    """
    return {
        "base_url": app["config"]["BASE_URL"],
        "signed_request": request.signed_request,
        "room_id": request.jwt_data["context"]["room_id"],
        "create_new_report_enabled": os.environ.get("create_new_report_enabled", False)
    }

@asyncio.coroutine
@require_jwt(app)
def get_statuses(request):
    _, statuses = yield from find_statuses(app, request.client)

    web.Response(text=json.dumps(glance_json(statuses)))

@asyncio.coroutine
@require_jwt(app)
@aiohttp_jinja2.template('statuses.jinja2')
def get_statuses_view(request):
    results = []
    _, statuses = yield from find_statuses(app, request.client)
    for status in statuses.values():
        results.append(status_to_view(status))

    return {
        "statuses": results
    }

@asyncio.coroutine
@require_jwt(app)
@aiohttp_jinja2.template('create.jinja2')
def create_new_report_view(request):
    spec, statuses = yield from find_statuses(app, request.client)

    room_id = request.jwt_data['context']['room_id']
    user_id = request.jwt_data['prn']

    last_status = None
    for status in statuses.values():
        if str(status['user']['id']) == request.jwt_data['prn']:
            last_status = status_to_view(status)
            break
    if not last_status:
        user = yield from get_user(request.app, request.client, room_id, user_id)

        last_status = {
            "date": "Never",
            "user": user,
            "message_html": render_markdown_as_safe_html("**Yesterday I worked on:**   \n" +
                            """ ¯\\\_(ツ)\_/¯""")
        }

    return {
        "base_url": app["config"]["BASE_URL"],
        "status": last_status,
        "signed_request": request.signed_request,
        "report_template": "**Yesterday I worked on:**   \n" +
                           "1.  \n\n" +
                           "**Today**  \n" +
                           "1.  "
    }


@asyncio.coroutine
@require_jwt(app)
def create_new_report(request):
    body = yield from request.json()

    status = body['message']
    room_id = request.jwt_data['context']['room_id']
    user_id = request.jwt_data['prn']

    room = {
        "id": room_id
    }
    from_user = yield from get_user(request.app, request.client, room_id, user_id)

    if not from_user:
        return web.Response(status=401)
    else:
        yield from record_status(app, request.client, from_user, status, room, request, send_notification=False)
        return web.Response(status=204)

@asyncio.coroutine
def get_user(app, client, room_id, user_id):
    user = None

    user_key = USER_CACHE_KEY.format(user_id=user_id)
    with (yield from app['redis_pool']) as redis:
        cached_data = (yield from redis.get(user_key))
        user = json.loads(cached_data.decode(encoding="utf-8")) if cached_data else None

    if not user:
        room_participants = yield from get_room_participants(app, client, room_id)
        for room_participant in room_participants:
            if room_participant['id'] == int(user_id):
                user = room_participant

    return user

@asyncio.coroutine
def keep_alive(websocket, ping_period=15):
    while True:
        yield from asyncio.sleep(ping_period)

        try:
            log.debug("Ping websocket")
            websocket.ping()
        except Exception as e:
            log.warn('Got exception when trying to keep connection alive, '
                       'giving up.')
            return

ws_connections = defaultdict(lambda: defaultdict(set))

@asyncio.coroutine
def reader(channel):
    while (yield from channel.wait_message()):
        msg = yield from channel.get(encoding='utf-8')
        yield from websocket_send_udpate(msg)

@asyncio.coroutine
def send_udpate(client, room_id, data):
    log.debug("Publish update to Redis".format(len(ws_connections)))
    with (yield from app['redis_pool']) as redis:
        redis.publish_json("updates:1", {
            "client_id": client.id,
            "room_id": room_id,
            "data": data,
        })

@asyncio.coroutine
def websocket_send_udpate(json_data):
    data = json.loads(json_data)

    ws_connections_for_room = ws_connections[data["client_id"]][data["room_id"]]
    log.debug("Send update to {0} WebSocket".format(len(ws_connections_for_room)))
    for ws_connection in ws_connections_for_room:
        try:
            ws_connection.send_str(json.dumps(data["data"]))
        except RuntimeError as e:
            log.warn(e)
            ws_connections.remove(ws_connection)


@asyncio.coroutine
@require_jwt(app)
def websocket_handler(request):
    response = web.WebSocketResponse()
    ok, protocol = response.can_start(request)
    if not ok:
        return web.Response(text="Can't start webSocket connection.")

    response.start(request)

    asyncio.async(keep_alive(response))

    client_id = request.client.id
    room_id = request.jwt_data["context"]["room_id"]

    ws_connections_for_room = ws_connections[client_id][room_id]
    if len(ws_connections_for_room) == 0:
        yield from subscribe_new_client(client_id, room_id)

    ws_connections_for_room.add(response)
    log.debug("WebSocket connection open ({0} in total)".format(len(ws_connections)))

    while True:
        try:
            msg = yield from response.receive()

            if msg.tp == aiohttp.MsgType.close:
                log.info("websocket connection closed")
            elif msg.tp == aiohttp.MsgType.error:
                log.warn("response connection closed with exception %s",
                      response.exception())
        except RuntimeError:
            break

    ws_connections_for_room = ws_connections.get(client_id).get(room_id)
    ws_connections_for_room.remove(response)

    if len(ws_connections_for_room) == 0:
        yield from unsubscribe_client(client_id, room_id)

    return response

def status_to_view(status):
    msg_date = arrow.get(status['date'])
    message = status['message']
    html = render_markdown_as_safe_html(message)

    return {
        "date": msg_date.humanize(),
        "user": status['user'],
        "message_html": html
    }

def render_all_statuses(statuses):
    txt = ""
    for status in statuses.values():
        txt += render_status(status) + "<br>"
    return txt


def render_status(status):
    msg_date = arrow.get(status['date'])

    message = status['message']
    html = render_markdown_as_safe_html(message)
    html = html.replace("<p>", "")
    html = html.replace("</p>", "")
    name = status['user']['name']
    return "<b>{name}</b>: {message} -- <i>{ago}</i>".format(name=name, message=html, ago=msg_date.humanize())

def status_spec(client):
    return {
        "client_id": client.id,
        "group_id": client.group_id,
        "capabilities_url": client.capabilities_url
    }

allowed_tags = bleach.ALLOWED_TAGS + ["p"]
def render_markdown_as_safe_html(message):
    html = markdown.markdown(message)

    return bleach.clean(html, tags=allowed_tags, strip=True)

def standup_db(app):
    return app['mongodb'].default_database['standup']

app.router.add_static('/static', os.path.join(os.path.dirname(__file__), 'static'), name='static')
app.router.add_route('GET', '/', capabilities)
app.router.add_route('GET', '/glance', get_glance)
app.router.add_route('GET', '/status', get_statuses)
app.router.add_route('GET', '/status_view', get_statuses_view)
app.router.add_route('POST', '/standup', standup_webhook)
app.router.add_route('GET', '/report', report_view)
app.router.add_route('GET', '/dialog', create_new_report_view)
app.router.add_route('POST', '/create', create_new_report)
app.router.add_route('GET', '/websocket', websocket_handler)

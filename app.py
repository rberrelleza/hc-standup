from datetime import datetime
import logging
import os
import asyncio
import arrow
import markdown
from bottle_ac import create_addon_app

log = logging.getLogger(__name__)
app = create_addon_app(__name__,
                       plugin_key="hc-standup",
                       addon_name="HC Standup",
                       from_name="Standup",
                       base_url="http://192.168.33.1:8080")

app.config['MONGO_URL'] = os.environ.get("MONGOHQ_URL", None)
app.config['REDIS_URL'] = os.environ.get("REDISTOGO_URL", None)


def init():
    @asyncio.coroutine
    def _send_welcome(event):
        client = event['client']
        yield from client.send_notification(app.addon,
            text="HC Standup was added to this room. Type '/standup I did *this*' to get started (yes, "
                 "you can use Markdown).")

    app.addon.register_event('install', _send_welcome)


app.add_hook('before_first_request', init)


# noinspection PyUnusedLocal
@app.route('/')
def capabilities(request, response):
    return {
        "links": {
            "self": app.config.get("BASE_URL"),
            "homepage": app.config.get("BASE_URL")
        },
        "key": app.config.get("PLUGIN_KEY"),
        "name": app.config.get("ADDON_NAME"),
        "description": "HipChat connect add-on that supports async standups",
        "vendor": {
            "name": "Atlassian Labs",
            "url": "https://atlassian.com"
        },
        "capabilities": {
            "installable": {
                "allowGlobal": False,
                "allowRoom": True,
                "callbackUrl": app.config.get("BASE_URL") + "/installable/"
            },
            "hipchatApiConsumer": {
                "scopes": [
                    "view_group",
                    "send_notification"
                ],
                "fromName": app.config.get("FROM_NAME")
            },
            "webhook": [
                {
                    "url": app.config.get("BASE_URL") + "/standup",
                    "event": "room_message",
                    "pattern": "^/standup(\s|$).*"
                }
            ],
        }
    }


@app.route('/standup', method='POST')
@asyncio.coroutine
def standup(request, response):
    body = request.json
    client_id = body['oauth_client_id']
    client = yield from app.addon.load_client(client_id)

    status = str(body['item']["message"]["message"][len("/standup"):]).strip()
    from_user = body['item']['message']['from']

    if not status:
        yield from display_all_statuses(app.addon, client)
    elif status.startswith("@") and ' ' not in status:
        yield from display_one_status(app.addon, client, mention_name=status)
    else:
        yield from record_status(app.addon, client, from_user, status)

    response.status = 204


@asyncio.coroutine
def record_status(addon, client, from_user, status):
    spec, statuses = yield from find_statuses(addon, client)

    user_mention = from_user['mention_name']
    statuses[user_mention] = {
        "user": from_user,
        "message": status,
        "date": datetime.utcnow()
    }

    data = dict(spec)
    data['users'] = statuses

    yield from standup_db(addon).update(spec, data, upsert=True)

    yield from client.send_notification(addon, text="Status recorded")


@asyncio.coroutine
def display_one_status(addon, client, mention_name):
    spec, statuses = yield from find_statuses(addon, client)

    status = statuses.get(mention_name)
    if status:
        yield from client.send_notification(addon, html=render_status(status))
    else:
        yield from client.send_notification(addon, text="No status found")


@asyncio.coroutine
def display_all_statuses(addon, client):
    spec, statuses = yield from find_statuses(addon, client)

    if statuses:
        yield from client.send_notification(addon, html=render_all_statuses(statuses))
    else:
        yield from client.send_notification(addon, text="No status found")


def render_all_statuses(statuses):
    txt = ""
    for status in statuses.values():
        txt += render_status(status) + "<br>"
    return txt


def render_status(status):
    msg_date = arrow.get(status['date'])

    message = status['message']
    html = markdown.markdown(message)
    html = html.replace("<p>", "")
    html = html.replace("</p>", "")
    name = status['user']['name']
    return "<b>{name}</b>: {message} -- <i>{ago}</i>".format(name=name, message=html, ago=msg_date.humanize())


@asyncio.coroutine
def find_statuses(addon, client):
    spec = status_spec(client)
    data = yield from standup_db(addon).find_one(spec)
    if not data:
        statuses = {}
    else:
        statuses = data.get('users', {})

    return spec, statuses


def status_spec(client):
    return {
        "client_id": client.id,
        "group_id": client.group_id,
        "capabilities_url": client.capabilities_url
    }


def standup_db(addon):
    return addon.mongo_db.default_database['standup']


if __name__ == "__main__":
    app.run(host="", reloader=True, debug=True)

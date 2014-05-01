import asyncio
from datetime import datetime
from pytz import timezone
import sys

from bottle_ac import http_request
import app


force = sys.argv and len(sys.argv) == 2 and sys.argv[1] == "--force"

@asyncio.coroutine
def execute():
    webapp = app.app
    yield from webapp.trigger_hook('before_first_request')
    addon = webapp.addon

    clients = yield from addon.load_all_clients()

    for client in clients:

        token = yield from client.get_token(addon.redis, scopes=["view_group"])

        headers = {"Authorization": "Bearer %s" % token}
        with (yield from http_request('GET', client.room_base_url + "?expand=participants",
                                      headers=headers, timeout=10)) as resp:
            if resp.status == 200:
                body = yield from resp.read(decode=True)
                standup_users = []
                for user in body['participants']:
                    is_online = user['presence']['is_online']
                    if 'timezone' in user and is_online:
                        tz = timezone(user['timezone'])
                        now = tz.localize(datetime.now())
                        if int(now.strftime("%H")) == 10 or force:
                            standup_users.append("@" + user['mention_name'])

                if standup_users:
                    _, statuses = yield from app.find_statuses(addon, client)
                    if statuses:
                        yield from client.send_notification(addon, text="Standup for %s" % " ".join(standup_users))
                        yield from app.display_all_statuses(addon, client)

            elif resp.status == 404:
                print("weird...")
            else:
                raise Exception("Invalid response: %s" % (yield from resp.read()))


loop = asyncio.get_event_loop()
loop.run_until_complete(asyncio.Task(execute()))
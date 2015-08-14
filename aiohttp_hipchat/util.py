from contextlib import closing
import logging
import os
from time import time
import aiohttp
import asyncio
from asyncio import TimeoutError

_log = logging.getLogger(__name__)


def require_env_variable(name):
    val = os.environ.get(name)
    if val is None:
        raise Exception(
            "Must have defined environment variable %s" % name)

    return val


class Response:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self.resp

    def __exit__(self, *args):
        self.resp.close()


@asyncio.coroutine
def http_request(method, url, timeout=10, *args, **kwargs):

    attempt = 1
    while attempt <= 3:
        try:
            t = timer()
            res = yield from asyncio.wait_for(aiohttp.request(method, url, *args, **kwargs), timeout)
            _log.info("Called ({method}) {url} with result of {code} in {elapsed} ms".format(
                method=method, url=url, elapsed=t.end(), code=res.status
            ))
            return closing(res)
        except TimeoutError:
            sleep_for = attempt * attempt
            _log.warn("Timeout calling {url}, attempt again in {sleep_for}".format(
                url=url, sleep_for=sleep_for
            ))
            asyncio.sleep(sleep_for)
            attempt += 1

    raise TimeoutError()


def timer():
    return _Timer()


class _Timer(object):
    def __init__(self):
        super().__init__()
        self.start = time()

    def end(self):
        elapsed = (time() - self.start) * 1000  # elapsed in ms
        elapsed = int(elapsed + 0.5)  # round + convert to int
        return elapsed

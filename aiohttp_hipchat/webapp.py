import asyncio
from aiohttp import web
from aiohttp.log import web_logger
from aiohttp.web import RequestHandlerFactory


class WebApp(web.Application):

    __hook_names = 'before_first_request', 'before_request', 'after_request', 'app_reset', 'config'
    __hook_reversed = 'after_request'

    def __init__(self, *, logger=web_logger, loop=None, router=None, handler_factory=RequestHandlerFactory,
            middlewares=()):

        middlewares = list(middlewares)
        middlewares.append(hook_middleware_factory)

        super().__init__(logger=logger, loop=loop, router=router, handler_factory=handler_factory,
                         middlewares=middlewares)

        self._hooks = dict((name, []) for name in self.__hook_names)

    def route(self, path, methods=None):
        if not methods:
            methods = ["GET"]

        def inner(func):
            for method in methods:
                self.router.add_route(method, path, func)
                if path != "/" and path.endswith("/"):
                    self.router.add_route(method, path[:-1], func)
            return asyncio.coroutine(func)
        return inner

    def add_hook(self, name, func):
        self._hooks[name].append(func)

    @asyncio.coroutine
    def trigger_hook(self, __name, *args, **kwargs):
        """ Trigger a hook and return a list of results. """
        results = []
        for hook in self._hooks[__name][:]:
            result = yield from hook(*args, **kwargs)
            results.append(result)

        return results

@asyncio.coroutine
def hook_middleware_factory(app, handler):
    @asyncio.coroutine
    def middleware(request):
        if app.get("first_request", True):
            app["first_request"] = False
            yield from app.trigger_hook("before_first_request", app)

        return (yield from handler(request))

    return middleware

import asyncio
import json
import logging
import weakref
import webbrowser
from io import StringIO

import aiohttp
from aiohttp import WSCloseCode
from aiohttp import web

from . import schema
from . import utils

log = logging.getLogger(__name__)


class Server:
    def __init__(self, graphs):
        self.graphs = graphs
        self.updater = None
        self._generation = 0

    async def sync(self, ws):
        for graph in self.graphs:
            await ws.send_str(
                utils.dump(dict(kind="update", data=graph, generation=self._generation))
            )
        self._generation += 1

    async def index(self, request):
        return web.FileResponse("assets/html/index.html")

    async def handle_update(self, ws, data):
        g = data.get("generation")
        if g is not None and g <= self._generation:
            await self.sync(ws)

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        request.app["websockets"].add(ws)

        if self.updater is None:
            self.updater = asyncio.create_task(self.update_clients())

        # dump all graph state on client connect
        await self.sync(ws)
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    log.debug(f"ws msg:: {msg.data}")
                    if not msg.data:
                        continue
                    data = json.loads(msg.data)
                    m = getattr(self, f"handle_{data['kind']}", None)
                    if not m:
                        log.info("unhandled client msg:: {data}")
                        continue
                    await m(ws, data)
                elif msg.type == web.WSMsgType.ERROR:
                    # FIXME
                    pass
                elif msg.type == web.WSMsgType.CLOSE:
                    break
        finally:
            request.app["websockets"].discard(ws)

        return ws

    async def update_clients(self):
        import random

        while True:
            # create a model mutation
            for graph in self.graphs:
                s = random.choice(graph.services)
                s.status = random.choice(["running", "stopped", "degraded"])
                self._generation += 1
            await asyncio.sleep(random.randint(1, 3))

    async def cleanup_ws(self, app):
        for ws in app["websockets"]:
            await ws.close(code=WSCloseCode.GOING_AWAY, message="Server Shutdown")

    async def open_viewer(self, app):
        async def view():
            webbrowser.open_new_tab("http://localhost:8080/")

        asyncio.create_task(view())

    async def get_interface(self, request):
        name = request.match_info["name"]
        interface = self.store.interface[name]
        return web.json_response(interface.serialized())

    async def get_component(self, request):
        name = request.match_info["name"]
        comp = self.store.component.get(name)
        return web.json_response(comp.serialized())

    async def loader(self, request):
        # XXX: simple impl here is a DoS vector
        data = await request.read()
        fh = StringIO(data.decode("utf-8"))
        fh.name = "<upload>"
        schema.load_and_store(fh, self.store)

    def serve_forever(self, store, update=False):
        self.store = store
        if update:
            self.updater = True

        app = web.Application()
        app["websockets"] = set()
        # app.on_startup.append(self.open_viewer)
        app.on_shutdown.append(self.cleanup_ws)
        app.add_routes(
            [
                web.get("/", self.index),
                # web.request("/api/v1/service/<name>", self.update_service)
                web.get("/api/v1/interface/{name}", self.get_interface),
                web.get("/api/v1/component/{name}", self.get_component),
                web.post("/api/v1/loader", self.loader),
                web.get("/ws", self.handle),
            ]
        )
        self.app = app
        web.run_app(app)


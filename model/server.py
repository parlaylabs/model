import asyncio
import webbrowser

import aiohttp
from aiohttp import web

from . import utils


class Server:
    def __init__(self, graphs):
        self.graphs = graphs

    async def sync(self, ws):
        for graph in self.graphs:
            await ws.send_str(utils.dump(graph))

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # dump all graph state on client connect
        await self.sync(ws)

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                print(f"got {msg.data}")
                await ws.send_str("Hello, {}".format(msg.data))
            elif msg.type == web.WSMsgType.BINARY:
                await ws.send_bytes(msg.data)
            elif msg.type == web.WSMsgType.ERROR:
                # FIXME
                pass
            elif msg.type == web.WSMsgType.CLOSE:
                break
        return ws

    def serve_forever(self):
        app = web.Application()
        app.add_routes(
            [web.get("/", self.handle),]
        )
        web.run_app(app)


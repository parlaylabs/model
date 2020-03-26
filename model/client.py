import asyncio

import aiohttp

from 

class Client:
    def __init__(self, addr):
        self.addr = addr

    async def post(self, path, data):
        # XXX: url join
        url = f"{self.addr}/{self.path}"
        async with aiohttp.ClientSession() as session:
            return await session.post(url, data)
    
    async def filedata(self, filenames):
        data = aiohttp.FormData()
        if isinstance(filenames, str):
            filenames = [filenames]
        for fn in filenames:
            fn = Path(fn)
            fh = open(fn, "rb", encoding="utf-8")
            data.add_field(fn.name, fh, filename=fn.name, content_type="text/x-yaml")
    
    
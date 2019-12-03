import io
import sys

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

from . import utils


@contextmanager
def streamer(fn_or_fp):
    closing = False
    if str(fn_or_fp) == "-":
        fn_or_fp = sys.stdout

    if isinstance(fn_or_fp, io.TextIOBase):
        fp = fn_or_fp
    else:
        fp = open(fn_or_fp, "w", encoding="utf-8")
        closing = True
    yield fp
    if closing:
        fp.close()


@dataclass
class Output:
    name: str
    data: Dict[str, Any]
    annotations: Dict[str, Any]


class Renderer(list):
    def __init__(self, root=None):
        self.root = Path(root)
        self.index = {}  # name -> ent

    def add(self, name, data, plugin, **kwargs):
        annotations = kwargs
        annotations["plugin"] = plugin
        ent = Output(name, data, annotations)
        self.append(ent)
        if ent.name in self.index:
            # we are replacing an old entity
            # we could/should notify user?
            pass
        self.index[ent.name] = ent

    def interpolate(self):
        # This happens after all the base data has been added, it allows
        # more sophisticated interpolations
        for ent in self:
            ctx = {}
            ctx.update(ent.data)
            ctx.update(ent.annotations)
            ent.data = utils.interpolate(ent.data, ctx)

    def __contains__(self, key):
        return key in self.index


class DirectoryRenderer(Renderer):
    def write(self):
        if not self.root.exists():
            self.root.mkdir()
        self.interpolate()
        for ent in self:
            ofn = self.root / ent.name
            with open(ofn, "w", encoding="utf-8") as fp:
                data = ent.data
                if not isinstance(data, list):
                    data = [data]
                print("---", file=fp)
                yaml.safe_dump_all(data, stream=fp)


class FileRenderer(Renderer):
    def write(self):
        self.interpolate()
        with streamer(self.root) as fp:
            for ent in self:
                data = ent.data
                if not isinstance(ent, list):
                    data = [data]
                print("---", file=fp)
                yaml.safe_dump_all(data, stream=fp)


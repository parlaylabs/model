import functools
import io
import logging
import sys

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import jmespath
import jsonmerge
import yaml

from . import utils

log = logging.getLogger(__package__)


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

    def update(self, data, schema=None):
        if not schema:
            schema = {}
        utils.merge_paths(self, data, schema=schema)


def _match_plugin(item, query):
    plugins = item.annotations.get("plugin", None)
    plugin = query["plugin"]
    if isinstance(plugins, list):
        return any([(plugin == p) for p in plugins])
    else:
        return plugin == plugins
    return False


class Renderer(list):
    def __init__(self, root=None):
        self.root = Path(root)
        self.index = {}  # name -> ent

    def add(self, name, data, plugin, **kwargs):
        if name in self.index:
            # we are replacing an old entity
            # we could/should notify user?
            log.warning(
                f"{name} already exists in output, either use update() or fix logic"
            )
            return
        annotations = kwargs
        annotations["plugin"] = [plugin]
        ent = Output(name, data, annotations)
        self.append(ent)
        self.index[ent.name] = ent

    def update(self, name, data, plugin, schema=None, **kwargs):
        """
        Calls to update must pass data compatable with the utils.merge_paths
        overrides convention. This will update Output objects using that style of override.
        """
        ent = utils.pick(self, name=name)
        if not ent:
            raise KeyError(f"Attempting to update missing output entry {name}")
        plugins = ent.annotations.setdefault("plugin", [])
        if plugin not in plugins:
            plugins.append(plugin)
        if data:
            if not schema:
                schema = {}
            ent.update(data, schema=schema)

    def pick(self, **kwargs):
        plugin = kwargs.get("plugin")
        if plugin:
            kwargs["predicate"] = functools.partial(_match_plugin, query=plugin)
        return utils.filter_iter(self, **kwargs)

    def filter(self, **kwargs):
        return self.pick(reversed=True, **kwargs)

    def __contains__(self, key):
        return key in self.index


class DirectoryRenderer(Renderer):
    def write(self):
        if not self.root.exists():
            self.root.mkdir()
        for ent in self:
            ofn = (self.root / ent.name).resolve()
            ofn.parent.mkdir(mode=0o744, parents=True, exist_ok=True)
            with open(ofn, "w", encoding="utf-8") as fp:
                data = ent.data
                if not isinstance(data, list):
                    data = [data]
                print("---", file=fp)
                yaml.dump_all(data, stream=fp)


class FileRenderer(Renderer):
    def write(self):
        with streamer(self.root) as fp:
            for ent in self:
                data = ent.data
                if not isinstance(ent, list):
                    data = [data]
                print("---", file=fp)
                yaml.dump_all(data, stream=fp)


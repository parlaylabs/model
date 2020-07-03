import base64
import itertools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

from . import config
from . import docker
from . import exceptions
from . import render
from . import utils

log = logging.getLogger(__name__)
_marker = object()
_plugins = {}
_runtimes = {}


def register(cls):
    _plugins[cls.__name__.lower()] = cls
    return cls


@dataclass
class RuntimePlugin:
    name: str
    config: dict = field(init=False, default_factory=utils.AttrAccess)


@dataclass(unsafe_hash=True)
class RuntimeImpl:
    name: str = field(hash=True)
    kind: str = field(init=False, hash=True, default="RuntimeImpl")
    plugins: List[RuntimePlugin] = field(hash=False)

    def __post_init__(self):
        for p in self.plugins:
            setattr(p, "runtime_impl", self)

    def serialized(self):
        return dict(
            name=self.name, kind=self.kind, plugins=[p.name for p in self.plugins]
        )

    def plugin(self, key):
        return utils.pick(self.plugins, name=key)

    @property
    def qual_name(self):
        return self.name

    def lookup(self, name, reverse=True, default=_marker):
        plugins = self.plugins
        if reverse is True:
            plugins = reversed(plugins)

        for p in plugins:
            m = getattr(p, name, None)
            if m:
                return m
        if default is not _marker:
            return default
        raise AttributeError(f"RuntimeImpl plugins didn't provide an attribute {name}")

    def method_lookup(self, name, reverse=True):
        try:
            m = self.lookup(name, reverse=reverse)
        except AttributeError:
            m = None
        if not callable(m):
            raise TypeError(f"method_lookup() expect to find a method")
        return m

    def __getattr__(self, key):
        return self.lookup(key)


def render_graph(graph, outputs):
    # TODO: split the rendering of relations to support 1/2 living in another runtime
    #       ex render_relation_ep(relation.ep)
    runtimes = set()
    # 1st collect all the runtimes referenced in the graph
    for obj in graph.services:
        if obj.runtime is not None:
            runtimes.add(obj.runtime)

    for runtime in runtimes:
        for plugin in runtime.plugins:
            m = getattr(plugin, "init", None)
            if m:
                m(graph, outputs)

    # Here we must resolve the correct runtime to process each
    for phase in ["pre_", "", "post_"]:
        # dynamic method resolution in the form of
        # <phase>_render_<kind.lower>
        for obj in graph.services:
            runtime = obj.runtime
            if not runtime:
                continue
            for plugin in runtime.plugins:
                kind = obj.kind.lower()
                mn = f"{phase}render_{kind}"
                m = getattr(plugin, mn, None)
                if m:
                    m(obj, graph, outputs)
        for obj in graph.relations:
            for endpoint in obj.endpoints:
                runtime = endpoint.service.runtime
                if not runtime:
                    continue
                for plugin in runtime.plugins:
                    kind = obj.kind.lower()
                    mn = f"{phase}render_relation_ep"
                    m = getattr(plugin, mn, None)
                    if m:
                        m(obj, endpoint, graph, outputs)

    for runtime in runtimes:
        for plugin in runtime.plugins:
            m = getattr(plugin, "fini", None)
            if m:
                m(graph, outputs)


def resolve(runtime_name, store):
    global _runtimes
    # Look for a runtime entry in the store
    if not runtime_name:
        return None
    runtime_name = runtime_name.lower()
    if runtime_name in _runtimes:
        return _runtimes[runtime_name]
    rspec = store.runtime[runtime_name]
    plugins = resolve_plugins(rspec.plugins)
    for plugin in plugins:
        m = getattr(plugin, "load", None)
        if m:
            m()
    runtime = RuntimeImpl(runtime_name, plugins=plugins)
    _runtimes[runtime_name] = runtime
    store.add(runtime)

    return runtime


def resolve_plugins(plugins):
    impls = []
    ctx = config.get_context()
    for p in plugins:
        plug = None
        name = p.get("name", "").lower()
        path = p.get("path")
        package = p.get("package")
        if path:
            log.debug(f"loading plugin {name}::{path}")
            cls = utils.import_object(path, package=package)
            plug = cls()
            if not name:
                name = plug.__class__.__name__.lower()
            _plugins[name] = plug
            log.debug(f"loaded plugin {name}::{plug}")
        else:
            if name not in _plugins:
                # attempt to load it from the runtimes submodule
                utils.import_submodules("model.runtimes")
                plug = _plugins[name]()
        cfg = p.get("config")
        if cfg:
            cfg = utils.interpolate(cfg, ctx)
            plug.config.update(cfg)
        if plug is None:
            raise exceptions.ConfigurationError(f"Unable to resolve {p}")
        impls.append(plug)
    return impls

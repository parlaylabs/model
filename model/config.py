import logging
import threading
from pathlib import Path

import click
import coloredlogs
import yaml

from . import runtime as runtime_impl
from . import schema, server, store, utils

cmd_name = __package__.split(".")[0]
log = logging.getLogger(cmd_name)


class ModelConfig:
    def __init__(self):
        self.store = store.Store()
        self._environment = None
        self._context = utils.MergingChainMap()

    def get_runtime(self, name=None):
        if not self.store:
            return
        rts = list(self.store.runtime.keys())
        if len(rts) == 1:
            if name and rts[0] != name:
                log.warning(f"Requesting env {name} but only {rts} are configured")
            name = rts[0]
            log.debug(f"Using only provided runtime {name} for config")
        else:
            if not name:
                name = self.find("runtime")

        return runtime_impl.resolve(name, self.store)

    def get_environment(self, name=None):
        save = name is None
        if not self.store:
            return
        if self._environment:
            return self._environment

        envs = list(self.store.environment.keys())
        if len(envs) == 1:
            if name and envs[0] != name:
                log.warning(f"Requesting env {name} but only {envs} are configured")
            name = envs[0]
            log.debug(f"Using only provided environment {name} for config")
        else:
            if not name:
                name = self.find("environment")
        env = self.store.environment[name]
        if save:
            self._environment = env
        return env

    @property
    def environment(self):
        return self.get_environment()

    def find(self, name, default=None, ctx=None):
        if not ctx:
            ctx = click.get_current_context()
        while ctx:
            val = ctx.params.get(name)
            if val:
                return val
            ctx = ctx.parent
        return default

    def setup_logging(self):
        level = self.find("log_level").upper()
        logging.basicConfig(level=level)

        field_styles = dict(
            asctime=dict(color=241),
            hostname=dict(color=241),
            levelname=dict(color=136, bold=True),
            programname=dict(color=234),
            name=dict(color=61),
            message=dict(),
        )

        level_styles = dict(
            spam=dict(color=240, faint=True),
            debug=dict(color=241),
            verbose=dict(color=254),
            info=dict(color=244),
            notice=dict(color=166),
            warning=dict(color=125),
            success=dict(color=64, bold=True),
            error=dict(color=160),
            critical=dict(color=160, bold=True),
        )
        coloredlogs.install(
            level=level,
            field_styles=field_styles,
            level_styles=level_styles,
            fmt="[%(name)s:%(levelname)s] [%(filename)s:%(lineno)s (%(funcName)s)] %(message)s",
        )
        logging.getLogger("jsonmerge").setLevel(logging.WARNING)
        logging.getLogger("botocore").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    def parse_model_config(self, pathname: Path):
        conf = {}
        if not pathname.exists:
            return conf
        text = pathname.read_text(encoding="utf-8")
        conf = yaml.safe_load(text)
        return conf

    def load_configs(self):
        cd = self.find("config_dir")
        if not cd:
            return
        cd = list(cd)

        # see if there is  [~/.model.conf', '.model.conf']
        paths = filter(
            lambda p: p.exists(),
            [Path("~/.model.conf").expanduser(), Path(".model.conf")],
        )
        for p in paths:
            conf = self.parse_model_config(p)
            bases = conf.get("bases")
            if bases:
                for b in reversed(bases):
                    cd.insert(0, b)

        for d in cd:
            schema.load_config(self.store, d)

    def init(self):
        self.setup_logging()
        self.load_configs()
        # The graph can define a default runtime but any service in the graph could specify another
        # the idea of what a runtime is belongs to the imported Runtime object of the name referenced
        # by the object (service)
        self.runtime = self.get_runtime()
        _set_model_config(self)

    def context(self, **kwargs):
        ctx = self._get_context()
        if ctx is None:
            ctx = self._context
            ctx["environment"] = self.environment
            ctx["runtime"] = self.runtime

        return ctx.new_child(kwargs)

    def _get_context(self):
        tl = threading.local()
        ctx = getattr(tl, "model_context", None)
        return ctx

    def set_context(self, ctx=None):
        tl = threading.local()
        if ctx is None:
            try:
                del tl.model_context
            except AttributeError:
                pass
        else:
            tl.model_context = ctx


_config = None


def get_model_config():
    global _config
    if _config is None:
        _config = ModelConfig()
    return _config


def _set_model_config(cfg):
    global _config
    _config = cfg
    return cfg


def get_context(**kwargs):
    cfg = get_model_config()
    ctx = cfg.context(**kwargs)
    return ctx


def set_context(ctx=None):
    cfg = get_model_config()
    cfg.set_context(ctx)

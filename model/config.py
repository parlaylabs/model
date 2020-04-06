import logging

import click
import coloredlogs

from . import runtime as runtime_impl
from . import schema, server, store, utils

cmd_name = __package__.split(".")[0]
log = logging.getLogger(cmd_name)


class ModelConfig:
    def __init__(self):
        self.store = store.Store()

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
        if not self.store:
            return
        envs = list(self.store.environment.keys())
        if len(envs) == 1:
            if name and envs[0] != name:
                log.warning(f"Requesting env {name} but only {envs} are configured")
            name = envs[0]
            log.debug(f"Using only provided environment {name} for config")
        else:
            if not name:
                name = self.find("environment")
        return self.store.environment[name]

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

    def load_configs(self):
        cd = self.find("config_dir")
        if not cd:
            return
        for d in cd:
            schema.load_config(self.store, d)

    def init(self):
        self.setup_logging()
        self.load_configs()
        self.environment = self.get_environment()
        # The graph can define a default runtime but any service in the graph could specify another
        # the idea of what a runtime is belongs to the imported Runtime object of the name referenced
        # by the object (service)
        self.runtime = self.get_runtime()


_config = None


def get_model_config():
    global _config
    if _config is None:
        _config = ModelConfig()
    return _config

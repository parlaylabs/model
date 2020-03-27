import logging
import os
import subprocess
import tempfile
from pathlib import Path

import click
import coloredlogs

from .. import entity
from .. import exceptions
from .. import graph as graph_manager
from .. import model
from .. import pipeline
from .. import render as render_impl
from .. import runtime as runtime_impl
from .. import schema, server, store
from .. import utils
from .clicktools import spec, using

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
        # logging.getLogger("jsonmerge").setLevel(logging.WARNING)
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


_log_levels = ["DEBUG", "INFO", "WARNING", "CRITICAL"]
_log_levels = _log_levels + [i.lower() for i in _log_levels]
common_args = [
    spec("-l", "--log-level", default="INFO", type=click.Choice(_log_levels),),
    spec(
        "-c",
        "--config-dir",
        multiple=True,
        type=click.Path(exists=True, file_okay=True, dir_okay=True, readable=True),
    ),
]


@click.group()
@using(ModelConfig, common_args)
def main(config, **kwargs):
    pass


@main.command()
@using(ModelConfig, common_args)
def init(ctx):
    print(f"Init {cmd_name}")


@main.group()
@using(ModelConfig, common_args)
def component(ctx):
    pass


@component.command()
@using(ModelConfig, common_args)
def init(config, src_ref):
    # XXX: This is just a testing impl, no flexability
    pass


@main.group()
@using(ModelConfig, common_args)
def pipeline(config, **kwargs):
    pass


@pipeline.command()
@using(ModelConfig, common_args)
@click.argument("pipeline_name")
@click.argument("segment", required=False, default=None, nargs=-1)
def run(config, pipeline_name, segment, **kwargs):
    # First load in the graph references from config
    # then fine the pipeline object referenced by name
    # trigger the pipeline using the graph
    # to either supply arguments or to pass the complete structure
    config.init()
    pipeline = config.store.pipeline.get(pipeline_name)
    pipeline.runtime = config.get_runtime(pipeline.runtime)
    if not pipeline:
        raise exceptions.ConfigurationError(
            f"unable to find a pipeline {pipeline_name}. Aborting."
        )
    pipeline.run(config.store, config.environment, segments=segment)


graph_common = [
    spec("-e", "--environment"),
]


@main.group()
@using(ModelConfig, common_args, graph_common)
def graph(config, **kwargs):
    pass


@graph.command()
@using(ModelConfig, common_args, graph_common)
def plan(config, **kwargs):
    config.init()
    graphs = config.store.graph.values()
    for graph in graphs:
        graph = graph_manager.plan(graph, config.store, environment=config.environment,)
        print(f"plan graph {graph}")


@graph.command()
@using(ModelConfig, common_args, graph_common)
@click.option("-o", "--output-dir", default="-")
def render(config, output_dir, **kwargs):
    config.init()
    graphs = config.store.graph.values()
    # Apply should be graph at a time
    # or at least a single runtime

    if output_dir == "-":
        ren = render_impl.FileRenderer(output_dir)
    else:
        ren = render_impl.DirectoryRenderer(output_dir)

    for graph in graphs:
        graph = graph_manager.plan(graph, config.store, environment=config.environment)
        graph_manager.apply(graph, config.store, config.runtime, ren)


@graph.command()
@using(ModelConfig, common_args, graph_common)
@click.option("-o", "--output-dir", default=None)
def up(config, output_dir, **kwargs):
    config.init()
    graphs = config.store.graph.values()
    # Apply should be graph at a time
    # or at least a single runtime

    if not output_dir:
        output_dir = tempfile.mkdtemp("-base", prefix="model-")
        log.info(f"Rendering model output to {output_dir}")

    ren = render_impl.DirectoryRenderer(output_dir)

    for graph in graphs:
        graph = graph_manager.plan(graph, config.store, environment=config.environment)
        graph_manager.apply(graph, config.store, config.runtime, ren)
    subprocess.run(f"kubectl apply -k {output_dir}", shell=True)


@graph.command()
@using(ModelConfig, common_args)
@click.option("--update/--no-update", default=False)
def develop(config, update, **kwargs):
    config.init()
    # launch a development server for testing
    graphs = []

    try:
        graph_ents = config.store.graph.values()
    except KeyError:
        graph_ents = []

    for graph in graph_ents:
        graphs.append(
            graph_manager.plan(
                graph, store=config.store, environment=config.environment,
            )
        )
    srv = server.Server(graphs)
    srv.serve_forever(store=config.store, update=update)


@graph.command()
@using(ModelConfig, common_args, graph_common)
def shell(config, **kwargs):
    import atexit
    import code
    import readline
    import sys
    from jedi.utils import setup_readline

    histfile_name = ".python_history"

    config.init()
    # launch a development server for testing
    graph_ents = config.store.graph.values()
    graphs = []
    for graph in graph_ents:
        graphs.append(
            graph_manager.plan(
                graph, store=config.store, environment=config.environment,
            )
        )

    output = render_impl.FileRenderer("-")

    def renderer(x):
        runtime_impl.render_graph(x, output)
        output.write()

    ns = {
        "graphs": graphs,
        "store": config.store,
        "config": config,
        "dump": lambda x: print(utils.dump(x)),
        "render": renderer,
        "output": output,
        "utils": utils,
    }
    if graphs:
        ns["g"] = graphs[0]

    class O:
        def __init__(self, ns):
            self.__dict__ = ns

    setup_readline(O(ns))

    env = os.environ.get("VIRTUAL_ENV")

    if env:
        env_name = os.path.basename(env)
        histfile_name = "{}_{}".format(histfile_name, env_name)
        name = graphs[0].name if graphs else "default"
        sys.ps1 = f"model ({name}) >>> "

    # set history file
    try:
        histfile = os.path.join(os.environ["XDG_CACHE_HOME"], "python", histfile_name)
    except KeyError:
        histfile = os.path.join(os.environ["HOME"], ".cache", "python", histfile_name)

    Path(os.path.dirname(histfile)).mkdir(parents=True, exist_ok=True)

    try:
        readline.read_history_file(histfile)
        # default history len is -1 (infinite), which may grow unruly
        readline.set_history_length(1000)
    except FileNotFoundError:
        pass

    atexit.register(readline.write_history_file, histfile)
    banner = f"""model interactive shell
{sorted(ns.keys())}
    """
    code.interact(banner, local=ns)


if __name__ == "__main__":
    obj = {"store": store.Store()}
    main(prog_name="model", auto_envvar_prefix="MODEL", context_settings=obj)

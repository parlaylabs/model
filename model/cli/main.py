import logging
import os
import subprocess
import tempfile
from pathlib import Path

import click

from .. import entity
from .. import exceptions
from .. import graph as graph_manager
from .. import model
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
        logging.basicConfig(level=self.find("log_level").upper())
        logging.getLogger("jsonmerge").setLevel(logging.WARNING)

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


common_args = [
    spec(
        "-l",
        "--log-level",
        default="INFO",
        type=click.Choice(["DEBUG", "INFO", "WARNING", "CRITICAL"]),
    ),
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
        output_dir = tempfile.mkdtemp("base")
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
    import code
    from jedi.utils import setup_readline

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

    output = render.FileRenderer("-")

    def renderer(x):
        runtime_impl.render_graph(x, output)
        output.write()

    ns = {
        "graphs": graphs,
        "g": graphs[0],
        "store": config.store,
        "config": config,
        "dump": lambda x: print(utils.dump(x)),
        "render": renderer,
    }

    class O:
        def __init__(self, ns):
            self.__dict__ = ns

    setup_readline(O(ns))
    code.interact("model interactive shell", local=ns)


if __name__ == "__main__":
    obj = {"store": store.Store()}
    main(prog_name="model", auto_envvar_prefix="MODEL", context_settings=obj)

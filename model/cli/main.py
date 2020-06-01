import logging
import os
import subprocess
import tempfile
from pathlib import Path

import click
from rich.traceback import install as install_rich_tb

from .. import entity, exceptions
from .. import graph as graph_manager
from .. import model, pipeline
from .. import render as render_impl
from .. import runtime as runtime_impl
from .. import schema, server, store, utils
from ..config import get_model_config, _set_model_config
from .clicktools import spec, using

cmd_name = __package__.split(".")[0]
log = logging.getLogger(cmd_name)


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
@using(common_args)
def main(config, **kwargs):
    install_rich_tb()


@main.group()
@using(common_args)
def pipeline(config, **kwargs):
    pass


@pipeline.command()
@using(common_args)
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
    pipeline._interpolate_entities()
    if not pipeline:
        raise exceptions.ConfigurationError(
            f"unable to find a pipeline {pipeline_name}. Aborting."
        )
    pipeline.run(config.store, config.environment, segments=segment)


graph_common = [
    spec("-e", "--environment"),
]


@main.group()
@using(common_args, graph_common)
def graph(config, **kwargs):
    pass


@graph.command()
@using(common_args, graph_common)
def plan(config, **kwargs):
    config.init()
    graphs = config.store.graph.values()
    for graph in graphs:
        graph = graph_manager.plan(graph, config.store, environment=config.environment,)
        print(f"plan graph {graph}")


@graph.command()
@using(common_args, graph_common)
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
@using(common_args, graph_common)
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
@using(common_args)
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
@using(common_args, graph_common)
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
    main(prog_name="model", auto_envvar_prefix="MODEL")

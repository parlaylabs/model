import logging
import os
import subprocess
from pathlib import Path

import click

from .. import entity
from .. import graph as graph_manager
from .. import model
from .. import render
from .. import runtime as runtime_impl
from .. import schema
from .. import server
from .. import store

cmd_name = __package__.split(".")[0]
log = logging.getLogger(cmd_name)


def add_options(options):
    def _add_options(func):
        for option in reversed(options):
            func = option(func)
        return func

    return _add_options


def setupLogging(level):
    logging.basicConfig(level=level)


def load_config(store, config_dir):
    p = Path(config_dir)
    if not p.exists() or not p.is_dir():
        raise OSError("""No config directory -- post alpha this won't be required""")
    for yml in sorted(p.rglob("*.yaml")):
        log.debug(f"Loading config from {yml}")
        schema.load_and_store(yml, store)
    # Validate after all loading is done
    for obj in store.qual_name.values():
        obj.validate()


def load_configs(store, dirs):
    for d in dirs:
        schema.load_config(store, d)


common_args = [click.option("-c", "--config-dir", multiple=True, default="conf")]

# def ensure(ctx, **kwargs):
#    ctx.ensure_object(dict)

# handlers = {
#    None: ensure
#    "log_level":
# }


@click.group()
@click.option("-l", "--log-level", type=str, default="INFO")
@click.pass_context
def main(ctx, log_level):
    ctx.ensure_object(dict)
    setupLogging(log_level.upper())
    s = ctx.obj["store"] = store.Store()


@main.command()
@click.pass_context
def init(ctx):
    print(f"Init {cmd_name}")


@main.group()
@click.pass_context
def component(ctx):
    pass


@component.command()
@click.argument("src_ref", type=str)
@click.pass_context
def init(ctx, src_ref):
    # Mock impl. For now just write the changes to a known file
    print(f"init {src_ref}")


graph_common = [
    click.option("-e", "--environment"),
    click.option("-r", "--runtime", default="kubernetes"),
]


@main.group()
@add_options(common_args)
@add_options(graph_common)
@click.pass_context
def graph(ctx, environment, runtime, config_dir):
    s = ctx.obj["store"]
    load_configs(s, config_dir)
    ctx.obj["runtime"] = runtime_impl.resolve(runtime, s)
    ctx.obj["environment"] = s.qual_name[f"Environment:{environment}"]


@graph.command()
@add_options(common_args)
@add_options(graph_common)
@click.pass_context
def plan(ctx, **kwargs):
    runtime = ctx.obj.get("runtime")
    env = ctx.obj["environment"]
    s = ctx.obj["store"]
    graphs = s["kind"].get("Graph")
    if not graphs:
        raise KeyError("No graphs to plan in config")
    graphs = graphs["name"].values()
    for graph in graphs:
        graph = graph_manager.plan(graph, s, env, runtime)
        print(f"plan graph {graph}")
    log.debug(ctx.obj["store"])


@graph.command()
@add_options(graph_common)
@click.option("-o", "--output-dir", default="-")
@click.option("-k", "--kustomize")
@click.pass_context
def apply(ctx, environment, runtime, output_dir, kustomize):
    s = ctx.obj["store"]
    runtime = ctx.obj.get("runtime")
    graphs = s["kind"].get("Graph")
    environment = ctx.obj["environment"]
    if not graphs:
        raise KeyError("No graphs to plan in config")
    graphs = graphs["name"].values()
    # Apply should be graph at a time
    # or at least a single runtime

    if kustomize and output_dir == "-":
        raise RuntimeError("You must output to a directory using -o <dir> to kustomize")

    if output_dir == "-":
        ren = render.FileRenderer(output_dir)
    else:
        ren = render.DirectoryRenderer(output_dir)

    for graph in graphs:
        graph = graph_manager.plan(graph, s, environment, runtime)
        graph_manager.apply(graph, store, runtime, ren)

    if kustomize:
        subprocess.run(f"kubectl kustomize {kustomize}", shell=True)


@graph.command()
@click.pass_context
def develop(ctx):
    # launch a development server for testing
    s = ctx.obj["store"]
    runtime = ctx.obj.get("runtime")
    graphs = s["kind"].get("Graph")
    if not graphs:
        raise KeyError("No graphs to serve in config")
    graph_ents = graphs["name"].values()
    graphs = []
    for graph in graph_ents:
        graphs.append(graph_manager.plan(graph, s, runtime))
    srv = server.Server(graphs)
    srv.serve_forever()


if __name__ == "__main__":
    main(auto_envvar_prefix="PULP")

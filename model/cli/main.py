import logging
import os
from pathlib import Path

import click

from .. import entity
from .. import graph as graph_manager
from .. import model
from .. import schema
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
    for yml in p.rglob("*.yaml"):
        log.debug(f"Loading config from {yml}")
        schema.load_and_store(yml, store)
    # Validate after all loading is done
    for obj in store.qual_name.values():
        obj.validate()


common_args = [click.option("-c", "--config-dir", default="conf")]


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


graph_common = [click.option("-r", "--runtime", default="kubernetes")]


@main.group()
@add_options(common_args)
@add_options(graph_common)
@click.pass_context
def graph(ctx, runtime, config_dir):
    s = ctx.obj["store"]
    load_config(s, config_dir)
    ctx.obj["runtime"] = model.Runtime(name=runtime)


@graph.command()
@add_options(graph_common)
@click.pass_context
def plan(ctx, runtime):
    runtime = ctx.obj.get("runtime")
    s = ctx.obj["store"]
    graphs = s["kind"].get("Graph")
    if not graphs:
        raise KeyError("No graphs to plan in config")
    graphs = graphs["name"].values()
    for graph in graphs:
        graph = graph_manager.plan(graph, s, runtime)
        print(f"plan graph {graph}")
    log.debug(ctx.obj["store"])


@graph.command()
@add_options(graph_common)
@click.pass_context
def apply(ctx, runtime):
    s = ctx.obj["store"]
    runtime = ctx.obj.get("runtime")
    graphs = s["kind"].get("Graph")
    if not graphs:
        raise KeyError("No graphs to plan in config")
    graphs = graphs["name"].values()
    # Apply should be graph at a time
    # or at least a single runtime
    for graph in graphs:
        graph = graph_manager.plan(graph, s, runtime)
        graph_manager.apply(graph, store, runtime)
    pass


if __name__ == "__main__":
    main(auto_envvar_prefix="PULP")

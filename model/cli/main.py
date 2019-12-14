import logging
import os
import subprocess
from pathlib import Path

import click

from .. import entity
from .. import graph as graph_manager
from .. import model, render
from .. import runtime as runtime_impl
from .. import schema, server, store
from .clicktools import spec, using

cmd_name = __package__.split(".")[0]
log = logging.getLogger(cmd_name)


class ModelConfig:
    def __init__(self):
        self.store = store.Store()

    def get_runtime(self, name=None):
        if not name:
            name = self.find("runtime")
        return runtime_impl.resolve(name, self.store)

    def get_environment(self, name=None):
        if not name:
            name = self.find("environment")
        return self.store.qual_name[f"Environment:{name}"]

    def find(self, name, ctx=None):
        if not ctx:
            ctx = click.get_current_context()
        while ctx:
            val = ctx.params.get(name)
            if val:
                return val
            ctx = ctx.parent
        raise KeyError(f"missing required param {name}")

    def setup_logging(self):
        logging.basicConfig(level=self.find("log_level").upper())
        logging.getLogger("jsonmerge").setLevel(logging.WARNING)

    def load_configs(self):
        for d in self.find("config_dir"):
            schema.load_config(self.store, d)

    def init(self):
        self.setup_logging()
        self.load_configs()
        self.environment = self.get_environment()
        # XXX: this should be per object in graph, not global
        self.runtime = self.get_runtime()


common_args = [
    spec("-l", "--log-level", default="INFO", type=str),
    spec("-c", "--config-dir", multiple=True),
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
@click.argument("src_ref", type=str)
def init(config, src_ref):
    # Mock impl. For now just write the changes to a known file
    print(f"init {src_ref}")


graph_common = [
    spec("-e", "--environment"),
    spec("-r", "--runtime", default="kubernetes"),
]


@main.group()
@using(ModelConfig, common_args, graph_common)
def graph(config, **kwargs):
    pass


@graph.command()
@using(ModelConfig, common_args, graph_common)
def plan(config, **kwargs):
    config.init()
    graphs = config.store["kind"].get("Graph")
    if not graphs:
        raise KeyError("No graphs to plan in config")
    graphs = graphs["name"].values()
    for graph in graphs:
        graph = graph_manager.plan(
            graph, config.store, config.environment, config.runtime
        )
        print(f"plan graph {graph}")


@graph.command()
@using(ModelConfig, common_args, graph_common)
@click.option("-o", "--output-dir", default="-")
@click.option("-k", "--kustomize")
def apply(config, output_dir, kustomize, **kwargs):
    config.init()
    graphs = config.store["kind"].get("Graph")
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
        graph = graph_manager.plan(
            graph, config.store, config.environment, config.runtime
        )
        graph_manager.apply(graph, config.store, config.runtime, ren)

    if kustomize:
        subprocess.run(f"kubectl kustomize {kustomize}", shell=True)


@graph.command()
@using(ModelConfig, common_args)
@click.option("--update/--no-update", default=False)
def develop(config, update, **kwargs):
    config.init()
    # launch a development server for testing
    graphs = config.store["kind"].get("Graph")
    if not graphs:
        raise KeyError("No graphs to serve in config")
    graph_ents = graphs["name"].values()
    graphs = []
    for graph in graph_ents:
        graphs.append(graph_manager.plan(graph, config.store, config.runtime))
    srv = server.Server(graphs)
    srv.serve_forever(store=config.store, update=update)


if __name__ == "__main__":
    obj = {"store": store.Store()}
    main(prog_name="model", auto_envvar_prefix="MODEL", context_settings=obj)

import logging

from dataclasses import dataclass
from typing import Any, Dict

from . import model
from . import runtime as runtime_impl
from . import utils

log = logging.getLogger(__name__)


@dataclass
class Graph:
    nodes: Dict[str, model.Service]
    edges: Dict[str, model.Relation]
    model: model.GraphObj
    runtime: runtime_impl
    environment: model.Environment

    @property
    def services(self):
        return self.nodes

    @property
    def relations(self):
        return self.edges

    def serialized(self):
        return dict(nodes=self.nodes, links=self.edges, model=self.model)

    def __post_init__(self):
        # Inject the graph objects belong to so they can resolve other objects
        for entity in self.nodes:
            entity.graph = self


def plan(graph_entity, store, environment, runtime=None):
    # for now this is semantic object validation (beyond what schemas give us)
    services = {}
    relations = {}
    components = {}

    for comp_spec in graph_entity.get("components", []):
        # ensure we have a component defintion for each entry
        name = comp_spec.get("name")
        comp = store["kind"]["Component"]["name"].get(name)
        if not comp:
            raise ValueError(f"graph references unknown component {comp_spec}")

        # Combine graph config with raw component data as a new facet on the entity
        # XXX: src could/should be a global graph reference
        comp.add_facet(comp_spec, "<graph>")
        c_eps = comp.get("endpoints", [])
        exposed = comp_spec.get("expose", [])
        if exposed:
            for ep in exposed:
                if not utils.pick(c_eps, name=ep):
                    raise ValueError(f"Unable to expose unknown endpoint {ep}")

        s = model.Service(
            entity=comp, name=name, runtime=runtime, config=comp_spec.get("config", {}),
        )

        for ep in c_eps:
            s.add_endpoint(name=ep["name"], interface=ep["interface"])
        components[name] = comp
        services[s.name] = s
        store.add(s)

    for relation in graph_entity.get("relations", []):
        # each relation is a list of "comp":"endpoint"
        # ensure each exists on the components in question and that the
        # endpoint's interface is compatable (the same for now)
        ifaces = set()
        endpoints = []
        for ep_spec in relation:
            cname, _, epname = ep_spec.partition(":")
            c = store["kind"]["Component"]["name"][cname]
            c_eps = c.get("endpoints", [])
            # XXX: cname and service name will not be the same in the future
            s = services[cname]
            ep = s.get_endpoint(name=epname)
            log.debug(f"planning {ep_spec} {epname}")
            ifaces.add(ep.interface)
            endpoints.append(ep)
        if len(ifaces) != 1:
            raise ValueError(
                f"More than one interface used in relation {relation} {ifaces}"
            )
        r = model.Relation(endpoints=endpoints)
        relations[r.name] = r
        store.add(r)

    g = Graph(
        model=graph_entity,
        nodes=list(services.values()),
        edges=list(relations.values()),
        runtime=runtime,
        environment=environment,
    )
    # view(g)
    return g


def apply(graph, store, runtime, ren):
    runtime.render(graph, ren)
    ren.write()


def view(g):
    from pathlib import Path
    import graphviz
    import webbrowser

    gv = graphviz.Graph(format="svg")
    with gv.subgraph(name=g.model.name, comment=g.model.name) as cluster:
        for rel in g.relations:
            cluster.edge(
                *[ep.qual_name for ep in rel.endpoints],
                label=rel.endpoints[0].interface,
            )

        # There might be unconnected services (which is broken but we want to show here)
        # for service in g.services:
        #    cluster.node(service.name)
    # gv.view()
    fn = gv.render()
    fn = f"file://{Path.cwd()}/{fn}"
    webbrowser.open_new_tab(fn)

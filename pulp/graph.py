from dataclasses import dataclass
from typing import Any, Dict

from . import model
from . import runtime as runtime_impl
from . import utils


@dataclass
class Graph:
    nodes: Dict[str, model.Service]
    edges: Dict[str, model.Relation]

    @property
    def services(self):
        return self.nodes

    @property
    def relations(self):
        return self.edges

    def __post_init__(self):
        # Inject the graph objects belong to so they can resolve other objects
        for entity in self.nodes:
            entity.graph = self


def plan(graph_entity, store, runtime=None):
    # for now this is semantic object validation (beyond what schemas give us)
    services = {}
    relations = {}
    components = {}

    if not runtime:
        runtime = "kubernetes"
    runtime = runtime_impl.resolve(runtime)

    for comp_spec in graph_entity.get("components", []):
        # ensure we have a component defintion for each entry
        name = comp_spec.get("name")
        comp = store["kind"]["Component"]["name"].get(name)
        if not comp:
            raise ValueError(f"graph references unknown component {comp_spec}")

        c_eps = comp.get("endpoints", [])
        exposed = comp_spec.get("expose", [])
        if exposed:
            for ep in exposed:
                if not utils.pick(c_eps, name=ep):
                    raise ValueError(f"Unable to expose unknown endpoint {ep}")

        s = model.Service(component=comp, name=name, runtime=runtime)
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
            s = services[cname]
            ep = s.get_endpoint(name=epname)
            ifaces.add(ep.interface)
            # XXX: cname and service name will not be the same in the future
            endpoints.append(ep)
        if len(ifaces) != 1:
            raise ValueError(
                f"More than one interface used in relation {relation} {ifaces}"
            )
        r = model.Relation(endpoints=endpoints)
        relations[r.name] = r
        store.add(r)

    # TODO: must feed graph_ent into the graph used here, we need it for config
    g = Graph(nodes=list(services.values()), edges=list(relations.values()))
    # view(g)
    return g


def apply(graph, store, runtime):
    # use the runtime(s) to create a rendering of base objects
    if not runtime:
        runtime = "kubernetes"
    runtime = runtime_impl.resolve(runtime)

    output = runtime.render(graph)
    output.render()


def view(g):
    from pathlib import Path
    import graphviz
    import webbrowser

    gv = graphviz.Graph(format="svg")
    for rel in g.relations:
        gv.edge(
            *[ep.qual_name for ep in rel.endpoints], label=rel.endpoints[0].interface
        )
    # gv.view()
    fn = gv.render()
    fn = f"file://{Path.cwd()}/{fn}"
    webbrowser.open_new_tab(fn)

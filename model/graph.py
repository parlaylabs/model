import logging

from dataclasses import dataclass
from typing import Any, Dict

import jsonmerge

from . import entity
from . import model
from . import runtime as runtime_impl
from . import utils

log = logging.getLogger(__name__)


@dataclass
class Graph:
    nodes: Dict[str, model.Service]
    edges: Dict[str, model.Relation]
    entity: entity.Entity
    runtime: runtime_impl
    environment: model.Environment
    interfaces: Dict[str, model.Interface]

    @property
    def name(self):
        return self.entity.name

    @property
    def services(self):
        return self.nodes

    @property
    def relations(self):
        return self.edges

    def serialized(self):
        return dict(nodes=self.nodes, links=self.edges, model=self.entity)

    def __post_init__(self):
        # Inject the graph objects belong to so they can resolve other objects
        for entity in self.nodes:
            entity.graph = self


def plan(graph_entity, store, environment, runtime=None):
    # runtime is provided as a default, it can/should be overridden and resolved per Service
    # for now this is semantic object validation (beyond what schemas give us)
    services = {}
    relations = {}
    components = {}
    interfaces = store.kind.get("Interface", {}).get("name", {})
    interface_impls = {}

    for ie in interfaces.values():
        iface = model.Interface(
            entity=ie, name=ie.name, version=ie.get("version", "latest")
        )
        interface_impls[iface.name] = iface

    for service_spec in graph_entity.get("services", []):
        # ensure we have a component defintion for each entry
        name = service_spec.get("name")
        cname = service_spec.get("component", name)
        comp = store["kind"]["Component"]["name"].get(cname)
        # Commonly config comes from the env object, not the graph but we support
        # certain reuable configs none the less
        config = service_spec.get("config", {})
        if not comp:
            raise ValueError(f"graph references unknown component {service_spec}")

        # Combine graph config with raw component data as a new facet on the entity
        # XXX: src could/should be a global graph reference
        comp.add_facet(service_spec, "<graph>")
        c_eps = comp.get("endpoints", [])
        exposed = service_spec.get("expose", [])
        if exposed:
            for ep in exposed:
                if not utils.pick(c_eps, name=ep):
                    raise ValueError(f"Unable to expose unknown endpoint {ep}")

        s = model.Service(entity=comp, name=name, runtime=runtime, config=config)

        for ep in c_eps:
            # look up a known interface if it exists and use
            # its values as defaults
            addresses = ep.get("addresses", [])
            iface_name, _, iface_version = ep["interface"].partition(":")
            if iface_name not in interface_impls:
                print(
                    f"endpoint {ep} using unregistered interface {iface_name} for Service {s.name}"
                )
            # XXX: this would have to improve and be version aware if its
            # going to work this way.
            iface = interface_impls[iface_name]
            defaults = iface.entity.get("defaults", {}).get("addresses").copy()
            if not addresses:
                addresses = defaults
            else:
                # XXX: use arrayMergeById and idRef=/name
                addresses = jsonmerge.merge(
                    defaults, addresses, dict(mergeStrategy="arrayMergeByIndex"),
                )
            ep = s.add_endpoint(name=ep["name"], interface=iface, addresses=addresses)
            log.debug(f"adding endpoint to service {s.name} {ep.qual_name}")

        components[name] = comp
        services[s.name] = s
        store.add(s)

    for relation in graph_entity.get("relations", []):
        # each relation is a list of "comp":"endpoint"
        # ensure each exists on the components in question and that the
        # endpoint's interface is compatable (the same for now)
        ifaces = set()
        endpoints = []
        rel_services = []
        for ep_spec in relation:
            sname, _, epname = ep_spec.partition(":")
            s = services[sname]
            ep = s.get_endpoint(name=epname)
            if not ep:
                log.warn(f"Unable to find endpoint {epname} for {relation} on {s.name}")
            else:
                log.debug(f"planned {ep_spec} for {relation} {ep}")
            ifaces.add(ep.interface.qual_name)
            endpoints.append(ep)
        if len(ifaces) != 1:
            raise ValueError(
                f"More than one interface used in relation {relation} {ifaces}"
            )
        r = model.Relation(endpoints=endpoints)
        relations[r.name] = r
        # link the service and relation
        for ep in endpoints:
            ep.service.relations.append(r)
        store.add(r)

    g = Graph(
        entity=graph_entity,
        nodes=list(services.values()),
        edges=list(relations.values()),
        runtime=runtime,
        environment=environment,
        interfaces=interface_impls,
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

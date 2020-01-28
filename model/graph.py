import logging

from dataclasses import dataclass
from typing import Any, Dict

import jsonmerge

from . import entity
from . import exceptions
from . import model
from . import render
from . import runtime as runtime_impl
from . import store
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
    store: store.Store

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

        for entity in self.nodes:
            fini = getattr(entity, "fini", None)
            if fini:
                fini()

        for entity in self.nodes:
            entity.validate()

    def __getattr__(self, key):
        # Proxy the indexes from store
        # This would allow seeing items in the store that are not
        # in the actual graph, but that can be ok for now
        return getattr(self.store, key)

    def render(self, outputs=None):
        if outputs is None:
            outputs = render.FileRenderer("-")
        runtime_impl.render_graph(self, outputs)
        outputs.write()


def plan(graph_entity, store, environment, runtime=None):
    # runtime is provided as a default, it can/should be overridden and resolved per Service
    # for now this is semantic object validation (beyond what schemas give us)
    services = {}
    relations = {}
    components = {}
    interface_impls = {}

    for ie in store.interface.values():
        iface = model.Interface(
            entity=ie, name=ie.name, version=ie.get("version", "latest"), roles=ie.role
        )
        interface_impls[iface.name] = iface

    for service_spec in graph_entity.get("services", []):
        # ensure we have a component defintion for each entry
        name = service_spec.get("name")
        cname = service_spec.get("component", name)
        comp = store.component.get(cname)
        if not comp:
            raise exceptions.ConfigurationError(
                f"graph references unknown component {service_spec}"
            )
        # Commonly config comes from the env object, not the graph but we support
        # certain reuable configs none the less
        config = service_spec.get("config", {})

        # Combine graph config with raw component data as a new facet on the entity
        # XXX: src could/should be a global graph reference
        comp.add_facet(service_spec, "<graph>")
        c_eps = comp.get("endpoints", [])
        exposed = service_spec.get("expose", [])
        for ep in exposed:
            if not utils.pick(c_eps, name=ep):
                raise exceptions.ConfigurationError(
                    f"Unable to expose unknown endpoint {ep}"
                )

        srt = service_spec.get("runtime", graph_entity.get("runtime", runtime))
        if isinstance(srt, str):
            srt = runtime_impl.resolve(srt, store)

        # XXX: we could/should merge env into config here
        s = model.Service(entity=comp, name=name, runtime=srt, config=config)
        for ep in c_eps:
            # look up a known interface if it exists and use
            # its values as defaults
            iface_name, _, iface_role = ep["interface"].partition(":")
            if iface_name not in interface_impls:
                log.warning(
                    f"endpoint {ep} using unregistered interface {iface_name} for Service {s.name}"
                )
            # XXX: this would have to improve and be version aware if its
            # going to work this way.
            iface = interface_impls.get(iface_name, utils.AttrAccess(name=iface_name))
            if not iface or not utils.pick(iface.roles, name=iface_role):
                raise exceptions.ConfigurationError(
                    f"""Interface not defined or missing expected role '{iface_role}' for '{iface_name}' in Service '{s.name}'. 
                    Interface for endpoints should be defined as interface_name:role."""
                )
            ep = s.add_endpoint(name=ep["name"], interface=iface, role=iface_role)
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
            ep = s.endpoints.get(epname)
            if not ep:
                log.warn(f"Unable to find endpoint {epname} for {relation} on {s.name}")
            else:
                log.debug(f"planned {ep_spec} for {relation} {ep}")
                ifaces.add(ep.interface.qual_name)
                endpoints.append(ep)
        if len(ifaces) != 1:
            raise exceptions.ConfigurationError(
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
        runtime=runtime_impl.resolve(graph_entity.get("runtime", runtime), store),
        environment=environment,
        interfaces=interface_impls,
        store=store,
    )
    return g


def apply(graph, store, runtime, ren):
    runtime_impl.render_graph(graph, ren)
    ren.write()


def view(g):
    from pathlib import Path
    import graphviz
    import webbrowser

    gv = graphviz.Graph(format="svg")
    with gv.subgraph(name=g.name, comment=g.name) as cluster:
        for rel in g.relations:
            cluster.edge(
                *[ep.qual_name for ep in rel.endpoints],
                label=rel.endpoints[0].interface.name,
            )

        # There might be unconnected services (which is broken but we want to show here)
        # for service in g.services:
        #    cluster.node(service.name)
    # gv.view()
    fn = gv.render()
    fn = f"file://{Path.cwd()}/{fn}"
    webbrowser.open_new_tab(fn)

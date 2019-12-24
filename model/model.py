from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

import jsonmerge

from . import entity
from . import schema
from . import utils


@dataclass
class GraphObj:
    kind: str = field(hash=True)
    name: str = field(hash=True)
    entity: entity.Entity
    namespace: str = field(init=False, default="default")
    graph: "GraphObj" = field(init=False, default=None)

    @property
    def qual_name(self):
        return f"{self.kind}:{self.name}"

    def add_facet(self, data, src_ref=None):
        self.entity.add_facet(data, src_ref)

    def validate(self):
        return self.entity.validate()

    def get(self, key, default=None):
        return self.entity.get(key, default)

    def serialized(self):
        return asdict(self)

    def __hash__(self):
        return hash((self.name, self.kind))


@dataclass
class Runtime(GraphObj):
    kind: str = field(init=False, default="Runtime")

    def serialized(self):
        return dict(name=self.name, kind=self.kind)

    @property
    def impl(self):
        # XXX: bad shortcut
        return self.graph.qual_name[f"RuntimeImpl:{self.name}"]

    def __hash__(self):
        return hash((self.name, self.kind))


@schema.register_class
@dataclass
class Environment(GraphObj):
    kind: str = field(init=False, default="Environment")

    @property
    def config(self):
        return self.entity.get("config", {})

    def __hash__(self):
        return hash((self.name, self.kind))


@schema.register_class
@dataclass(unsafe_hash=True)
class Component(GraphObj):
    name: str
    kind: str = field(init=False, default="Component")
    image: str
    version: str


@dataclass
class Service(GraphObj):
    kind: str = field(init=False, hash=True, default="Service")
    endpoints: Dict[str, "Endpoint"] = field(
        init=False, default_factory=utils.AttrAccess
    )
    relations: List = field(init=False, default_factory=list)
    runtime: Runtime
    # TODO: this can be in init and draw config from the graph
    config: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash((self.name, self.kind))

    def add_endpoint(self, name, interface, addresses=None):
        ep = Endpoint(name=name, interface=interface, service=self, addresses=addresses)
        self.endpoints[name] = ep
        return ep

    def get_remote_endpoint(self, epname):
        this = self.endpoints[epname]
        # scan the relations to and return the (relation, endpoint) pair

        return utils.pick(self.endpoints, **kwargs)

    @property
    def exposed(self):
        # XXX: we are combining at runtime rather than at creation
        # change this pattern
        return self.entity.get("expose", [])

    @property
    def exposed_endpoints(self):
        for ex in self.exposed:
            yield self.endpoints[ex]

    @property
    def get_environment(self):
        pass

    @property
    def ports(self):
        ports = []
        for ep in self.endpoints.values():
            for address in ep.addresses:
                if "ports" in address:
                    for port in address["ports"]:
                        ports.append(dict(name=ep.name, port=str(port)))
        ports.sort(key=lambda x: x["name"])
        return ports

    def serialized(self):
        return dict(
            name=self.name,
            kind=self.kind,
            relations=self.full_relations,
            # endpoints=[e.serialized() for e in self.endpoints],
            config=self.full_config,
        )

    @property
    def full_relations(self):
        context = dict(service=self, this=self, graph=self.graph)
        return utils.interpolate(self.relations, context)

    @property
    def full_config(self):
        # There might be config for the service in either/both the graph and the environment.
        # The env will take priority as the graph object can be reusable but the env contains
        # specific overrides.

        env_config = self.graph.environment.get("config", {})
        service_config = env_config.get("services", {}).get(self.name, {})
        composed = jsonmerge.merge(self.config, service_config)
        context = dict(service=self, this=self)
        context.update(composed)
        return utils.interpolate(composed, context)


@dataclass
class Interface(GraphObj):
    name: str
    kind: str = field(init=False, default="Interface")
    version: str

    # XXX: this needs a merged composed form
    # each service/endpoint in teh relations should
    # have a change to update the composed values in rounds
    # till nothing changes and the interpolation is complete
    def __hash__(self):
        return hash((self.name, self.kind, self.version))

    def serialized(self):
        return dict(
            name=self.name,
            kind=self.kind,
            version=self.version,
            defaults=dict(self.entity.get("defaults")),
            spec=self.entity.get("interface"),
        )


@dataclass
class Endpoint:
    name: str
    kind: str = field(init=False, default="Endpoint")
    service: Service
    interface: Interface
    addresses: List[Dict[str, str]]

    def __hash__(self):
        return hash((self.name, self.kind))

    @property
    def qual_name(self):
        return f"{self.service.name}:{self.name}"

    @property
    def ports(self):
        ports = set()
        for a in self.addresses:
            if "ports" in a:
                for p in a["ports"]:
                    ports.add(str(p))
        ports = list(ports)
        ports.sort()
        return ports

    def serialized(self):
        return dict(
            name=self.name,
            kind=self.kind,
            service=self.service.name,
            interface=self.interface.serialized(),
            addresses=self.addresses,
        )


@dataclass
class Relation:
    kind: str = field(init=False, default="Relation")
    endpoints: List[Endpoint] = field(default_factory=list)

    def __hash__(self):
        return hash((self.name, self.kind))

    @property
    def name(self):
        # join the endpoint names
        return "=".join([ep.qual_name for ep in self.endpoints])

    qual_name = name

    def serialized(self):
        return dict(
            kind=self.kind,
            name=self.name,
            endpoints=[e.serialized() for e in self.endpoints],
        )


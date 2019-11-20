from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import entity
from . import utils


@dataclass
class GraphObj:
    kind: str
    name: str
    graph: "GraphObj" = field(init=False, default=None)

    @property
    def qual_name(self):
        return f"{self.kind}:{self.name}"


@dataclass
class Runtime(GraphObj):
    kind: str = field(init=False, default="Runtime")

    def serialized(self):
        return dict(name=self.name, kind=self.kind)


@dataclass
class Service(GraphObj):
    kind: str = field(init=False, default="Service")
    component: entity.Entity
    endpoints: List["Endpoint"] = field(init=False, default_factory=list)
    relations: List = field(init=False, default_factory=list)
    runtime: Runtime
    # TODO: this can be in init and draw config from the graph
    config: Dict[str, Any] = field(default_factory=dict)

    def add_endpoint(self, name, interface):
        addresses = []
        eps = self.component.get("endpoints", [])
        ep_spec = utils.pick(eps, name=name, interface=interface)
        addresses = ep_spec.get("addresses", [])
        ep = Endpoint(name=name, interface=interface, service=self, addresses=addresses)
        self.endpoints.append(ep)
        return ep

    def get_endpoint(self, **kwargs):
        return utils.pick(self.endpoints, **kwargs)

    @property
    def ports(self):
        ports = set()
        for ep in self.endpoints:
            for address in ep.addresses:
                if "ports" in address:
                    for port in address["ports"]:
                        ports.add(str(port))
        ports = list(ports)
        ports.sort()
        return ports

    def serialized(self):
        return dict(
            name=self.name, kind=self.kind, endpoints=self.endpoints, config=self.config
        )


@dataclass
class Endpoint:
    name: str
    kind: str = field(init=False, default="Endpoint")
    service: Service
    interface: str
    addresses: List[Dict[str, str]]

    @property
    def qual_name(self):
        return f"{self.service.name}:{self.interface}"

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
            interface=self.interface,
            addresses=self.addresses,
        )


@dataclass
class Relation:
    kind: str = field(init=False, default="Relation")
    endpoints: List[Endpoint] = field(default_factory=list)

    @property
    def name(self):
        # join the endpoint names
        return "=".join([ep.qual_name for ep in self.endpoints])

    qual_name = name

    def serialized(self):
        return dict(kind=self.kind, name=self.name, endpoints=self.endpoints)

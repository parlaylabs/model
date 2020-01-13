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

    def fini(self):
        self._populate_endpoint_config()

    def _populate_endpoint_config(self):
        env_config = self.graph.environment.get("config", {})
        service_config = env_config.get("services", {}).get(self.name, {})
        composed = jsonmerge.merge(self.config, service_config)
        # XXX: resolve references to overlay vars from service_config.config
        # into the endpoint data as a means of setting runtime values
        # TODO: we should be able to reference vault and/or other secret mgmt tools
        # here do reference actual credentials
        config_data = service_config.get("config", [])
        for cd in config_data:
            epname = cd.get("endpoint")
            endpoint = self.endpoints[epname]
            data = cd.get("data", {})
            for k, v in data.items():
                item = utils.pick(endpoint.provides, name=k)
                # This works because normalize_values in ep
                # prefers 'value' to 'default'
                # XXX: really we'd want this to delegate to the underlying facets
                # but that will take some reactoring
                item["value"] = v

    def validate(self):
        for rel in self.relations:
            rel.validate()

        comp = self.entity
        # this is the environment validation relative to the component
        envspec = comp.get("environment", {})
        requires = envspec.get("requires", [])

        config = self.full_config()
        env = config.get("environment", [])
        for key in requires:
            v = utils.pick(env, name=key)
            if not v:
                # XXX: these should become validationerrors
                raise ValueError(
                    f"Missing required ENV variable {key} in endpoint {self.name} for service {self.service.name}."
                )
            val = v.get("value")
            if not val:
                raise ValueError(
                    f"Empty required environment variable {key} for endpoint {self.name} in service {self.service.name}"
                )

    def add_endpoint(self, name, interface, role):
        ep = Endpoint(name=name, interface=interface, service=self, role=role)
        self.endpoints[name] = ep
        return ep

    @property
    def service_addr(self):
        return self.runtime.service_addr(self, self.graph)

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
    def ports(self):
        ports = []
        for ep in self.endpoints.values():
            if ep.provides:
                for p in ep.ports:
                    ports.append(dict(name=ep.name, port=str(p)))
        ports.sort(key=lambda x: x["name"])
        return ports

    def serialized(self):
        return dict(
            name=self.name,
            kind=self.kind,
            relations=self.full_relations(),
            config=self.full_config(),
        )

    def full_relation(self, relation, secrets=False):
        remote = relation.get_remote(self)
        local = relation.get_local(self)
        context = dict(
            service=remote,
            local=local,
            this=self,
            relation=relation,
            remote=remote,
            graph=self.graph,
            runtime=self.runtime,
        )
        if secrets:
            base = remote.provided_secrets
        else:
            base = remote.provided
        base["interface"] = local.interface.qual_name
        base["service"] = remote.service.name
        data = {local.name: base}

        return utils.interpolate(data, context)

    def full_relations(self, secrets=False):
        rels = {}
        for rel in self.relations:
            rels.update(self.full_relation(rel, secrets=secrets))
        return rels

    def get_relation_by_endpoint(self, ep):
        if isinstance(ep, str):
            ep = self.endpoints[ep]
        for rel in self.relations:
            if ep in rel.endpoints:
                return rel
        return None

    def build_context_from_endpoints(self):
        """Build context to populate an interpolation context by adding name_relation, 
        name_local and name_remote with the relations, and the endpoints.
        """
        ctx = {}
        for name, ep in self.endpoints.items():
            local = ep
            rel = self.get_relation_by_endpoint(ep)
            if not rel:
                # not all endpoints will be in a relation with this usage
                continue
            remote = rel.get_remote(self)
            # TODO: verify the service is in the relation
            ctx[f"{name}_relation"] = rel
            ctx[f"{name}_local"] = local
            ctx[f"{name}_remote"] = remote
        return ctx

    def full_config(self):
        # There might be config for the service in either/both the graph and the environment.
        # The env will take priority as the graph object can be reusable but the env contains
        # specific overrides.
        env_config = self.graph.environment.get("config", {})
        service_config = env_config.get("services", {}).get(self.name, {})
        composed = jsonmerge.merge(self.config, service_config)
        # XXX: resolve references to overlay vars from service_config.config
        # into the endpoint data as a means of setting runtime values
        # TODO: we should be able to reference vault and/or other secret mgmt tools
        # here do reference actual credentials
        context = dict(service=self, this=self, **env_config)
        context.update(composed)

        # XXX: This could filter down to only the connected relation but
        # for now we do all
        ctx = self.build_context_from_endpoints()
        context.update(ctx)
        return utils.interpolate(composed, context)


@dataclass
class Interface(GraphObj):
    name: str
    kind: str = field(init=False, default="Interface")
    version: str
    roles: Dict[str, List[Dict[str, Any]]]

    @property
    def qual_name(self):
        return f"{self.name}:{self.version}"

    def __hash__(self):
        return hash((self.name, self.kind, self.version))

    def serialized(self):
        return dict(
            name=self.name,
            kind=self.kind,
            version=self.version,
            # roles=list(self.roles.keys()),
        )

    def validate(self, service, endpoint):
        # ensure that the endpoint 'provides' have been provided with values
        rel = service.get_relation_by_endpoint(endpoint)
        ep = rel.get_remote(service)
        rservice = ep.service
        vals = rservice.full_relation(rel)
        priv = rservice.full_relation(rel, secrets=True)
        vals = jsonmerge.merge(vals, priv)
        specs = self.roles[endpoint.role].get("provides", [])
        type_map = {"str": str, "string": str, "int": int, "number": (int, float)}
        for spec in specs:
            name = spec["name"]
            v = vals[endpoint.name].get(name)
            if v is None or not isinstance(v, type_map[spec.get("type", "str")]):
                raise ValueError(
                    f"Missing expected value '{name}' for interface {self.name} from endpoint {ep.name}:{ep.role} of service {rservice.name}\n{vals}"
                )


@dataclass
class Endpoint:
    name: str
    kind: str = field(init=False, default="Endpoint")
    service: Service
    interface: Interface
    role: str

    def __hash__(self):
        return hash((self.name, self.kind))

    @property
    def runtime(self):
        return self.service.runtime

    @property
    def qual_name(self):
        return f"{self.service.name}:{self.name}({self.interface.name}:{self.role})"

    @property
    def config(self):
        c = self.interface.roles.get(self.role)
        if not c:
            c = {}
        return c

    def normalize_values(self, data, secrets=False):
        # take the schema styled config data and map it to kvpairs
        result = utils.AttrAccess()
        for spec in data:
            name = spec["name"]
            is_secret = spec.get("secret", False)
            if (is_secret and secrets is False) or (not is_secret and secrets is True):
                # XXX: we could put "<redacted>"
                # but for now we omit those  fields
                continue
            result[name] = spec.get("value", spec.get("default"))
        return result

    @property
    def provides(self):
        return self.config.get("provides", [])

    @property
    def provided(self):
        return self.normalize_values(self.provides)

    @property
    def provided_secrets(self):
        return self.normalize_values(self.provides, secrets=True)

    @property
    def requires(self):
        return self.config.get("requires", [])

    @property
    def required(self):
        return self.normalize_values(self.requires)

    @property
    def required_secrets(self):
        return self.normalize_values(self.requires, secrets=True)

    @property
    def service_addr(self):
        return self.service.service_addr

    @property
    def ports(self):
        ports = set()
        for c in utils.filter_iter(self.provides, name="port"):
            p = c.get("default")
            if not p:
                continue
            # There may be interpolation needed here
            p = utils.interpolate(
                str(p),
                dict(
                    this=self,
                    endpoint=self,
                    service=self.service,
                    interface=self.interface,
                    runtime=self.service.runtime,
                    config=self.config,
                ),
            )
            ports.add(p)

        ports = list(ports)
        ports.sort()
        return ports

    def serialized(self):
        return dict(
            name=self.name,
            kind=self.kind,
            service=self.service.name,
            interface=self.interface.serialized(),
        )

    def validate(self):
        # The component may have defined things like ENV vars that are needed for the component to run
        # ensure that in their final config all such values are present for the connected relations
        # Note that this should be called from the relation object to validate each endpoint as there
        # are no requirements to validate unrelated endpoints
        comp = self.service.entity
        # this is the environment validation relative to the component
        epspec = utils.pick(comp.endpoints, name=self.name)
        envspec = epspec.get("environment", {})
        requires = envspec.get("requires", [])

        config = self.service.full_config()
        env = config.get("environment", [])
        for key in requires:
            v = utils.pick(env, name=key)
            if not v:
                # XXX: these should become validationerrors
                raise ValueError(
                    f"Missing required ENV variable {key} in endpoint {self.name} for service {self.service.name}."
                )
            val = v.get("value")
            if not val:
                raise ValueError(
                    f"Empty required environment variable {key} for endpoint {self.name} in service {self.service.name}"
                )
        self.interface.validate(self.service, self)


@dataclass
class Relation:
    kind: str = field(init=False, default="Relation")
    endpoints: List[Endpoint] = field(default_factory=list)

    def __hash__(self):
        return hash((self.name, self.kind))

    @property
    def name(self):
        # join the endpoint names
        return "<>".join([ep.qual_name for ep in self.endpoints])

    qual_name = name

    def get_remote(self, service):
        # return the remote endpoint for a relation given the 'local' service
        found = False
        remote = None
        for ep in self.endpoints:
            if ep.service == service:
                found = True
            else:
                remote = ep
            if found and remote:
                break
        if not found:
            raise ValueError(f"Service not in relation {self.seralized}")
        return remote

    def get_local(self, service):
        # return the remote endpoint for a relation given the 'local' service
        for ep in self.endpoints:
            if ep.service == service:
                return ep
        raise ValueError(f"Service not in relation {self.seralized}")

    def serialized(self):
        return dict(
            kind=self.kind,
            name=self.name,
            endpoints=[e.serialized() for e in self.endpoints],
        )

    def validate(self):
        for ep in self.endpoints:
            ep.validate()

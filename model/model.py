import logging
import os

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

import jsonmerge

from . import config
from . import entity
from . import exceptions
from . import schema
from . import utils

_marker = object()
log = logging.getLogger(__name__)


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

    @property
    def src_ref(self):
        return self.entity.src_ref

    def validate(self):
        return self.entity.validate()

    def __getattr__(self, key):
        val = self.entity[key]
        if isinstance(val, dict):
            val = utils.AttrAccess(val)
        return val

    def get(self, key, default=None):
        return self.entity.get(key, default)

    def serialized(self):
        return asdict(self)

    def __hash__(self):
        return hash((self.name, self.kind))

    def get_template(self, key, paths=None):
        # Support getting a jinja2 template relative to defintion
        # of any graph obj.
        # just invoke render(ctx) as needed
        # Env is prepended to the search path here because its location represents a
        # logical play to look for overridden templates
        extra = None
        if self.graph:
            extra = self.graph.environment.entity
        template = self.entity.get_template(key, extra=extra, paths=paths)
        return template

    def fini(self):
        self._interpolate_entity()

    def _interpolate_entity(self, context=None):
        if not context:
            context = self.context
        data = self.entity.serialized()
        data = utils.interpolate(data, context)
        self.entity.add_facet(data, "<interpolated>")


@dataclass
class Runtime(GraphObj):
    kind: str = field(init=False, default="Runtime")

    def serialized(self):
        return dict(name=self.name, kind=self.kind)

    @property
    def impl(self):
        return self.graph.runtime[self.name]

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

    @property
    def env(self):
        return os.environ


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
        super().fini()
        self._populate_endpoint_config()

    def _populate_endpoint_config(self):
        env_config = self.graph.environment.get("config", {})
        service_config = env_config.get("services", {}).get(self.name, {})
        env_config_data = service_config.get("config", [])

        # XXX: resolve references to overlay vars from service_config.config
        # into the endpoint data as a means of setting runtime values
        # TODO: we should be able to reference vault and/or other secret mgmt tools
        # here do reference actual credentials
        # lookup order in is [interface, endpoint, component, service via graph [TBD], environment]
        # last write wins and is recorded in ep data
        for ep in self.endpoints.values():
            env = utils.pick(env_config_data, endpoint=ep.name, default={}).get(
                "data", {}
            )
            component_data = utils.pick(
                self.entity.endpoints, name=ep.name, default={}
            ).get("data", {})
            data = jsonmerge.merge(component_data, env)
            ep.data.update(data)

        env = None
        for cd in env_config_data:
            epname = cd.get("endpoint")
            if epname:
                continue
            data = cd.get("data", {})
            self.add_facet(data, self.graph.environment.src_ref[0])
        self._interpolate_entity()

    def validate(self):
        for rel in self.relations:
            rel.validate()

        for epname in self.exposed:
            if epname not in self.endpoints:
                raise exceptions.ConfigurationError(
                    f"Unable to expose unknown endpoint {epname} in service {self.name}"
                )

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
    def exposed(self):
        return self.entity.get("expose", [])

    @property
    def exposed_endpoints(self):
        for ex in self.exposed:
            yield self.endpoints[ex]

    @property
    def service_addr(self):
        rt = self.runtime
        if not rt:
            # When there is no runtime we depend on the address being encoded
            # at the environment level. This contract will have to be validated
            # in the future, but for now
            # FIXME: the new address scheme places the address under a named endpoint
            # we don't currently know which address this is. The common usecase is there
            # might only be one static address for an unmanaged endpoint like this
            # however this guesswork isn't quite right
            # XXX: for now we scan
            for endpoint in self.endpoints.values():
                address = endpoint.data.get("address")
                if address:
                    return address
            log.warning(
                f"Endpoints for service {self.name} have no bound address. This is usually a configuration error."
            )
            return None
        return self.runtime.service_addr(self, self.graph)

    @property
    def ports(self):
        ports = []
        for ep in self.endpoints.values():
            if ep.provides:
                for p in ep.ports:
                    ports.append(p)
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
            service=remote.service,
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
        base["service_name"] = remote.service.name
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

    def _build_context_from_endpoints(self):
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

    def _context(self):
        env_config = self.graph.environment.get("config", {})
        service_config = env_config.get("services", {}).get(self.name, {})
        composed = jsonmerge.merge(self.config, service_config)
        # XXX: resolve references to overlay vars from service_config.config
        # into the endpoint data as a means of setting runtime values
        # TODO: we should be able to reference vault and/or other secret mgmt tools
        # here do reference actual credentials
        context = config.get_context(service=self, this=self, **env_config)
        context.update(composed)

        # XXX: This could filter down to only the connected relation but
        # for now we do all
        ctx = self._build_context_from_endpoints()
        context.update(ctx)

        if "environment" in context:
            # This would represent ENV defaults provided at some layer
            # rename to env
            context["env"] = context["environment"]
        context["environment"] = self.graph.environment
        config.set_context(context)
        return context, composed

    @property
    def context(self):
        return self._context()[0]

    def full_config(self, allow_missing=False):
        # There might be config for the service in either/both the graph and the environment.
        # The env will take priority as the graph object can be reusable but the env contains
        # specific overrides.
        context, composed = self._context()
        return utils.interpolate(composed, context, allow_missing=allow_missing)

    @property
    def annotations(self):
        return utils.AttrAccess(self.entity.get("annotations", {}))

    @property
    def files(self):
        return self.entity.get("files", [])

    def render_template(self, name):
        template = self.get_template(name)
        return template.render(self.context)


@dataclass
class Interface(GraphObj):
    name: str
    kind: str = field(init=False, default="Interface")
    version: str
    roles: List[List[Dict[str, Any]]]
    data: Dict[str, Any] = field(init=False, repr=False)

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
        specs = utils.pick(self.roles, name=endpoint.role).get("provides", [])
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
    data: Dict[str, Any] = field(init=False, default_factory=utils.AttrAccess)

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
        c = utils.pick(self.interface.roles, name=self.role)
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
            result[name] = self.data.get(name, spec.get("default"))
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
        ports = []
        # XXX: this doesn't support per-port overrides, its very generic now
        # see the flaws document about named portsets
        for c in utils.filter_iter(self.provides, name="port"):
            p = self.data.get("port", c.get("default"))
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
            p = utils.AttrAccess(
                port=p, name=self.name, protocol=c.get("protocol", "TCP")
            )
            ports.append(p)

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

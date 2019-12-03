import itertools
from dataclasses import dataclass, field
from typing import List

import yaml

from . import render

_plugins = {}
_runtimes = {}


def register(cls):
    _plugins[cls.__name__.lower()] = cls
    return cls


@dataclass
class RuntimePlugin:
    name: str


@register
@dataclass
class Kubernetes:
    name: str = field(init=False, default="Kubernetes")

    def render_service(self, service, graph, output):
        # push out a deployment
        ports = service.ports
        # XXX: ServiceAccountName
        dports = []
        for p in ports:
            dports.append(dict(containerPort=int(p), protocol="TCP"))

        sconfig = graph.environment.config.get("services", {}).get(service.name, {})
        senv = sconfig.get("environment", [])
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"labels": {"app": service.name}, "name": service.name,},
            "spec": {
                "replicas": service.entity.get("replicas"),
                "selector": {"matchLabels": {"app": service.name}},
                "template": {
                    "metadata": {
                        "labels": {
                            "app.kubernetes.io/name": service.name,
                            "app.kubernetes.io/version": service.entity.version,
                            "app.kubernetes.io/component": service.entity.name,
                            # XXX: become a graph ref
                            "app.kubernetes.io/part-of": graph.model.name,
                            "app.kubernetes.io/managed-by": __package__,
                        },
                    },
                    "spec": {
                        "containers": [
                            # XXX: join with context/runtime container registry
                            # XXX: model and support cross cutting concerns here
                            {
                                "name": service.name,
                                "image": service.entity.image,
                                "imagePullPolicy": "IfNotPresent",
                                "ports": dports,
                                "env": senv,
                            },
                        ],
                        # see https://kubernetes.io/docs/concepts/workloads/pods/pod-topology-spread-constraints/#spread-constraints-for-pods
                        # "topologySpreadConstraints": [
                        #    {
                        ##        "topologyKey": service.name,
                        #       "whenUnsatisfiable": "ScheduleAnyway",
                        #       "labelSelector": {"name": service.name},
                        #   }
                        # ],
                    },
                },
            },
        }

        output.add(f"{service.name}-deployment.yaml", deployment, self, service=service)

        # next push out a service object
        ports = []
        for p in service.ports:
            # XXX: static protocol, pull from endpoint
            # XXX: use named targetPort from pod definition
            ports.append({"protocol": "TCP", "port": int(p)})
        serviceSpec = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": service.name},
            "spec": {
                "selector": {"app": service.name},
                "ports": ports,
                # XXX: specify elb/nlb annotations when url is AWS
                # see https://kubernetes.io/docs/concepts/services-networking/service/#connection-draining-on-aws
                # https://kubernetes.io/docs/concepts/services-networking/service/#aws-nlb-support
            },
        }
        output.add(f"{service.name}-service.yaml", serviceSpec, self, service=service)


@register
@dataclass
class Istio:
    name: str = field(init=False, default="Istio")

    def init(self, graph, output):
        gateway = {
            "kind": "Gateway",
            "spec": {
                "selector": {"istio": "ingressgateway"},
                # XXX: add HTTPS (and termination model if needed or a way to get CERTS)
                "servers": [
                    {
                        "hosts": ["*"],
                        "port": {"protocol": "HTTP", "name": "http", "number": 80},
                    }
                ],
            },
            "apiVersion": "networking.istio.io/v1alpha3",
            "metadata": {"name": "ingressgateway", "namespace": "istio-system",},
        }
        output.add(f"ingressgateway.yaml", gateway, self)

    def render_service(self, service, graph, output):
        # FIXME: we will need the ability to handle any type of endpoint the sevice exposes
        exposed = service.exposed
        if not exposed:
            return

        public_dns = graph.environment.config["public_dns"]
        for ex in exposed:
            ep = service.get_endpoint(name=ex)
            vs = {
                "apiVersion": "networking.istio.io/v1alpha3",
                "kind": "VirtualService",
                "spec": {
                    "hosts": [f"{service.name}.{public_dns}"],
                    "http": [
                        {
                            "route": [
                                {
                                    "destination": {
                                        "host": service.name,
                                        # XXX: single port at random from set, come on...
                                        "port": {"number": int(ep.ports[0])},
                                    }
                                }
                            ],
                            # XXX: control this or ignore? feels like app detail
                            "match": [{"uri": {"prefix": "/"}}],
                        }
                    ],
                    # global gateway or 1 per?
                    "gateways": ["ingressgateway.istio-system.svc.cluster.local"],
                },
                "metadata": {
                    "name": service.name,
                    # XXX: ns control
                    "namespace": "default",
                },
            }
            output.add(
                f"{service.name}-{ep.name}-virtualservice.yaml",
                vs,
                self,
                service=service,
            )


@register
@dataclass
class Kustomize:
    name: str = field(init=False, default="Kustomize")

    def fini(self, graph, output):
        # XXX: temp workaround till we assign outputs to layers
        if isinstance(output, render.FileRenderer):
            return
        # Render a kustomize resource file into what we presume to be a base dir
        output.add("kustomization.yaml", {"resources": list(output.index.keys())}, self)


@dataclass
class RuntimeImpl:
    name: str
    kind: str = field(init=False, default="RuntimeImpl")
    plugins: List[RuntimePlugin]

    @property
    def qual_name(self):
        return self.name

    def render(self, graph, outputs):
        # Run three phases populating data dicts into outputs
        # then run the renderer to create output data dicts
        # NOTE: we use data dicts here rather than the python-kube API
        # because we want to allow various plugins to operate on the data
        # rather than produce a single API call. Using the API would have the
        # advantage that we'd expect some validate, however the ability to break
        # this into layers here wins, actually applying this to the runtime (k8s)
        # will validate the results.

        for plugin in self.plugins:
            m = getattr(plugin, "init", None)
            if m:
                m(graph, outputs)

        for phase in ["pre_", "", "post_"]:
            # dynamic method resolution in the form of
            # <phase>_render_<kind.lower>
            for obj in itertools.chain(graph.services, graph.relations):
                for plugin in self.plugins:
                    kind = obj.kind.lower()
                    mn = f"{phase}render_{kind}"
                    m = getattr(plugin, mn, None)
                    if m:
                        m(obj, graph, outputs)

        for plugin in self.plugins:
            m = getattr(plugin, "fini", None)
            if m:
                m(graph, outputs)

        return outputs


def resolve(runtime_name, store):
    global _runtimes
    # Look for a runtime entry in the store
    if runtime_name in _runtimes:
        return _runtimes[runtime_name]
    rspec = store.qual_name[f"Runtime:{runtime_name}"]
    plugins = resolve_each(rspec.plugins)
    runtime = RuntimeImpl(runtime_name, plugins=plugins)
    _runtimes[runtime_name] = runtime
    store.add(runtime)
    return runtime


def resolve_each(plugins):
    impls = []

    for p in plugins:
        impls.append(_plugins[p["name"].lower()]())
    return impls

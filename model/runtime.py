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
            dports.append(dict(containerPort=int(p["port"]), protocol="TCP"))

        sconfig = graph.environment.config.get("services", {}).get(service.name, {})
        senv = sconfig.get("environment", [])

        labels = {
            "app.kubernetes.io/name": service.name,
            "app.kubernetes.io/version": str(service.entity.version),
            "app.kubernetes.io/component": service.entity.name,
            # XXX: become a graph ref
            "app.kubernetes.io/part-of": graph.name,
            "app.kubernetes.io/managed-by": __package__,
        }

        ns = dict(
            apiVersion="v1",
            kind="Namespace",
            metadata=dict(name=graph.name, labels={}),
        )
        nsfn = f"00-{graph.name}-namespace.yaml"
        if nsfn not in output:
            output.add(nsfn, ns, self, graph=graph)

        # XXX: This must become the full context, see Kustomize.render_service notes for the change
        config_map = service.serialized()
        output.add(
            f"configs/{graph.name}-{service.name}-config.yaml",
            config_map,
            self,
            service=service,
            graph=graph,
        )

        pod_labels = {
            "app": service.name,
            "version": str(service.entity.version),
        }
        pod_labels.update(labels)

        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "labels": labels,
                "namespace": graph.name,
                "name": service.name,
            },
            "spec": {
                "replicas": service.entity.get("replicas"),
                "selector": {"matchLabels": {"app.kubernetes.io/name": service.name}},
                "template": {
                    "metadata": {"labels": pod_labels},
                    "spec": {
                        "restartPolicy": "Always",
                        "containers": [
                            # XXX: join with context/runtime container registry
                            # XXX: model and support cross cutting concerns here
                            {
                                "name": service.name,
                                "image": service.entity.image,
                                "imagePullPolicy": "IfNotPresent",
                                "ports": dports,
                                "env": senv,
                                "volumeMounts": [
                                    {"name": "model-config", "mountPath": "/etc/model"},
                                    {"name": "podinfo", "mountPath": "/etc/podinfo"},
                                ],
                            },
                        ],
                        "volumes": [
                            {
                                "name": "model-config",
                                # XXX: not using kustomize generator name
                                "configMap": {"name": f"{service.name}-config",},
                            },
                            {
                                "name": "podinfo",
                                "downwardAPI": {
                                    "items": [
                                        {
                                            "path": "labels",
                                            "fieldRef": {
                                                "fieldPath": "metadata.labels",
                                            },
                                        },
                                        {
                                            "path": "annotations",
                                            "fieldRef": {
                                                "fieldPath": "metadata.annotations",
                                            },
                                        },
                                    ],
                                },
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

        output.add(
            f"40-{service.name}-deployment.yaml",
            deployment,
            self,
            service=service,
            graph=graph,
        )

        # next push out a service object
        ports = []
        for p in service.ports:
            # XXX: static protocol, pull from endpoint
            # XXX: use named targetPort from pod definition
            # ports need a unique name when there is more than one
            # for this reason we must include the endpoint name
            # TODO: support UDP and others as needed
            ports.append({"protocol": "TCP", "name": p["name"], "port": int(p["port"])})
        serviceSpec = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"namespace": graph.name, "name": service.name},
            "spec": {
                "selector": {"app": service.name},
                "ports": ports,
                # XXX: specify elb/nlb annotations when url is AWS
                # see https://kubernetes.io/docs/concepts/services-networking/service/#connection-draining-on-aws
                # https://kubernetes.io/docs/concepts/services-networking/service/#aws-nlb-support
            },
        }
        output.add(
            f"50-{service.name}-service.yaml", serviceSpec, self, service=service
        )


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
        output.add(f"02-ingressgateway.yaml", gateway, self)

    def fini(self, graph, output):
        ns_out = f"00-{graph.name}-namespace.yaml"
        if ns_out in output:
            ent = output.index[ns_out]
            ent.data["metadata"]["labels"]["istio-injection"] = True

    def render_service(self, service, graph, output):
        # FIXME: we will need the ability to handle any type of endpoint the sevice exposes
        exposed = service.exposed
        if not exposed:
            return

        public_dns = graph.environment.config["public_dns"]
        for ep in service.exposed_endpoints:
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
                                        "host": f"{service.name}.{graph.name}.svc.cluster.local",
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
                "metadata": {"namespace": graph.name, "name": service.name,},
            }
            output.add(
                f"60-{service.name}-{ep.name}-virtualservice.yaml",
                vs,
                self,
                endpoint=ep,
                service=service,
            )


@register
@dataclass
class Kustomize:
    name: str = field(init=False, default="Kustomize")
    fn = "kustomization.yaml"

    def init(self, graph, output):
        output.add(self.fn, {"resources": [], "configMapGenerator": []}, self)

    def render_service(self, service, graph, output):
        # For each service we inject a config-map for use in configuring
        # the k8s deployment pods as a volume. To support this we must create
        # a configmapgenerator in the kustomize file. This is because the
        # accepted pattern for updating a configmap is to render a new
        # one and update the deployments reference to it.
        # The Kubernetes plugin should have registered a config map to
        # the output.
        context = dict(
            name=f"{service.name}-config",
            namespace=graph.name,
            files=[f"configs/{graph.name}-{service.name}-config.yaml"],
        )

        output.update(
            self.fn,
            data={"data.configMapGenerator": [context]},
            plugin=self,
            schema={"mergeStrategy": "append"},
        )

    def fini(self, graph, output):
        # XXX: temp workaround till we assign outputs to layers
        if isinstance(output, render.FileRenderer):
            return
        # Render a kustomize resource file into what we presume to be a base dir
        files = [
            e.name for e in sorted(output.filter(plugin=self), key=lambda x: x.name)
        ]
        files = list(filter(lambda x: not x.startswith("configs/"), files))
        output.update(self.fn, {"data.resources": files}, self)


@dataclass(unsafe_hash=True)
class RuntimeImpl:
    name: str = field(hash=True)
    kind: str = field(init=False, hash=True, default="RuntimeImpl")
    plugins: List[RuntimePlugin] = field(hash=False)

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
    rspec = store.runtime[runtime_name]
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

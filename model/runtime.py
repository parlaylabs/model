import base64
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

from . import docker
from . import exceptions
from . import render
from . import utils

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
class Docker:
    name: str = field(init=False, default="Docker")

    def init(self, graph, outputs):
        # Parse the users docker conf file
        # and record a list of logins we know about
        self.auths = set()
        self.cfg = None
        self.graph = graph
        self.image_pull_secrets = {}
        cfg = docker.parse_config()
        if cfg:
            self.auths |= set(cfg.get("auths").keys())
            self.cfg = cfg

    def image_secrets_for(self, image):
        m = docker.parse_docker_tag(image)
        if not m or m["domain"] not in self.auths:
            return None
        r = utils.AttrAccess(
            auth=docker.auth_for(self.cfg, m["domain"]),
            key=f"{self.graph.name}-{m['domain']}",
        )
        self.image_pull_secrets[r.key] = r
        return r


@register
@dataclass
class Kubernetes:
    name: str = field(init=False, default="Kubernetes")

    def service_addr(self, service, graph):
        return f"{service.name}.{graph.name}.svc.cluster.local"

    def config_map_for(self, service):
        cm = dict(config=service.full_config(), relations=service.full_relations())
        return cm

    def secrets_for(self, service):
        secrets = dict(relations=service.full_relations(secrets=True))
        return secrets

    def add_namespace(self, graph, name, output):
        ns = dict(
            apiVersion="v1",
            kind="Namespace",
            metadata=dict(name=graph.name, labels={}),
        )
        nsfn = f"00-{graph.name}-namespace.yaml"
        if nsfn not in output:
            output.add(nsfn, ns, self, graph=graph)

    def add_configmap(self, graph, service, output, data=None, name=None):
        # creating the context for the config map involves all the service config and any information
        # provided by connected endpoints
        if data is None:
            data = self.config_map_for(service)
        if name is None:
            name = f"configs/{graph.name}-{service.name}-config.json"
        output.add(
            name, data, self, format="json", service=service, graph=graph,
        )
        return name

    def add_secrets(self, graph, service, output, data=None, name=None):
        if data is None:
            data = self.secrets_for(service)
        if name is None:
            name = f"configs/{graph.name}-{service.name}-secrets.json"

        output.add(
            name, data, self, format="json", service=service, graph=graph,
        )
        return name, bool(data)

    def add_volumes(self, graph, service, output, configmap_name, secrets_name=None):
        volumeMounts = [
            {
                "name": "model-config",
                "mountPath": "/etc/model/config",
                "readOnly": True,
            },
            {"name": "podinfo", "mountPath": "/etc/podinfo", "readOnly": True,},
        ]

        if secrets_name:
            volumeMounts.append(
                {
                    "name": "model-secrets",
                    "mountPath": "/etc/model/secrets",
                    "readOnly": True,
                }
            )

        volumes = [
            {
                "name": "model-config",
                "configMap": {
                    "name": configmap_name,
                    "items": [
                        {
                            "key": f"{graph.name}-{service.name}-config.json",
                            "path": f"{service.name}-config.json",
                        }
                    ],
                },
            },
            {
                "name": "podinfo",
                "downwardAPI": {
                    "items": [
                        {
                            "path": "labels",
                            "fieldRef": {"fieldPath": "metadata.labels",},
                        },
                        {
                            "path": "annotations",
                            "fieldRef": {"fieldPath": "metadata.annotations",},
                        },
                    ],
                },
            },
        ]

        if secrets_name:
            volumes.append(
                {
                    "name": "model-secrets",
                    "secret": {
                        "secretName": secrets_name,
                        "defaultMode": 0o511,
                        "items": [
                            {
                                "key": f"{graph.name}-{service.name}-secrets.json",
                                "path": f"{service.name}-secrets.json",
                            }
                        ],
                    },
                }
            )

        # Handle any files which should be templatized and mapped into the container
        for filespec in service.files:
            template = filespec.get("template")
            container_path = filespec.get("container_path")
            if not template:
                raise exceptions.ConfigurationError(
                    f"file directive for service {service.name} doesn't define template"
                )
            if not container_path:
                raise exceptions.ConfigurationError(
                    f"file directive for service {service.name} is missing container_path directive"
                )

            name = str(Path(container_path).name)
            name = utils.filename_to_label(name)
            fn = utils.filename_to_label(template)
            output.add(
                f"resources/{graph.name}-{service.name}-{fn}",
                service.render_template(template),
                self,
                format="raw",
                service=service,
                graph=graph,
            )
            cp = str(Path(container_path).parent / Path(container_path).stem)
            volumeMounts.append(dict(name=name, mountPath=cp, readOnly=True))
            key = f"{graph.name}-{service.name}-{fn}"
            volumes.append(
                dict(
                    name=name,
                    configMap=dict(
                        name=f"{service.name}-{fn}",
                        items=[dict(key=key, path=template)],
                    ),
                )
            )
        return volumeMounts, volumes

    def add_image_pull_secret(self, graph, service, output, container_spec):
        # see if we need ImagePullSecrets based on defaultContainer
        default_container = container_spec["template"]["spec"]["containers"][0]
        docker = self.runtime_impl.plugin("Docker")
        if docker:
            pull_secret = docker.image_secrets_for(default_container["image"])
            if pull_secret:
                container_spec["template"]["spec"]["imagePullSecrets"] = [
                    {"name": utils.filename_to_label(pull_secret.key)}
                ]
                # Manually Generate the pull secret as its special formatting needs
                # not supported by the kustomize secrets generator
                sec = {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": utils.filename_to_label(pull_secret.key),
                        "namespace": graph.name,
                    },
                    "data": {
                        ".dockerconfigjson": base64.b64encode(
                            json.dumps(pull_secret.auth).encode("utf-8")
                        )
                    },
                    "type": "kubernetes.io/dockerconfigjson",
                }
                output.add(
                    pull_secret.key, sec, self, service=service, graph=graph,
                )

    def render_service(self, service, graph, output):
        labels = {
            "app.kubernetes.io/name": service.name,
            "app.kubernetes.io/version": str(service.entity.version),
            "app.kubernetes.io/component": service.entity.name,
            # XXX: become a graph ref
            "app.kubernetes.io/part-of": graph.name,
            "app.kubernetes.io/managed-by": __package__,
        }

        self.add_namespace(graph, graph.name, output)
        cm_name = self.add_configmap(graph, service, output)
        sec_name, use_secrets = self.add_secrets(graph, service, output)

        volumeMounts, volumes = self.add_volumes(
            graph, service, output, cm_name, use_secrets and sec_name or None
        )

        pod_labels = {
            "app": service.name,
            "version": str(service.entity.version),
        }
        pod_labels.update(labels)

        default_container = {
            "name": service.name,
            "image": service.entity.image,
            "imagePullPolicy": "IfNotPresent",
            "volumeMounts": volumeMounts,
        }

        # push out a deployment
        ports = service.ports
        # XXX: Protocol support
        dports = []
        for p in ports:
            dports.append(dict(containerPort=int(p["port"]), protocol=p["protocol"]))

        senv = service.full_config().get("environment", [])
        if dports:
            default_container["ports"] = dports
        if senv:
            default_container["env"] = senv

        container_spec = {
            "replicas": service.entity.get("replicas"),
            "selector": {"matchLabels": {"app.kubernetes.io/name": service.name}},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "restartPolicy": "Always",
                    "containers": [default_container,],
                    "volumes": volumes,
                },
            },
        }

        self.add_image_pull_secret(graph, service, output, container_spec)

        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "labels": labels,
                "namespace": graph.name,
                "name": service.name,
            },
            "spec": container_spec,
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
            ports.append(
                {"protocol": p["protocol"], "name": p["name"], "port": int(p["port"])}
            )
        serviceSpec = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"namespace": graph.name, "name": service.name},
            "spec": {
                "selector": {"app": service.name},
                # XXX: specify elb/nlb annotations when url is AWS
                # see https://kubernetes.io/docs/concepts/services-networking/service/#connection-draining-on-aws
                # https://kubernetes.io/docs/concepts/services-networking/service/#aws-nlb-support
            },
        }

        if ports:
            serviceSpec["spec"]["ports"] = ports
            # Only render the service if we have defined ports.
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
            ent.data["metadata"]["labels"]["istio-injection"] = "enabled"

    def render_service(self, service, graph, output):
        # FIXME: we will need the ability to handle any type of endpoint the service exposes
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
                                        # FIXME: this has the issue that kustomize won't see it for
                                        # name prefix changes which do impact this
                                        # this is a general problem
                                        "host": f"{service.name}.{graph.name}.svc.cluster.local",
                                        # XXX: single port at random from set, come on...
                                        "port": {"number": int(ep.ports[0].port)},
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
        output.add(
            self.fn,
            {"resources": [], "configMapGenerator": [], "secretGenerator": []},
            self,
        )

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
            files=[f"configs/{graph.name}-{service.name}-config.json"],
        )

        output.update(
            self.fn,
            data={"data.configMapGenerator": [context]},
            plugin=self,
            schema={"mergeStrategy": "append"},
        )

        context = dict(
            name=f"{service.name}-secrets",
            namespace=graph.name,
            files=[f"configs/{graph.name}-{service.name}-secrets.json"],
        )

        output.update(
            self.fn,
            data={"data.secretGenerator": [context]},
            plugin=self,
            schema={"mergeStrategy": "append"},
        )

        # If the service has "files" which should be mapped into the container
        # this will be registered here as well
        for file in service.files:
            fn = file.get("template")
            fn = utils.filename_to_label(fn)
            context = dict(
                name=f"{service.name}-{fn}",
                namespace=graph.name,
                files=[f"resources/{graph.name}-{service.name}-{fn}"],
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
        files = list(filter(lambda x: not x.startswith("resources/"), files))
        output.update(self.fn, {"data.resources": files}, self)


@dataclass(unsafe_hash=True)
class RuntimeImpl:
    name: str = field(hash=True)
    kind: str = field(init=False, hash=True, default="RuntimeImpl")
    plugins: List[RuntimePlugin] = field(hash=False)

    def __post_init__(self):
        for p in self.plugins:
            setattr(p, "runtime_impl", self)

    def plugin(self, key):
        return utils.pick(self.plugins, name=key)

    @property
    def qual_name(self):
        return self.name

    def method_lookup(self, name, reverse=True):
        plugins = self.plugins
        if reverse is True:
            plugins = reversed(plugins)

        for p in plugins:
            m = getattr(p, name, None)
            if m:
                return m
        raise AttributeError(f"RuntimeImpl plugins didn't provide a method {name}")

    def service_addr(self, service, graph):
        m = self.method_lookup("service_addr")
        return m(service, graph)


## WIP
def render_graph(graph, outputs):
    # TODO: split the rendering of relations to support 1/2 living in another runtime
    #       ex render_relation_ep(relation.ep)
    runtimes = set()
    # 1st collect all the runtimes referenced in the graph
    for obj in graph.services:
        if obj.runtime is not None:
            runtimes.add(obj.runtime)

    for runtime in runtimes:
        for plugin in runtime.plugins:
            m = getattr(plugin, "init", None)
            if m:
                m(graph, outputs)

    # Here we must resolve the correct runtime to process each
    for phase in ["pre_", "", "post_"]:
        # dynamic method resolution in the form of
        # <phase>_render_<kind.lower>
        for obj in graph.services:
            runtime = obj.runtime
            if not runtime:
                continue
            for plugin in runtime.plugins:
                kind = obj.kind.lower()
                mn = f"{phase}render_{kind}"
                m = getattr(plugin, mn, None)
                if m:
                    m(obj, graph, outputs)
        # XXX: This is more complex as we'd like each relation endpoint to be able to
        # belong to a different runtime
        # FIXME: for now this is the current behavior (which could be odd without per-endpoint runtime rendering)
        for obj in graph.relations:
            for endpoint in obj.endpoints:
                runtime = endpoint.service.runtime
                if not runtime:
                    continue
                for plugin in runtime.plugins:
                    kind = obj.kind.lower()
                    mn = f"{phase}render_relation_ep"
                    m = getattr(plugin, mn, None)
                    if m:
                        m(obj, graph, outputs, endpoint)

    for runtime in runtimes:
        for plugin in runtime.plugins:
            m = getattr(plugin, "fini", None)
            if m:
                m(graph, outputs)


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

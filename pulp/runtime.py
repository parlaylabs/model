import itertools
from dataclasses import dataclass, field
from typing import List

import yaml

from . import render


@dataclass
class RuntimePlugin:
    name: str


@dataclass
class Kubernetes:
    name: str = field(init=False, default="Kubernetes")

    def render_service(self, service, graph, output):
        # push out a deployment
        ports = set()
        for ep in service.endpoints:
            ports |= set(ep.ports)
        ports = list(ports)
        ports.sort()
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"labels": {"app": service.name}, "name": service.name},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": service.name}},
                "template": {
                    "metadata": {"labels": {"app": service.name, "origin": "pulp"}},
                    "spec": {
                        "containers": [
                            # XXX: join with context/runtime container registry
                            {
                                "name": service.name,
                                "image": service.component.get("image"),
                                "imagePullPolicy": "IfNotPresent",
                                "ports": ports,
                            }
                        ]
                    },
                },
            },
        }
        output[f"service-{service.name}-deployment.yaml"] = deployment
        # next push out a service object

    def render_relation(self, relation, graph, output):
        pass
        # print(f"render relation {relation}")


@dataclass
class Istio:
    name: str = field(init=False, default="Istio")


@dataclass
class RuntimeImpl:
    plugins: List[RuntimePlugin]

    def render(self, graph, target=None):
        # XXX: obviously pass through config
        outputs = render.FileRenderer("/tmp/manifest.yaml")

        # Run three phases populating data dicts into outputs
        # then run the renderer to create output data dicts
        # NOTE: we use data dicts here rather than the python-kube API
        # because we want to allow various plugins to operate on the data
        # rather than produce a single API call. Using the API would have the
        # advantage that we'd expect some validate, however the ability to break
        # this into layers here wins, actually applying this to the runtime (k8s)
        # will validate the results.
        for phase in ["pre_", "", "post_"]:
            # dynamic method resolution in the form of
            # <phase>_render_<kind.lower>
            for obj in itertools.chain(graph.services, graph.relations):
                for plugin in self.plugins:
                    kind = obj.kind
                    mn = f"{phase}render_{kind.lower()}"
                    m = getattr(plugin, mn, None)
                    if m:
                        m(obj, graph, outputs)
        return outputs


# XXX: this will have to become more flexible or fixed,
# this middle ground buys us little
KubernetesImpl = RuntimeImpl(plugins=[Kubernetes(), Istio()])


def resolve(runtime_name):
    # XXX: static singleton for now
    return KubernetesImpl

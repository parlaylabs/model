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
        ports = service.ports
        # XXX: ServiceAccountName
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"labels": {"app": service.name}, "name": service.name},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": service.name}},
                "template": {
                    "metadata": {
                        "labels": {"app": service.name, "origin": __package__}
                    },
                    "spec": {
                        "containers": [
                            # XXX: join with context/runtime container registry
                            # XXX: model and support cross cutting concerns here
                            {
                                "name": service.name,
                                "image": service.component.get("image"),
                                "imagePullPolicy": "IfNotPresent",
                                "ports": ports,
                            },
                        ],
                        # see https://kubernetes.io/docs/concepts/workloads/pods/pod-topology-spread-constraints/#spread-constraints-for-pods
                        "topologySpreadConstraints": [
                            {
                                "topologyKey": service.name,
                                "whenUnsatisfiable": "ScheduleAnyway",
                                "labelSelector": {"name": service.name},
                            }
                        ],
                    },
                },
            },
        }
        output.add(f"{service.name}-deployment.yaml", deployment, self)

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
                # XXX: specify elb/nlb annotations when environment is AWS
                # see https://kubernetes.io/docs/concepts/services-networking/service/#connection-draining-on-aws
                # https://kubernetes.io/docs/concepts/services-networking/service/#aws-nlb-support
            },
        }
        output.add(f"{service.name}-service.yaml", serviceSpec, self)

    def render_relation(self, relation, graph, output):
        # For now emit a network policy object
        # XXX: for now we assume a 2 ep relation
        # XXX: in the future we can indicate relations that expose things to
        # XXX: ring/quourm styled internal patterns as well
        if len(relation.endpoints) != 2:
            log.info(f"Unable to process endpoints for {relation}")
            return
        service_a = relation.endpoints[0]
        service_b = relation.endpoints[1]
        ports = []
        for p in service_a.ports:
            ports.append({"protocol": "TCP", "port": p})

        net_policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{service_a.name}-net-policy",
                # XXX: how to map?
                "namespace": "default",
            },
            "spec": {
                "podSelector": {"matchLabels": {"app": service_a.name,}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": {"from": [], "ports": ports},
                "egress": [],
            },
        }
        output.add(f"{relation.name}-net-policy.yaml", net_policy, self)

        default_policy_key = f"default-net-policy.yaml"
        if default_policy_key not in output:
            # Disable ingress by default, we want to own the network with the graph
            # to avoid whole suites of other possible issues
            output.add(
                default_policy_key,
                {
                    "apiVersion": "networking.k8s.io/v1",
                    "kind": "NetworkPolicy",
                    "metadata": {"name": "default-deny"},
                    "spec": {"podSelector": {}, "policyTypes": ["Ingress"]},
                },
                self,
            )


@dataclass
class Istio:
    name: str = field(init=False, default="Istio")


@dataclass
class Kustomize:
    name: str = field(init=False, default="Kustomize")

    def fini(self, graph, output):
        # Render a kustomize resource file into what we presume to be a base dir
        output.add("kustomization.yaml", {"resources": list(output.index.keys())}, self)


@dataclass
class RuntimeImpl:
    plugins: List[RuntimePlugin]

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


# XXX: this will have to become more flexible or fixed,
# this middle ground buys us little
KubernetesImpl = RuntimeImpl(plugins=[Kubernetes(), Istio(), Kustomize()])


def resolve(runtime_name):
    # XXX: static singleton for now
    return KubernetesImpl

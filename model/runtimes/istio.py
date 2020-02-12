from dataclasses import dataclass, field

from .. import docker
from .. import exceptions
from .. import utils
from ..runtime import register, RuntimePlugin


@register
@dataclass
class Istio(RuntimePlugin):
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


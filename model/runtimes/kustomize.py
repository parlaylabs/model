from dataclasses import dataclass, field

from .. import docker
from .. import exceptions
from .. import render
from .. import utils
from ..runtime import register, RuntimePlugin


@register
@dataclass
class Kustomize(RuntimePlugin):
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
        # XXX: we should scan for these or ask the K8s plugin directly for the names
        # right now they have to be in sync
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


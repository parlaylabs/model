import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_model_config
from .. import docker
from .. import exceptions
from .. import utils
from ..runtime import register, RuntimePlugin


cfg = get_model_config()


@register
@dataclass
class Kubernetes(RuntimePlugin):
    name: str = field(init=False, default="Kubernetes")
    expose = {"overlay", "ingress"}
    ingest = {"consul", "cloud"}

    def service_addr(self, service, graph):
        return f"{service.name}.{graph.name}.svc.cluster.local"

    def config_map_for(self, service):
        cm = dict(config=service.full_config(), relations=service.full_relations())
        return cm

    def secrets_for(self, service):
        secrets = dict(relations=service.full_relations(secrets=True))
        return secrets

    def add_namespace(self, graph, output):
        ns = dict(
            apiVersion="v1",
            kind="Namespace",
            metadata=dict(name=graph.name, labels={}),
        )
        nsfn = f"00-{graph.name}-namespace.yaml"
        if nsfn not in output:
            output.add(nsfn, ns, self, graph=graph)

    def add_service_account(self, graph, output, name=None):
        if name is None:
            name = f"{graph.name}-admin"
        data = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": name, "namespace": graph.name,},
        }
        output.add(
            f"01-service-account.yaml", data, self, graph=graph, ignore_existing=True
        )
        return name

    def add_configmap(self, graph, service, output, data=None, name=None):
        # creating the context for the config map involves all the service config and any information
        # provided by connected endpoints
        if data is None:
            data = self.config_map_for(service)
        if name is None:
            filename = f"configs/{graph.name}-{service.name}-config.json"
        output.add(
            filename, data, self, format="json", service=service, graph=graph,
        )
        return f"{service.name}-config"

    def add_secrets(self, graph, service, output, data=None, name=None):
        if data is None:
            data = self.secrets_for(service)
        if name is None:
            filename = f"configs/{graph.name}-{service.name}-secrets.json"

        output.add(
            filename, data, self, format="json", service=service, graph=graph,
        )
        return f"{service.name}-secrets", bool(data)

    def add_volumes(self, graph, service, output, configmap_name, secrets_name=None):
        volumeMounts = [
            {"name": "model", "mountPath": "/etc/model/", "readOnly": True,}
        ]

        volumes = [
            {
                "name": "model",
                "projected": {
                    "sources": [
                        {
                            "configMap": {
                                "name": configmap_name,
                                "items": [
                                    {
                                        "key": f"{graph.name}-{service.name}-config.json",
                                        "path": f"{service.name}-config.json",
                                    }
                                ],
                            }
                        },
                        {
                            "downwardAPI": {
                                "items": [
                                    {
                                        "path": "labels",
                                        "fieldRef": {"fieldPath": "metadata.labels",},
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
                    ]
                },
            }
        ]

        if secrets_name:
            volumes[0]["projected"]["sources"].append(
                {
                    "secret": {
                        "name": secrets_name,
                        "items": [
                            {
                                "mode": 0o511,
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
            cp = str(Path(container_path).parent / Path(container_path))
            key = f"{graph.name}-{service.name}-{fn}"
            volumes[0]["projected"]["sources"].append(
                dict(
                    configMap=dict(
                        name=f"{service.name}-{fn}", items=[dict(key=key, path=cp)],
                    ),
                )
            )
        return volumeMounts, volumes

    def add_storage_class(self, graph, output, name, kind="ebs"):
        data = {
            "kind": "StorageClass",
            "apiVersion": "storage.k8s.io/v1",
            "metadata": {
                "name": name,
                "namespace": graph.name,
                "provisioner": f"{kind}.csi.aws.com",
                "volumeBindingMode": "WaitForFirstConsumer",
            },
        }
        output.add(
            f"10-{name}-storageclass.yaml",
            data,
            self,
            graph=graph,
            ignore_existing=True,
        )
        return data

    def add_pvc(
        self,
        graph,
        service,
        claimName,
        mountPath,
        capacity,
        accessMode,
        container_spec,
        output,
        labels=None,
    ):
        # There are two components to a PVC
        # the path mapping in mounts
        # and the claim ref in volumes
        # FIXME: [0] is an ok default but we'd want a way to check these by id
        spec = container_spec["template"]["spec"]
        container = spec["containers"][0]
        mounts = container["volumeMounts"]
        pvm = dict(name=claimName, mountPath=mountPath)
        mounts.append(pvm)
        pvc = {"name": claimName, "persistentVolumeClaim": {"claimName": claimName,}}
        spec.setdefault("volumes", []).append(pvc)

        claim = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": claimName, "namespace": graph.name},
            "spec": {
                "accessModes": [accessMode],
                "storageClassName": claimName,
                "resources": {"requests": {"storage": capacity}},
            },
        }
        if labels:
            filtered = labels.copy()
            for k in labels:
                if k not in ["app.kubernetes.io/name", "app.kubernetes.io/part-of"]:
                    del filtered[k]
            claim["spec"]["selector"] = {"matchLabels": filtered}

        output.add(
            f"20-{claimName}-pvc.yaml",
            claim,
            self,
            service=service,
            graph=graph,
            allow_existing=True,
        )

    def assign_storage_claims(
        self, graph, service, output, container_spec, labels=None
    ):
        # see if there are volume claims
        # XXX: this should be an init() thing, not called by service, but...
        s_classes = {}
        storage_specs = graph.environment.config.get("storage")

        def _normalize_name(graph, service, n):
            return f"{graph.name}-{service.name}-{n}"

        if storage_specs:
            for s in storage_specs:
                name = _normalize_name(graph, service, s["name"])
                kind = s.get("kind", "ebs")
                if kind not in ["ebs", "efs"]:
                    raise exceptions.ConfigurationError(f"unknown storage class {kind}")
                capacity = s.get("storage")
                accessMode = s.get("accessMode", "readWriteOnce")
                # XXX: only do this if the service defines storage needs
                s_classes[name] = (kind, capacity, accessMode)
        #
        #  Now assuming there is a matching storage class for any given type
        # we can add a pvc and the needed volume mounts
        entity_storage = service.entity.get("storage")
        if not entity_storage:
            return
        for s in entity_storage:
            name = _normalize_name(graph, service, s.get("name"))
            path = s.get("path")
            if name not in s_classes:
                raise exceptions.ConfigurationError(
                    f"storage class '{name}' not defined in environment.storage"
                )
            kind, capacity, accessMode = s_classes[name]
            self.add_storage_class(graph, output, name, kind)
            self.add_pvc(
                graph,
                service,
                name,
                path,
                capacity,
                accessMode,
                container_spec,
                output=output,
                labels=labels,
            )

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

    def add_probes_from_endpoints(self, service, container_spec):
        epspecs = service.entity.get("endpoints")
        for ep in service.endpoints.values():
            epspec = utils.pick(epspecs, name=ep.name)
            if not epspec:
                continue
            probes = epspec.get("probes")
            if not probes:
                continue

            for probe in probes:
                kind = probe.get("kind")
                probeKey = f"{kind}Probe"
                startup = probe.get("startup")
                path = probe.get("path")
                command = probe.get("command")
                period = probe.get("periodSeconds", 10)
                initialDelaySeconds = probe.get("initialDelaySeconds", 20)
                failureThreshold = probe.get("failureThreshold", 30)
                payload = {}
                if initialDelaySeconds:
                    payload["initialDelaySeconds"] = initialDelaySeconds
                payload["failureThreshold"] = failureThreshold
                payload["periodSeconds"] = period
                result = {probeKey: payload}
                if ep.interface.isA("http", "server"):
                    payload["httpGet"] = {
                        "path": path,
                        # FIXME: again with the [0]
                        "port": ep.ports[0].port,
                    }
                elif ep.ports[0].protocol == "TCP":
                    payload["tcpSocket"] = {"port": ep.ports[0].port}
                else:
                    payload["exec"] = {"command": list(command)}

                if startup:
                    result["startupProbe"] = payload
                container_spec.update(result)

    def assign_host_networking(self, service, container_spec):
        networkType = service.config.get("networkType", "model/cni")
        if networkType == "model/host":
            container_spec["template"]["spec"].update(
                {
                    "hostNetwork": True,
                    "dnsPolicy": "ClusterFirstWithHostNet",
                    "nodeSelector": {"model/networkType": "host"},
                    # We taint nodes having host networking
                    # We have to tolerate that here
                    "tolerations": [
                        {
                            "key": "hostNetworking",
                            "operator": "Equal",
                            "value": "true",
                            "effect": "NoSchedule",
                        }
                    ],
                    # For things with host networking we want anti-affinity to pods of the same type
                    # which would by default bind the same host port ranges
                    "affinity": {
                        "podAntiAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": [
                                {
                                    "labelSelector": {
                                        "matchExpressions": [
                                            {
                                                "key": "app.kubernetes.io/name",
                                                "operator": "In",
                                                "values": [service.name],
                                            }
                                        ]
                                    },
                                    "topologyKey": "kubernetes.io/hostname",
                                }
                            ]
                        }
                    },
                },
            )

    def render_service(self, service, graph, output):
        labels = {
            "app.kubernetes.io/name": service.name,
            "app.kubernetes.io/instance": f"{graph.name}-{service.name}-{service.version}",
            "app.kubernetes.io/version": str(service.version),
            "app.kubernetes.io/component": service.entity.name,
            # XXX: become a graph ref
            "app.kubernetes.io/part-of": graph.name,
            "app.kubernetes.io/managed-by": "model",
        }

        self.add_namespace(graph, output)
        service_account = self.add_service_account(graph, output)

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
            "image": service.image,
            "imagePullPolicy": "IfNotPresent",
            "volumeMounts": volumeMounts,
        }
        command = service.get("command")
        args = service.get("args")
        if command:
            default_container["command"] = command
        if args:
            default_container["args"] = args

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
                    "serviceAccountName": service_account,
                    "restartPolicy": "Always",
                    "containers": [default_container,],
                    "volumes": volumes,
                },
            },
        }
        # Look at the endpoints and see if we need to add any probes
        self.add_probes_from_endpoints(service, container_spec)
        self.add_image_pull_secret(graph, service, output, container_spec)
        self.assign_host_networking(service, container_spec)
        self.assign_storage_claims(
            graph, service, output, container_spec, labels=labels
        )

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

    def render_relation_ep(self, relation, endpoint, graph, outputs):
        # If the endpoint has no runtime we want to render a service/endpoint pair
        # in the k8s runtime to make this resolve.
        # If the endpoint is in this runtime it will be handled in render_service
        other = relation.get_remote(endpoint.service)

        if other.runtime is None or (other.runtime != endpoint.service):
            expose_remote = other.service.runtime.lookup("expose", set())
            ingests_local = endpoint.service.runtime.lookup("ingest", set())
            if not (expose_remote & ingests_local):
                return
        # The current approach needs DNS resolution (sometimes) in the context
        # of the runtime (for example in AWS regions)
        # XXX: we could fix some of this with init containers
        # XXX: maybe with the default svc account creating the endpoints post DNS?
        ports = []  # {targetPort:, port: }
        for port in other.ports:
            ports.append(dict(targetPort=int(port.port), port=int(port.port)))

        subsets = []  # {addresses: [{ip:}], ports: [{port:}]}
        # FIXME: addresses should be a list, not single value, still problematic
        try:
            addresses = other.service.runtime.service_addrs(other.service, graph)
        except AttributeError:
            addresses = [other.service.runtime.service_addr(other.service, graph)]
        if not addresses:
            address = [other.provided.address]
        else:
            address = addresses[0]
        port = other.provided.port
        for address in addresses:
            subsets.append(
                dict(addresses=[{"ip": address}], ports=[{"port": int(port)}])
            )

        service = dict(
            kind="Service",
            apiVersion="v1",
            metadata=dict(name=other.service.name, namespace=graph.name),
            spec=dict(ports=ports),
        )
        if not utils.is_ip(address):
            service["spec"].update(dict(type="ExternalName", externalName=address))
        else:
            endpoints = dict(
                kind="Endpoints",
                apiVersion="v1",
                metadata=dict(name=other.service.name, namespace=graph.name),
                subsets=subsets,
            )
            outputs.add(
                f"72-{other.service.name}-endpoints.yaml",
                endpoints,
                self,
                relation=relation,
                endpoint=other,
                graph=graph,
            )
        outputs.add(
            f"70-{other.service.name}-service.yaml",
            service,
            self,
            relation=relation,
            endpoint=other,
            graph=graph,
        )

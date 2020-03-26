import json
import logging
import subprocess
import time

from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml

from . import entity
from . import exceptions
from . import model
from . import schema
from . import utils

log = logging.getLogger(__name__)
segments_map = {}


def register_class(cls):
    segments_map[cls.__name__.lower()] = cls
    return cls


@dataclass
class Segment(model.GraphObj):
    """When Pipeline Segments run they are expected to enforce loosely idempotent state changes
    such that more than one invocation should only progress towards the expected state."""

    pipeline: "Pipeline"

    def dispatch(self):
        "return the correct method/action to run for a given step"
        action = self.get("action", "run")
        method = getattr(self, action, None)
        if not method:
            raise exceptions.ConfigurationError(
                f"Pipeline segment has undefined or missing action: {action}"
            )
        return method

    def run(self, store, environment):
        print(f"Running {self.name}:{self.kind}")

    def _context(self, store, environment):
        context = dict(environment=environment, pipeline=self.pipeline)
        runtime_name = self.pipeline.get("runtime")
        if runtime_name:
            context["runtime"] = store.runtime.get(runtime_name)
        return context


@register_class
@dataclass
class Script(Segment):
    """Run a script doing any needed interpolation on the command
    """

    DEFAULT_TIMEOUT = 60

    def run(self, store, environment):
        context = self._context(store, environment)
        cmds = self.get("commands")
        if not cmds:
            cmds = [self.command]

        for c in cmds:
            cmd = self._prepare(c, context)
            result = self._run(cmd, context)

    def _prepare(self, cmd, context):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        cmd = utils.interpolate(cmd, context)
        return cmd

    def _run(self, cmd, context, **kwargs):
        timeout = self.get("timeout", self.DEFAULT_TIMEOUT)
        result = False
        try:
            # By using run() we get some nice conveniences, however it is
            # difficult to pull output as it happens because it only
            # returns a completed process
            log.debug(f"Run '{cmd}' with {kwargs}")
            result = subprocess.run(
                cmd,
                shell=not isinstance(cmd, (list, tuple)),
                timeout=timeout,
                encoding="utf-8",
                text=True,
                check=True,
                capture_output=True,
                **kwargs,
            )
        except subprocess.CalledProcessError as e:
            log.warning(f"ERROR: [{e.returncode}] {cmd} resulted in error")
            if result:
                log.warning(f"{result.stdout}\n{result.stderr}")
        except subprocess.TimeoutExpired as e:
            log.exception(f"{self.name}: {cmd} expired with timeout")
        except Exception as e:
            log.exception(f"{self.name}: {cmd} resulted in error")
            if result:
                log.warning(f"{result.stdout}\n{result.stderr}")
        else:
            log.debug(f"{self.name}: SUCCESS {cmd}\n{result.stdout}\n{result.stderr}")
        return result


@register_class
@dataclass
class KubernetesManifest(Script):
    """Apply a kubernetes manifest
    The manifest is first subject to interpolation using the model and jinja2 templating.
    """

    def run(self, store, environment):
        template = environment.get_template(self.template)
        context = self._context(store, environment)
        rendered = template.render(context)
        action = self.command or "apply"
        result = self._run(
            cmd=f"kubectl {action} -f -", context=context, input=rendered
        )

    def get_resource(self, resource, namespace="default", strip=False):
        result = self._run(
            cmd=f"kubectl get -n {namespace} -o json {resource}", context=None
        )
        resource = json.loads(result.stdout)
        if strip:
            md = resource["metadata"]
            dels = set(md.keys()) - {"name", "namespace", "labels", "annotations"}
            for k in dels:
                del md[k]
        return resource

    def patch(self, store, environment):
        # Strategy is
        # - read the object
        # - read the template
        # - do a full object patch (jsonmerge)
        # - replace the object
        resource = self.get_resource(
            self.resource, namespace=self.namespace, strip=True
        )
        template = environment.get_template(self.template)
        context = self._context(store, environment)
        rendered = template.render(context)
        rendered = yaml.load(rendered)
        output = utils.apply_overrides(resource, rendered["config"])
        output = yaml.dump(output)
        self._run(
            cmd=f"kubectl replace -n {namespace} {self.resource} -f -",
            context=context,
            input=output,
        )


@register_class
@dataclass
class Eksctl(Script):
    # provisioning can take a long time, this would create a blocking wait
    # for creates
    def run(self, store, environment):
        context = self._context(store, environment)
        cmd = self._prepare(f"eksctl {self.command}", context)
        result = self._run(cmd, context)
        return result and result.returncode == 0

    def replace_nodegroups(self, store, environment):
        # PLAN copy the input eksctl and create a modified version with updated
        # node groups. We want to do it this way to preserve things like the IAM
        # permissions associated with the the NG
        # After the NG is created
        context = self._context(store, environment)
        context["config"] = utils.interpolate(self.pipeline.config, context)
        eks_config_name = context["config"]["eksctl_config"]
        paths = context["config"].get("template_paths")
        eksconfig = self.pipeline.get_template(eks_config_name, paths=paths)
        rendered = eksconfig.render(context)
        print(rendered)


@schema.register_class
@dataclass
class Pipeline(model.GraphObj):
    kind: str = field(init=False, default="Pipeline")
    segments: List[Segment]

    def __hash__(self):
        return hash(self.name)

    def __post_init__(self):
        # Map segments to objects
        instances = []
        for segment in self.segments:
            if not isinstance(segment, Segment):
                cls = segments_map.get(segment["kind"].lower())
                if not cls:
                    raise exceptions.ConfigurationError(
                        f"unknown segment type {segment['kind']} in segment {segment['name']}"
                    )
                e = entity.Entity(segment, src_ref=self.src_ref)
                segment = cls(
                    pipeline=self, name=segment["name"], kind=segment["kind"], entity=e,
                )
            ns = e.get("namespace", "default")
            segment.namespace = ns

            instances.append(segment)
        self.segments = instances

    def run(self, store, environment):
        for segment in self.segments:
            # Adapt the calling convention to each type of segment
            # this means its either
            #    a plugin (getting passed the graph objects)
            #    a script (mapping args via interpolation)
            action = segment.dispatch()
            action(store, environment)

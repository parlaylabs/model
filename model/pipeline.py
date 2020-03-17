import logging
import subprocess
import time

from dataclasses import dataclass, field
from typing import Any, List

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

    def run(self, pipeline, store, environment):
        print(f"Running {self.name}:{self.kind}")


@register_class
@dataclass
class Script(Segment):
    """Run a script doing any needed interpolation on the command
    """

    DEFAULT_TIMEOUT = 60

    def run(self, pipeline, store, environment):
        context = dict(environment=environment)
        cmds = self.get("commands")
        if not cmds:
            cmds = [self.command]

        for c in cmds:
            cmd = self._prepare(c, context)
            result = self._run(cmd, context)
            return result and result.returncode == 0

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

        except subprocess.CalledProcessError:
            log.warning(f"ERROR: [{result.returncode}] {cmd} resulted in error")
            if result:
                log.warning(f"{result.stdout}\n{result.stderr}")
        except subprocess.TimeoutExpired as e:
            log.exception(f"{self.name}: {cmd} expired with timeout")
        except Exception as e:
            log.exception(f"{self.name}: {cmd} resulted in error")
            if result:
                log.warning(f"{result.stdout}\n{result.stderr}")
        else:
            log.info(f"{self.name}: SUCCESS {cmd}\n{result.stdout}\n{result.stderr}")
        return result


@register_class
@dataclass
class KubernetesManifest(Script):
    """Apply a kubernetes manifest
    The manifest is first subject to interpolation using the model and jinja2 templating.
    """

    def run(self, pipeline, store, environment):
        template = environment.get_template(self.template)
        context = dict(environment=environment)
        rendered = template.render(context)
        action = self.action or "apply"
        result = self._run(
            cmd=f"kubectl {action} -f -", context=context, input=rendered
        )


@register_class
@dataclass
class Eksctl(Script):
    def run(self, pipeline, store, environment):
        context = dict(environment=environment)
        cmd = self._prepare(f"eksctl {self.command}", context)
        result = self._run(cmd, context)
        return result and result.returncode == 0


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
                segment = cls(
                    name=segment["name"], kind=segment["kind"], entity=segment
                )
            instances.append(segment)
        self.segments = instances

    def run(self, store, environment):
        for segment in self.segments:
            # Adapt the calling convention to each type of segment
            # this means its either
            #    a plugin (getting passed the graph objects)
            #    a script (mapping args via interpolation)
            segment.run(self, store, environment)

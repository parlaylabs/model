import io
import json
import logging
import re
import subprocess
import tempfile
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

    def _run(self, cmd, context=None, **kwargs):
        timeout = self.get("timeout", self.DEFAULT_TIMEOUT)
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout
        timeout = kwargs.pop("timeout")
        input = kwargs.pop("input", None)
        try:
            log.debug(f"Run '{cmd}' with {kwargs}\n{input}")
            with subprocess.Popen(
                cmd,
                shell=not isinstance(cmd, (list, tuple)),
                encoding="utf-8",
                text=True,
                stdin=subprocess.PIPE if input else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **kwargs,
            ) as proc:
                out, err = proc.communicate(input, timeout=timeout)

                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(
                        proc.returncode, cmd, output=out, stderr=err
                    )
        except subprocess.TimeoutExpired as e:
            log.exception(f"{self.name}: {cmd} expired with timeout.\n{out}\n{err}")
        except (subprocess.CalledProcessError, OSError, Exception) as e:
            log.exception(f"{self.name}: {cmd} resulted in error")
            log.warning(out, err)
        else:
            log.debug(f"{self.name}: SUCCESS {cmd}\n{out}\n{err}")
            proc.stdout = out
            proc.stderr = err
            return proc
        return False


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
        return self._run(cmd=f"kubectl {action} -f -", context=context, input=rendered)

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
        rendered = yaml.safe_load(rendered)
        output = utils.apply_overrides(resource, rendered["config"])
        output = yaml.dump(output)
        log.debug(output)
        self._run(
            cmd=f"kubectl replace -n {self.namespace} {self.resource} -f -",
            context=context,
            input=output,
        )


_nodegroup_re = re.compile(r"(?P<name>[-\w]+)(-(?P<num>\d+))")


@register_class
@dataclass
class Eksctl(Script):
    # provisioning can take a long time, this would create a blocking wait
    # for creates
    def run(self, store, environment):
        context = self._context(store, environment)
        cmd = self._prepare(f"eksctl {self.command}", context)
        return self._run(cmd, context)

    def _parse_name(self, name):
        num = 0
        match = _nodegroup_re.match(name)
        if match:
            name = match.group("name")
            num = match.group("num")
        return name, int(num)

    def _select_nodegroups(self, config, name=None, labels=None):
        """
        Support selecting nodegroup(s) which have a single name and/or filtered by labels. 
        If labels are provided all must match. 
        labels can be passed as a dict of key/value pairs

        We also must look at the total set of nodegroups and identify a naming pattern
        to return the next number at which we should start naming ngs. This assumes
        a <name>-<num> pattern. However if the pattern fails to match it only means that 
        we can add -<num> to the existing names to increment the nodegroups. 
        """
        ngs = config.get("nodeGroups")
        matches = []
        if not ngs:
            raise exceptions.ConfigurationError("No nodeGroups in config file")
        if labels:
            ngs = utils.filter_iter(ngs, query={"labels": labels})
        if name:
            ngs = utils.filter_iter(ngs, name=name)
        ngs = list(ngs)
        if not ngs:
            raise exceptions.ConfigurationError(
                f"node group selector name: {name} labels: {labels} matched nothing"
            )
        base = max([self._parse_name(ng["name"])[1] for ng in ngs])
        return ngs, base

    def _clone_ng_data(self, template, name=None, labels=None):
        ngs, base = self._select_nodegroups(template, name, labels)
        cloned = []
        namemap = {}
        i = base
        for ng in ngs:
            i += 1
            name, _ = self._parse_name(ng["name"])
            name = f"{name}-{i}"
            namemap[ng["name"]] = name
            ng["name"] = name
            cloned.append(ng)
        result = template.copy()
        result["nodeGroups"] = ngs

        return result, namemap

    def _verify_config(self, data, store, environment):
        # very simple sanity checks
        assert data.get("kind") == "ClusterConfig"
        assert data.get("apiVersion", "").startswith("eksctl.io/v1")
        md = data.get("metadata", {})
        name = md.get("name")
        if name:
            assert name == environment.config["cluster"]
        region = md.get("region")
        if region:
            assert region == environment.config["region"]
        assert data.get("nodeGroups")

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
        config = eksconfig.render(context)
        config = utils.AttrAccess(yaml.safe_load(config))
        self._verify_config(config, store, environment)
        name = self.get("nodegroup")
        labels = self.get("labels")
        config, namemap = self._clone_ng_data(config, name=name, labels=labels)
        log.info(
            "Creating and draining nodegroups can take a substantial amount of time, 20m default timeout"
        )
        # XXX: run loop in parallel??
        cn = config["metadata"]["name"]
        for old, new in namemap.items():
            # create the new group
            log.info(f"creating new node group {new}")
            self._run(
                f"eksctl create nodegroup --config-file - --include='{new}'",
                input=config,
            )
            # XXX: We almost certainly want a wait-nodes loop here
            # delete the old group
            # this will do the drain as well
            log.info(f"deleting old nodegroup {old}")
            self._run(
                f"eksctl delete nodegroup -w --approve --cluster={cn} {old}",
                timeout=(20 * 60),
            )


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

    def run(self, store, environment, segments=None):
        for segment in self.segments:
            if segments and segment.name not in segments:
                continue
            # Adapt the calling convention to each type of segment
            # this means its either
            #    a plugin (getting passed the graph objects)
            #    a script (mapping args via interpolation)
            action = segment.dispatch()
            action(store, environment)

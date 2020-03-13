from dataclasses import dataclass, field
import logging

import boto3

from .. import docker
from .. import exceptions
from .. import render
from .. import utils
from ..runtime import register, RuntimePlugin

log = logging.getLogger(__name__)


@register
@dataclass
class EC2(RuntimePlugin):
    name: str = field(init=False, default="ec2")
    expose = {"cloud"}
    ingest = set()

    def __init__(self, **kwargs):
        # Check that we have connectivity to a region
        # FIXME: take region info from composed config
        self.ec2 = boto3.client("ec2")
        self.iam = boto3.client("iam")
        self.eks = boto3.client("eks")
        self.asg = boto3.client("autoscaling")
        self.elb = boto3.client("elb")

    def get_instance_by_tags(self, **kwargs):
        flist = [{"Name": f"tag:{k}", "Values": [v]} for k, v in kwargs.items()]
        log.debug(f"getting ec2 instances by tag: [{flist}]")
        results = self.ec2.describe_instances(Filters=flist)
        return [r["Instances"][0] for r in results["Reservations"]]

    def instances_in_asg(self, ins, validate=False):
        """given a list of instances see if they belong to an EC2 AutoScalingGroup"""
        if not ins:
            return None
        ids = [i["InstanceId"] for i in ins]
        result = self.asg.describe_auto_scaling_instances(InstanceIds=ids)
        output = {"ids": {}, "asg": {}}
        for r in result["AutoScalingInstances"]:
            output["ids"][r["InstanceId"]] = r
            output["asg"].setdefault(r["AutoScalingGroupName"], []).append(r)
        if validate:
            if set(ids) == set(output["ids"].keys()):
                # FIXME: use a real exc
                raise KeyError(
                    f"some instance ids provided are not in an ASG as expected"
                )
        return output

    def get_lb_for_asg(self, *asgs):
        output = {}
        results = self.asg.describe_auto_scaling_groups(AutoScalingGroupNames=asgs)[
            "AutoScalingGroups"
        ]
        for asg in results:
            lbns = asg["LoadBalancerNames"]
            res = self.elb.describe_load_balancers(LoadBalancerNames=[lbns])[
                "LoadBalancerDescrptions"
            ]
            for r in res:
                output.setdefault(asg["AutoScalingGroupName"], []).append(r)
        return output

    def service_addrs(self, service, graph):
        # Here we map between tagged instances in the EC2 tag namespace
        # and their PrivateIPAddress to return the set of addresses
        # the service should include
        tags = service.get("tags", {})
        if not tags:
            return []
        ins = self.get_instance_by_tags(**tags)
        try:
            asgs = self.instances_in_asg(ins)
            lbs = self.lb_for_asg(*asgs["asg"].keys())
        except Exception as e:
            pass
        # XXX: If we have lbs we can use DNSName here for the address. (if we want to connect over public)
        # but using the lb here makes sense sometimes
        return [i["PrivateIpAddress"] for i in ins]

    def service_addr(self, service, graph):
        return self.service_addrs(service, graph)[0]

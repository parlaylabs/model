from dataclasses import dataclass, field

import boto3

from .. import docker
from .. import exceptions
from .. import render
from .. import utils
from ..runtime import register, RuntimePlugin


@register
@dataclass
class EC2(RuntimePlugin):
    name: str = field(init=False, default="ec2")

    def __init__(self, **kwargs):
        # Check that we have connectivity to a region
        # FIXME: take region info from composed config
        self.ec2 = boto3.client("ec2")
        self.iam = boto3.client("iam")
        self.eks = boto3.client("eks")

    def get_instance_by_tags(self, **kwargs):
        flist = [{"Name": f"tag:{k}", "Values": [v]} for k, v in kwargs.items()]
        results = self.ec2.describe_instances(Filters=flist)
        return [r["Instances"][0] for r in results["Reservations"]]

    def service_addrs(self, service, graph):
        # Here we map between tagged instances in the EC2 tag namespace
        # and their PrivateIPAddress to return the set of addresses
        # the service should include
        tags = service.get("tags", {})
        if not tags:
            return []
        ins = self.get_instance_by_tags(**tags)
        return [i["PrivateIpAddress"] for i in ins]

    def service_addr(self, service, graph):
        return self.service_addrs(service, graph)[0]

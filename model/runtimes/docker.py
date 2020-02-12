from dataclasses import dataclass, field

from .. import docker
from .. import exceptions
from .. import utils
from ..runtime import register, RuntimePlugin


@register
@dataclass
class Docker(RuntimePlugin):
    name: str = field(init=False, default="Docker")

    def init(self, graph, outputs):
        # Parse the users docker conf file
        # and record a list of logins we know about
        self.auths = set()
        self.cfg = None
        self.graph = graph
        self.image_pull_secrets = {}
        cfg = docker.parse_config()
        if cfg:
            self.auths |= set(cfg.get("auths").keys())
            self.cfg = cfg

    def image_secrets_for(self, image):
        m = docker.parse_docker_tag(image)
        if not m or m["domain"] not in self.auths:
            return None
        r = utils.AttrAccess(
            auth=docker.auth_for(self.cfg, m["domain"]),
            key=f"{self.graph.name}-{m['domain']}",
        )
        self.image_pull_secrets[r.key] = r
        return r


import json
import re
from pathlib import Path


# Utils for managing ~/.docker/config.json
def parse_config(cfg_name=None):
    if not cfg_name:
        cfg_name = Path("~/.docker/config.json").expanduser()
    if not cfg_name.exists:
        return None
    data = json.load(cfg_name.open(mode="r"))
    return data


def auth_for(cfg, domain):
    auths = {}
    output = dict(auths=auths, HttpHeaders=cfg["HttpHeaders"])
    auths[domain] = cfg["auths"][domain]
    return output


# parse docker tag should return the match object
tag_re = re.compile(
    r"^((?P<domain>[^/]+)/)?((?P<org>[^/]+)/)?(?P<image>[^:]+)(:(?P<version>[\w\d]+))?$"
)


def parse_docker_tag(tag):
    m = tag_re.match(tag)
    if not m:
        return None
    return m.groupdict()


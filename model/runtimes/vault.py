from dataclasses import dataclass, field

import hvac

from .. import config
from .. import docker
from .. import exceptions
from .. import render
from .. import utils
from ..runtime import register, RuntimePlugin


class VaultProxy:
    def __init__(self, **kwargs):
        c = self.client = hvac.Client(**kwargs)
        if not c.is_authenticated():
            raise exceptions.ConfigurationError(f"Unable to connect vault client")
        self.src_map = {
            "kv": c.secrets.kv.v1.read_secret,
        }

    def __getitem__(self, vault_path):
        # lookup path in the format [src:]<vault/path>
        # This will return the value for the keyname or the whole dict of values if no
        # keyname was specified.
        src, _, path = vault_path.rpartition(":")
        if not src:
            src = "kv"
        driver = self.src_map[src]
        obj = driver(path)
        return utils.AttrAccess(obj["data"])


@register
@dataclass
class Vault(RuntimePlugin):
    # This allows vault access by taking the config from the plugin state
    # and providing a simple interpolator in the context
    name: str = field(init=False, default="Vault")

    def load(self):
        # Init plugins should be able to return context vars that get added to the
        # default object space for interpolation. In this case we want to add `vault`
        # to the context such that other objects can resolve from it
        # XXX: we'll need a global scope to reference in all context building
        self.client = VaultProxy(**self.config)
        # Add a global
        ctx = config.get_context()
        ctx["vault"] = self.client

    def render_service(self, graph, outputs, service):
        # add configmap and tls secret to any service deployment spec
        # this will let us map secrets from vault into the FS as needed
        pass

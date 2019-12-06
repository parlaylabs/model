from io import IOBase
import logging
from pathlib import Path

import jsonschema
import yaml

from . import entity
from . import utils

log = logging.getLogger(__package__)
_marker = object()
schema_map = {}


class Schema(dict):
    def validate(self, document):
        jsonschema.validate(document, self, format_checker=jsonschema.FormatChecker())

    @property
    def properties(self):
        return self["properties"].keys()

    def getProperty(self, name):
        return self["properties"].get(name)

    def schema_defaults(self, schema):
        output = {}

        def _build(s, result):
            props = s.get("properties")
            if not props:
                return
            for k, data in props.items():
                kind = data.get("type")
                default = data.get("default")
                if kind == "object":
                    result[k] = {}
                    _build(data, result[k])
                    continue
                elif kind == "array" and not default:
                    default = []
                elif kind == "string" and not default:
                    default = ""
                result[k] = default

        if schema:
            _build(self, output)
        return output


def register_class(cls):
    register(cls.kind, getattr(cls, "schema", None), cls)
    return cls


def register(kind, schema=None, cls=None):
    if not schema and not cls:
        raise ValueError("must supply schema and/or cls to register")
    if schema and not isinstance(schema, Schema):
        schema = Schema(schema)
    schema_map[kind] = (schema, cls)


def lookup(kind):
    return schema_map.get(kind, (None, None))


def load_and_store(fh, store):
    if isinstance(fh, (str, Path)):
        fp = open(fh, "r", encoding="utf-8")
    elif not isinstance(fh, IOBase):
        raise ValueError(f"expected filename or file object {fh}")
    else:
        fp = fh
    try:
        for obj in yaml.safe_load_all(fp):
            # blindly assume we have a dict here
            kind = obj["kind"]
            name = obj["name"]
            qual_name = f"{kind}:{name}"
            schema, cls = lookup(kind)
            if qual_name in store.qual_name:
                e = store.qual_name[qual_name]
                e.add_facet(obj, fp.name)
            else:
                e = entity.Entity.from_schema(obj, schema, fp.name)
                if cls is not None:
                    e = utils.apply_to_dataclass(cls, entity=e, **e.serialized())
            store.add(e)
    finally:
        fp.close()


def load_config(store, config_dir):
    p = Path(config_dir)
    if not p.exists() or not p.is_dir():
        raise OSError("""No config directory -- post alpha this won't be required""")
    for yml in sorted(p.rglob("*.yaml")):
        log.debug(f"Loading config from {yml}")
        load_and_store(yml, store)
    # Validate after all loading is done
    for obj in store.qual_name.values():
        obj.validate()


# v1 schema definitions
# XXX: These must actually be versioned in the future
register(
    "Graph",
    schema={
        "$schema": "http://json-schema.org/draft-08/schema#",
        "properties": {
            "name": dict(type="string"),
            "kind": dict(type="string"),
            "services": {"type": "array", "items": {"type": "object",},},
            "relations": {
                "type": "array",
                "items": {
                    type: "array",
                    "items": {"type": "string", "minItems": 1, "uniqueItems": True},
                },
            },
        },
        "required": ["name", "kind", "services"],
    },
)


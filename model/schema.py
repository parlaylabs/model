from io import IOBase
from pathlib import Path

import jsonschema
import yaml

from . import entity

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

    @classmethod
    def schema_defaults(cls, schema):
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


def register(kind, schema):
    schema_map[kind] = schema


def lookup(kind):
    return schema_map.get(kind)


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
            schema = lookup(kind)
            if qual_name in store.qual_name:
                e = store.qual_name[qual_name]
                e.add_facet(obj, fp.name)
            else:
                e = entity.Entity.from_schema(obj, schema, fp.name)
            store.add(e)
    finally:
        fp.close()


# v1 schema definitions

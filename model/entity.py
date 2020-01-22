from copy import deepcopy
import logging
import json
import yaml

from collections import ChainMap

import jmespath
import jsonmerge
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from pathlib import Path

from . import template
from . import utils


log = logging.getLogger("entity")
_marker = object()


class Entity:
    """An Immutable Document with optional ..schema support.

    At minimum Entities should have 
        - name(str)
        - kind(str)

    If you care about the validity of the document you
    must call .validate() -> exc or .valid -> boolget
    """

    def __init__(self, data=None, schema=None, src_ref=None):
        self.__data = utils.MergingChainMap()
        self.schema = schema
        self.src_ref = []
        if data is not None:
            self.add_facet(data, src_ref)

    def __str__(self):
        return utils.dump(self.serialized())

    def add_facet(self, data, src_ref):
        self.__data.new_child(data)
        if (
            isinstance(src_ref, str)
            and not src_ref.startswith("http://")
            and not src_ref.startswith("https://")
            and (not src_ref.startswith("<") and not src_ref.endswith(">"))
        ):
            src_ref = Path(src_ref).absolute()
        self.src_ref.append(src_ref)

    def relative_file(self, name=None, offset=0):
        """return a base directory or uri from which to resolve internal component references
        This defaults to the directory holding the imported file or if pull remotely the base uri.
        """
        base = "."
        if self.src_ref:
            # Don't use any overlays as the default (unless we want to use this to build a search path)
            base = self.src_ref[offset]
        if isinstance(base, str):
            if base.startswith("http://") or base.startswith("https://"):
                base = utils.uri_relative(base, name)
            elif base.startswith("<") and base.endswith(">"):
                # Convention of internally managed facets
                # which don't have a real path
                return None
        else:
            if name:
                base = "file://" + str(Path(base).parent / name)
            else:
                base = "file://" + str(Path(base).parent)
        return base

    def file_search_path(self, name=None):
        paths = {}
        # Here we exploit the properties of ordered dicts to unique the keys
        # while keeping order
        for i in range(len(self.src_ref)):
            paths[self.relative_file(name=name, offset=i)] = 1
        return list(filter(None, paths.keys()))

    def get_template(self, name, jinja_env=None, extra=None):
        if not jinja_env:
            jinja_env = template.get_env()
        paths = self.file_search_path(name)
        if extra:
            path = extra.file_search_path(name) + paths
        return jinja_env.select_template(paths)

    def __hash__(self):
        return hash((self.__data["name"], self.__data["kind"]))

    @classmethod
    def from_schema(cls, data, schema, src_ref=None):
        """populate instance from schema defaults (or None)"""
        if schema:
            defaults = schema.schema_defaults(schema)
            # Note that here we take advantage of jsonmerge's schema annotations to drive the merge behavior.
            merger = jsonmerge.Merger(schema)
            defaults = merger.merge(defaults, dict(data))
        else:
            defaults = data
        return cls(defaults, schema, src_ref)

    def serialized(self):
        return dict(self.__data)

    def validate(self):
        if self.schema is not None:
            self.schema.validate(self.__data)
        return self

    def valid(self):
        try:
            self.validate()
        except ValidationError as e:
            log.info("Validation error %s", e, exc_info=True)
            return False
        return True

    def __getitem__(self, key):
        return utils.prop_get(self.__data, key)

    def __getattr__(self, key):
        val = self.__data.get(key, _marker)
        if val is _marker:
            raise AttributeError(key)
        return val

    def __eq__(self, other):
        return other.serialized() == self.serialized()

    def get(self, key, default=None):
        return utils.prop_get(self.__data, key, default)

    def __repr__(self):
        ref = ""
        if self.src_ref:
            ref = f"@{self.src_ref}"
        return f"<Entity {self.kind}::{self.name} {ref}>"

    @property
    def data(self):
        # we return items as an immutable view
        return self.__data.items()

    @property
    def facets(self):
        maps = self.__data._maps
        refs = self.src_ref
        return tuple(reversed(tuple(zip(maps, refs))))

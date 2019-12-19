import copy
import functools
import itertools
import json

from collections import ChainMap
from dataclasses import fields
from pathlib import Path

import jmespath
import jsonmerge

_marker = object()


class AttrAccess(dict):
    def __getattr__(self, key):
        v = self[key]
        if isinstance(v, dict):
            v = AttrAccess(v)
        return v

    def __getitem__(self, key):
        v = super().__getitem__(key)
        if isinstance(v, dict):
            v = AttrAccess(v)
        return v


def nested_get(obj, path, default=None, sep="."):
    if not path:
        return obj
    o = obj
    for part in path.split(sep):
        try:
            o = getattr(o, part)
        except AttributeError:
            if part not in o:
                return default
            o = o[part]
    return o


def nested_set(obj, path, value, sep="."):
    if not path:
        o = obj
        key = path
    parts = path.split(sep)
    key = parts.pop()
    o = nested_get(obj, sep.join(parts), sep=sep)
    if isinstance(o, dict) or hasattr(o, "__setitem__"):
        o[key] = value
    else:
        setattr(o, key, value)
    return obj


def merge_paths(obj, overrides, schema=None):
    """
    Run a series of merge operations based on overrides.

    Overrides should be in dict in the format 
    { dotted_path_expr: {data_to_merge}}

    which will in turn be jsonmerged

    right now a single schema can be provided to turn the merge op 
    which is a bit awkward for addressing multiple paths so we make revisit this.
    """
    if schema is None:
        schema = {}

    for expr, data in overrides.items():
        o = nested_get(obj, expr)
        result = jsonmerge.merge(o, data, schema=schema)
        nested_set(obj, expr, result)
    return obj


def merge_path(obj, path, data, schema=None):
    overrides = {path: data}
    return merge_paths(obj, overrides, schema=schema)


def _interpolate_str(v, data_context):
    if v.startswith("{{") and v.endswith("}}") and v.count("{") == 2:
        # parse out the path. resolve it
        v = v[2:-2]
        return interpolate(nested_get(data_context, v), data_context)
    else:
        return v.format_map(data_context)


def interpolate(data, data_context=None):
    if isinstance(data, (str, int, float, dict, tuple, list, set)):
        result = type(data)()
    else:
        result = copy.copy(data)

    data_context = AttrAccess(data_context)
    if isinstance(data, list):
        for item in data:
            result.append(interpolate(item, data_context))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                result[k] = interpolate(v, data_context)
            elif isinstance(v, list):
                lst_result = []
                for item in v:
                    lst_result.append(interpolate(item, data_context))
                result[k] = lst_result
            elif isinstance(v, str):
                # Check the very special case that there is a single quoted var in the format string
                #  in this case we wish to allow non-string returns
                result[k] = _interpolate_str(v, data_context)
            else:
                result[k] = v
    elif isinstance(data, str):
        return _interpolate_str(data, data_context)
    else:
        if hasattr(data, "serialized"):
            return data.serialized()
        else:
            return data
    return result


class MergingChainMap(ChainMap):
    def __getitem__(self, key):
        parts = []
        for mapping in self.maps:
            try:
                v = mapping[key]
                parts.insert(0, v)
            except KeyError:
                pass
        if not parts:
            return self.__missing__(key)
        # Deep merge the components so last write wins
        # we do insert 0 above so we can use natural orderin here
        if len(parts) == 1:
            # fast path
            return parts[0]
        r = {}
        while parts:
            r = jsonmerge.merge(r, parts.pop(0))
        return r


def filter_select(item, query):
    for k, expect in query.items():
        try:
            v = nested_get(item, k)
        except (AttributeError, TypeError):
            v = getattr(item, k, _marker)
        if v != expect:
            return False
    return True


def filter_iter(lst, query=None, reversed=False, predicate=filter_select, **kwargs):
    if not query:
        query = {}
        query.update(kwargs)
    selector = filter
    if reversed:
        selector = itertools.filterfalse
    predicate = functools.partial(predicate, query=query)
    return selector(predicate, lst)


def pick(lst, query=None, default=None, **kwargs):
    if not query:
        query = {}
        query.update(kwargs)

    for item in lst:
        match = True
        for k, expect in query.items():
            try:
                v = nested_get(item, k)
            except (AttributeError, TypeError):
                v = getattr(item, k, _marker)
            if v != expect:
                match = False
                continue
        if match:
            return item
    return default


def _dumper(obj):
    m = getattr(obj, "serialized", None)
    if m:
        if callable(m):
            return m()
        else:
            return m
    else:
        return obj


def dump(obj):
    """Dump objects as JSON. If objects have a serialized method or property it
    will be used in the resulting output"""
    return json.dumps(obj, default=_dumper, indent=2)


def apply_to_dataclass(cls, **kwargs):
    # filter kwargs such that only fields are present
    args = {}
    ff = fields(cls)
    for k in kwargs:
        f = pick(ff, name=k)
        if f and f.init is not False:
            args[k] = kwargs[k]
    return cls(**args)


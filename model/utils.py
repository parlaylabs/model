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
            v = self.__class__(v)
        return v

    def __getitem__(self, key):
        v = super().__getitem__(key)
        if isinstance(v, dict):
            v = self.__class__(v)
        return v

    def __setattr__(self, key, value):
        self[key] = value

    def serialized(self):
        return dict(self)


def nested_get(obj, path=None, default=None):
    try:
        return jmespath.search(path, obj)
    except IndexError:
        return default


def nested_set(obj, path, value):
    if not path:
        o = obj
        key = path
    parts = path.split(".")
    key = parts.pop()
    o = prop_get(obj, ".".join(parts))
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
        o = prop_get(obj, expr)
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


def window(seq, n=2):
    "Returns a sliding window (of width n) over data from the iterable"
    "   s -> (s0,s1,...s[n-1]), (s1,s2,...,sn), ...                   "
    it = iter(seq)
    result = tuple(itertools.islice(it, n))
    if len(result) == n:
        yield result
    for elem in it:
        result = result[1:] + (elem,)
        yield result


class MergingChainMap(dict):
    def __init__(self, data=None, **kwargs):
        super().__init__()
        if data is not None:
            data = dict(data)
        else:
            data = {}
        data.update(kwargs)
        self._maps = [data]
        self._update()

    def new_child(self, data=None):
        if data is None:
            data = {}
        self._maps.insert(0, dict(data))
        self._update()
        return self

    def remove_child(self):
        if len(self._maps) > 1:
            self._maps.pop(0)
            self._update()
        return self

    def __enter__(self):
        self.new_child()
        return self

    def __exit__(self, ex_type, value, tb):
        if value:
            raise value.__with_traceback__(tb)

    def __setitem__(self, key, value):
        self._maps[0][key] = value
        self._update()

    def update(self, data):
        self._maps[0].update(dict(data))
        self._update()
        return self

    def _update(self):
        if len(self._maps) == 1:
            self.clear()
            super().update(self._maps[0])
            return
        r = {}
        for m in reversed(self._maps):
            r = jsonmerge.merge(r, m)
        self.clear()
        super().update(r)


def prop_get(obj, path, default=None, sep="."):
    if not path:
        return obj
    o = obj
    for part in path.split(sep):
        try:
            o = getattr(o, part)
        except (AttributeError, KeyError):
            if part not in o:
                return default
            o = o[part]
    return o


def filter_select(item, query):
    for k, expect in query.items():
        try:
            v = prop_get(item, k)
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
        r = filter_select(item, query)
        if not r:
            continue
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


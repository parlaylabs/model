import ast
import copy
import functools
import importlib
import ipaddress
import itertools
import json
import pkgutil
import re
import urllib.parse

from collections import ChainMap
from dataclasses import fields
from pathlib import Path

import gitignore_parser
import jmespath
import jsonmerge
import yaml

_marker = object()


class AttrAccess(dict):
    def __getattr__(self, key):
        v = self[key]
        if isinstance(v, dict):
            v = self.__class__(v)
        return v

    def __getitem__(self, key):
        try:
            v = super().__getitem__(key)
        except KeyError as e:
            raise AttributeError(key)
        if isinstance(v, dict):
            v = self.__class__(v)
        return v

    def __setattr__(self, key, value):
        self[key] = value

    def serialized(self):
        return dict(self)


def AttrAccess_representer(dumper, data):
    return dumper.represent_dict(dict(data))


yaml.add_representer(AttrAccess, AttrAccess_representer)


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


def apply_overrides(obj, plan):
    """plan_overrides maps from a list of data plans to produce a jsonmerge schema 
    and data to drive merge_paths
    
    Arguments:
        obj -- object to update
        plan -- list of {path: str, strategy: [append, overwrite, arrayMergeById], data: Any, extras}
    """
    for p in plan:
        path = p["path"]
        name = path.rsplit(".", 1)[-1]
        inline = p.get("inline", False)
        data = p["data"]
        # XXX: this is flat, but could build a deeply nested schema to recreate the path properly
        strat = p.get("strategy", "objectMerge")
        schema = {"mergeStrategy": strat}
        if strat == "arrayMergeById":
            schema["idRef"] = p.get("id", "id")
        fmt = p.get("format", "yaml")
        obj = merge_path(obj, path, data, schema=schema, inline=inline, format=fmt)
    return obj


def merge_paths(obj, overrides, schema=None, inline=False, format="yaml"):
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
        if inline:
            if format == "yaml":
                o = yaml.load(o)
                data = yaml.load(data)
            else:
                raise ValueError("unknown format")
        result = jsonmerge.merge(o, data, schema=schema)
        if inline:
            if format == "yaml":
                result = yaml.dump(result)
        nested_set(obj, expr, result)
    return obj


def merge_path(obj, path, data, schema=None, inline=False, format="yaml"):
    overrides = {path: data}
    return merge_paths(obj, overrides, schema=schema, inline=inline, format=format)


_fstring_expr = re.compile("{(?P<expr>[^}]+?)}|(?P<str>[^{]+)")


def fstring(string, data_context):
    # This is an f-string like mini-implementation
    # we do this to make pulling expressions from user written yaml
    # function in a way like f-strings (able to eval expressions)
    output = []
    matches = re.finditer(_fstring_expr, string)
    for m in matches:
        expr = m.group("expr")
        string = m.group("str")
        if expr:
            expr = ast.parse("(" + expr + ")", "<interpolation>", "eval")
            code = compile(expr, "<interpolation>", "eval")
            result = eval(code, None, data_context)
            output.append(result)
        elif string:
            output.append(string)
    if len(output) > 1:
        return "".join([str(s) for s in output])
    return output[0]


def _interpolate_str(v, data_context, allow_missing=False):
    try:
        # Fast path -- however computed properties might make the 2nd form nearly as fast
        return v.format_map(data_context)
    except (AttributeError, KeyError):
        try:
            return fstring(v, data_context)
        except AttributeError as e:
            if allow_missing:
                return v
            raise AttributeError(f"Error interpolating {v} with {data_context.keys()}")


def interpolate(data, data_context=None, allow_missing=False):
    if isinstance(data, (dict, tuple, list, set)):
        result = type(data)()
    else:
        result = copy.copy(data)

    data_context = AttrAccess(data_context)
    if isinstance(data, list):
        for item in data:
            result.append(interpolate(item, data_context, allow_missing))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                result[k] = interpolate(v, data_context)
            elif isinstance(v, (list, tuple, set)):
                lst_result = []
                for item in v:
                    lst_result.append(interpolate(item, data_context, allow_missing))
                result[k] = lst_result
            elif isinstance(v, str):
                # Check the very special case that there is a single quoted var in the format string
                #  in this case we wish to allow non-string returns
                result[k] = _interpolate_str(v, data_context, allow_missing)
            else:
                result[k] = v
    elif isinstance(data, str):
        return _interpolate_str(data, data_context, allow_missing)
    else:
        if hasattr(data, "serialized"):
            return interpolate(data.serialized(), data_context, allow_missing)
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


def uri_relative(uri, name=None):
    parts = urllib.parse.urlsplit(uri)
    parts = list(parts)
    path = Path(parts[2]).parent
    if name:
        parts[2] = (path / name).resolve()
    else:
        parts[2] = path
    parts[2] = str(parts[2])
    return urllib.parse.urlunsplit(parts)


def filename_to_label(filename):
    f = filename.replace(".", "-")
    f = f.replace("_", "-")
    return f


def is_ip(string):
    try:
        ipaddress.ip_address(string)
    except ValueError:
        return False
    return True


def import_submodules(package, recursive=True):
    """ Import all submodules of a module, recursively, including subpackages

    :param package: package (name or actual module)
    :type package: str | module
    :rtype: dict[str, types.ModuleType]
    """
    if isinstance(package, str):
        package = importlib.import_module(package)
    results = {}
    for loader, name, is_pkg in pkgutil.walk_packages(package.__path__):
        full_name = package.__name__ + "." + name
        results[full_name] = importlib.import_module(full_name)
        if recursive and is_pkg:
            results.update(import_submodules(full_name))
    return results


def modelignore_matcher(directory):
    # See if a modelignore file exists and return a matcher or return a truth function
    mipath = Path(directory / ".modelignore").absolute()
    if mipath.exists():
        return gitignore_parser.parse_gitignore(mipath, base_dir=directory)
    else:
        return lambda fn: False

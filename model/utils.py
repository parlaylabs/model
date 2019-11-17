import copy
import json

from pathlib import Path

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


def nested_get(adict, path, default=None, sep="."):
    o = adict
    for part in path.split(sep):
        if part not in o:
            return default
        o = o[part]
    return o


def _interpolate_str(v, data_context):
    if v.startswith("{") and v.endswith("}") and v.count("{") == 1:
        # parse out the path. resolve it
        v = v[1:-1]
        return interpolate(nested_get(data_context, v), data_context)
    else:
        return v.format_map(data_context)


def interpolate(data, data_context=None):
    result = type(data)()
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
        return data
    return result


def deepmerge(dest, src):
    """
    Deep merge of two dicts.

    This is destructive (`dest` is modified), but values
    from `src` are passed through `copy.deepcopy`.
    """
    for k, v in src.items():
        if dest.get(k) and isinstance(v, dict):
            deepmerge(dest[k], v)
        else:
            dest[k] = copy.deepcopy(v)
    return dest


def pick(lst, default=None, **kwargs):
    for item in lst:
        match = True
        for k, expect in kwargs.items():
            try:
                v = item.get(k)
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

import itertools
from contextlib import contextmanager
from functools import wraps

import click


class spec(dict):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.update(kwargs)

    def __getitem__(self, key):
        if isinstance(key, int):
            # can return IndexError
            return self.args[key]
        return super().__getitem__(key)

    def __getattr__(self, key):
        return super().__getitem__(key)

    def __getstate__(self):
        return self.__dict__

    def __repr__(self):
        return f"<spec {self.args} {super().__repr__()}>"


def using(configObj, *cmds):
    def _add_option(func, option):
        return click.option(*option.args, expose_value=True, **option)(func)

    def decorator(f):
        for argspecs in itertools.chain(cmds):
            for option in argspecs:
                f = _add_option(f, option)

        return click.make_pass_decorator(configObj, True)(f)

    return decorator


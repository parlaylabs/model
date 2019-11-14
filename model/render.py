import io
import sys

from contextlib import contextmanager
from pathlib import Path

import yaml


@contextmanager
def streamer(fn_or_fp):
    closing = False
    if str(fn_or_fp) == "-":
        fn_or_fp = sys.stdout

    if isinstance(fn_or_fp, io.TextIOBase):
        fp = fn_or_fp
    else:
        fp = open(fn_or_fp, "w", encoding="utf-8")
        closing = True
    yield fp
    if closing:
        fp.close()


class Renderer(dict):
    def __init__(self, root=None):
        self.root = Path(root)


class DirectoryRenderer(Renderer):
    def write(self):
        if not self.root.exists():
            self.root.mkdir()
        for fn, data in self.items():
            ofn = self.root / fn
            with open(ofn, "w", encoding="utf-8") as fp:
                if not isinstance(data, list):
                    data = [data]
                print("---", file=fp)
                yaml.safe_dump_all(data, stream=fp)


class FileRenderer(Renderer):
    def write(self):
        with streamer(self.root) as fp:
            for fn, data in self.items():
                if not isinstance(data, list):
                    data = [data]
                print("---", file=fp)
                yaml.safe_dump_all(data, stream=fp)


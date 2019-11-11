from pathlib import Path

import yaml


class Renderer(dict):
    def __init__(self, root=None):
        self.root = Path(root)


class DirectoryRenderer(Renderer):
    def render(self):
        if not self.root.exists():
            self.root.makedir()
        for fn, data in self.items():
            ofn = self.root / fn
            with open(ofn, "w", encoding="utf-8") as fp:
                if not isinstance(data, list):
                    data = [data]
                print("---", file=fp)
                yaml.safe_dump_all(data, stream=fp)


class FileRenderer(Renderer):
    def render(self):
        with open(self.root, "w", encoding="utf-8") as fp:
            for fn, data in self.items():
                if not isinstance(data, list):
                    data = [data]
                print("---", file=fp)
                yaml.safe_dump_all(data, stream=fp)


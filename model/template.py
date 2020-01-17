import requests
import requests_file

from jinja2 import Environment, BaseLoader, TemplateNotFound


class URILoader(BaseLoader):
    def __init__(self):
        self.session = requests.Session()
        self.session.mount("file://", requests_file.FileAdapter())

    def get_source(self, environment, template):
        r = self.session.get(template)
        if not r.status_code == requests.codes.ok:
            raise TemplateNotFound(template)
        # TODO: check etags and similar with read uptodate function
        # TODO: support dynamic search paths, in effect any entity can have its own search path
        # but we only want one environment.
        return (r.text, template, lambda: True)


def get_env():
    return Environment(loader=URILoader())


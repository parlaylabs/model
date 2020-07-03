import logging
from functools import partial

from .render import match_plugin
from .runtime import RuntimePlugin
from .utils import filter_iter, prop_get, filter_select, filter_callables

log = logging.getLogger(__name__)


def annotation_matcher(item, name, value=None):
    found = item.annotations.get(name)
    if value is None:
        return found is not None
    return found == value


class Feature(RuntimePlugin):
    """
    A Runtime plugin designed to be used on the output of other plugins.

    To support this we allow matching various attributes of the output so that we can 
    target objects which we expect to understand.
    """

    @classmethod
    def annotation(cls, annotation_name, value=None):
        return partial(annotation_matcher, name=annotation_name, value=value)

    @classmethod
    def plugin(cls, plugin_name):
        return partial(match_plugin, query={"plugin": plugin_name})

    @classmethod
    def feature(cls, featureName):
        # determine if the primary model object of this output has a given feature
        # by name
        def feature_matcher(item, name):
            obj = item.get_primary_object()
            if not obj:
                return False
            return obj.feature.get(name) is not None

        return partial(feature_matcher, name=featureName)

    @classmethod
    def label(cls, paths, query):
        def label_matcher(item, paths, labels):
            for p in paths:
                # get each path from item, then see if the label
                # is in the dict and if the key matches
                label_data = prop_get(item.data, p)
                if not label_data:
                    return False
                result = filter_select(label_data, labels)
                if result:
                    return True
            return False

        return partial(label_matcher, paths=paths, labels=query)

    def match(self, outputs):
        # Take an output renderer and walk the list of items it contains yielding each
        # object matching our filters
        # Build the query
        return filter_iter(outputs, query=self.requires, predicate=filter_callables)

    def apply(self, matches):
        """This will be called with the output object of a requires match."""
        raise NotImplementedError(
            f"subclass must process list of matches and apply feature changes"
        )

    def fini(self, graph, outputs):
        # For this feature if we have outputs that match the requires
        # we should call apply
        self.apply(self.match(outputs))

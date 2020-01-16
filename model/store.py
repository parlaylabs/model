import logging
import uuid

from . import utils

log = logging.getLogger("store")
_marker = object()


class Indexer(object):
    def __init__(self):
        self.indexes = []

    def __call__(self, state):
        # return a new mapping of indexKey: object or [objects]
        return {}

    def __delitem__(self, object):
        pass


def shallowmerge(dest, src):
    for k, v in src.items():
        if isinstance(v, dict):
            n = dest.setdefault(k, v.__class__())
            shallowmerge(n, v)
        else:
            dest[k] = v
    return dest


class ExtendingIndexer(Indexer):
    """Add to the base object (the store) based on properties defined here
    """

    def __init__(self, *props, normalize=None):
        self.props = props
        self.store = utils.AttrAccess()
        self.normalizer = normalize

    def __call__(self, item):
        def _getter(o, k, default=_marker):
            try:
                return getattr(o, k)
            except AttributeError:
                try:
                    return o[k]
                except KeyError:
                    return default

        o = self.store
        parts = list(self.props)[:]
        key = parts.pop()
        if parts:
            for p in parts:
                n = _getter(item, p)
                if self.normalizer:
                    n = self.normalizer(n)
                o = o.setdefault(n, utils.AttrAccess())
        o[_getter(item, key)] = item

    def __getattr__(self, key):
        if self.normalizer:
            key = self.normalizer(key)
        return self.store[key]

    def get(self, key, default=None):
        return self.store.get(key, default)


PropertyIndexer = ExtendingIndexer("kind", "name", normalize=str.lower)


class Store:
    """Datastore, maintains index over objects and fires events
    on _index_ mutation. Objects themselves are only mutated by replacement.

    To index an object it must have (in order)

    a callable Id method returning a string ID
    a string Id attribute
    a self['Id'] field
    """

    def __init__(self, *indexers):
        self.__state = set()
        self.__indexers = {}
        if not indexers:
            indexers = [PropertyIndexer]
        for indexer in indexers:
            self.addIndexer(indexer)

    @property
    def state(self):
        return self.__state

    def __len__(self):
        return len(self.__state)

    def __iter__(self):
        return iter(self.__state)

    @property
    def indexers(self):
        return tuple(self.__indexers.values())

    def __getattr__(self, indexName):
        for index in self.__indexers.values():
            o = getattr(index, indexName, _marker)
            if o is not _marker:
                return o
        raise AttributeError(indexName)

    def addIndexer(self, indexer):
        """Indexer should produce one or more dict like index objects"""
        uid = uuid.uuid4()
        self.__indexers[uid] = indexer
        return uid

    def removeIndexer(self, uid):
        self.__indexers.pop(uid, None)

    def __contains__(self, item):
        return item in self.__state

    def add(self, entity):
        self.__state.add(entity)
        for indexer in self.__indexers.values():
            indexer(entity)


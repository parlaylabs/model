import logging
import uuid


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


class AttributeIndexer(Indexer):
    def __init__(self, indexes):
        self.indexes = indexes
        self.__indexes = {}
        for i in self.indexes:
            if not isinstance(i, list):
                i = [i]
            self.__indexes.setdefault(i[0], {})

    def __repr__(self):
        return "<AttributeIndexer {}>".format(self.indexes)

    def __getattr__(self, indexName):
        try:
            return self.__indexes[indexName]
        except KeyError:
            raise AttributeError(indexName)

    def __call__(self, entity):
        for index in self.indexes:
            mapping = self.__indexes
            v = _marker
            if isinstance(index, str):
                index = [index]
            if not isinstance(index, list):
                raise ValueError(f"unexpected index type {index}")

            if len(index) > 1:
                for k in index:
                    v = getattr(entity, k)
                    mapping = mapping.setdefault(k, {})
                    mapping = mapping.setdefault(v, {})
            # for the last item in the index list we populate entity
            k = index[-1]
            v = getattr(entity, k)
            mapping = mapping.setdefault(k, {})
            mapping[v] = entity


EntityIndexer = AttributeIndexer(["qual_name", ["kind", "name"], ["name", "kind"]])


class Store:
    """Datastore, maintains index over objects and fires events
    on _index_ mutation. Objects themselves are only mutated by replacement.

    To index an object it must have (in order)

    a callable Id method returning a string ID
    a string Id attribute
    a self['Id'] field
    """

    def __init__(self, *indexers):
        self.__state = dict()
        self.__indexers = {}
        if not indexers:
            indexers = [EntityIndexer]
        for indexer in indexers:
            self.addIndexer(indexer)

    @property
    def state(self):
        return self.__state

    @property
    def indexers(self):
        return tuple(self.__indexers.values())

    def __getitem__(self, indexName):
        for index in self.__indexers.values():
            o = getattr(index, indexName, _marker)
            if o is not _marker:
                return o
        raise AttributeError(indexName)

    __getattr__ = __getitem__

    def addIndexer(self, indexer):
        """Indexer should produce one or more dict like index objects"""
        uid = uuid.uuid4()
        self.__indexers[uid] = indexer
        return uid

    def removeIndexer(self, uid):
        self.__indexers.pop(uid, None)

    def getId(self, entity):
        try:
            name = entity["name"]
            kind = entity["kind"]
        except (KeyError, TypeError):
            try:
                name = entity.name
                kind = entity.kind
            except AttributeError:
                raise ValueError(f"{entity} must have name and kind properties")
        return f"{kind}:{name}"

    def add(self, entity):
        eid = self.getId(entity)
        self.__state[eid] = entity

        for indexer in self.__indexers.values():
            indexer(entity)

    def __delitem__(self, entity):
        eid = self.getId(entity)
        e = self.__state.pop(eid, None)
        if not e:
            return
        for indexer in self.__indexers.values():
            del indexer[entity]

    def upsert(self, *entities):
        for entity in entities:
            self.add(entity)

    def serialized(self):
        return list(self.__state.values())


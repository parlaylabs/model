import pytest

from model import utils
import jsonmerge

a = dict(
    this="that",
    nest=dict(a=1, b=2, c=3),
    lst=[1, 2, 3],
    d=[{"name": "foo", "val": 9}, {"name": "bar", "val": 11}],
)

b = dict(
    flubber="blubber",
    nest=dict(a=99, b=2),
    lst=[3, 4, 4],
    d=[{"name": "alpha", "val": 1}, {"name": "bar", "val": 1}],
)


def test_merge_dict():
    # utils.deepmerge(a, b)
    # x = always_merger.merge(a, b)
    schema = {
        "properties": {
            "d": {
                "mergeStrategy": "arrayMergeById",
                "mergeOptions": {"idRef": "name"},
            },
            "lst": {"mergeStrategy": "append"},
        }
    }
    merger = jsonmerge.Merger(schema)
    x = merger.merge(a, b)
    assert x == {
        "d": [
            {"name": "foo", "val": 9},
            {"name": "bar", "val": 1},
            {"name": "alpha", "val": 1},
        ],
        "flubber": "blubber",
        "lst": [1, 2, 3, 3, 4, 4],
        "nest": {"a": 99, "b": 2, "c": 3},
        "this": "that",
    }

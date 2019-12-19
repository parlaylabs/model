import pytest

from model import render
from model import runtime


def test_pick_value():
    r = render.FileRenderer("-")
    r.add("01", dict(this="test"), runtime.Kubernetes(), foo="bar")
    r.add("02", dict(this="another"), runtime.Kubernetes(), foo="baz")
    result = r.pick(query={"data.this": "test"})
    assert list(result)[0].annotations["foo"] == "bar"


def test_pick_plugin():
    r = render.FileRenderer("-")
    r.add("01", dict(this="test"), runtime.Kubernetes(), foo="bar")
    r.add("02", dict(this="another"), runtime.Kubernetes(), foo="baz")
    result = r.pick(plugin=runtime.Kubernetes())
    assert len(list(result)) == 2

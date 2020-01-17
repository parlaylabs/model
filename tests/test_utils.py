from model import utils

filter_data = [
    dict(a=1, name="foo", this="that"),
    dict(a=2, name="bar", this="something"),
    dict(a=3, name="baz", this="in the"),
    dict(a=4, name="foo", this="night"),
]


def test_filter_iter_take():
    r = list(utils.filter_iter(filter_data, name="foo"))
    assert r[0] == filter_data[0]
    assert r[1] == filter_data[3]


def test_filter_iter_drop():
    r = list(utils.filter_iter(filter_data, reversed=True, name="foo"))
    assert r[0] == filter_data[1]
    assert r[1] == filter_data[2]


def test_merge_path():
    sample = {
        "this": {"that": {"foo": "bar", "baz": "whatever"}, "high": "low"},
        "one": 2,
    }
    utils.merge_path(sample, "this.that", dict(foo="fofofo", new="true"))

    r = utils.nested_get(sample, "this.that")
    assert r["foo"] == "fofofo"
    assert r["baz"] == "whatever"
    assert r["new"] == "true"


def test_merge_paths():
    sample = {
        "this": {"that": {"foo": "bar", "baz": "whatever"}, "high": "low"},
        "one": 2,
    }
    utils.merge_paths(
        sample, {"this.that": dict(foo="fofofo", new="true"), "this.high": "low"}
    )

    r = utils.nested_get(sample, "this.that")
    assert r["foo"] == "fofofo"
    assert r["baz"] == "whatever"
    assert r["new"] == "true"
    assert utils.nested_get(sample, "this.high") == "low"


def test_merging_chainmap_simple():
    m = utils.MergingChainMap()
    m.update(dict(this="test", a=1))
    assert m["this"] == "test"
    m.new_child()
    m.update(dict(this="that", c=3))
    assert m["this"] == "that"
    assert m["a"] == 1
    assert m["c"] == 3
    m.remove_child()
    assert m["this"] == "test"


def test_fake_fstring():
    data = utils.AttrAccess(dict(obj=utils.AttrAccess({"name": "foo"})))
    assert utils.fstring("{obj.name} and {40 + 2}", data) == "foo and 42"


def test_uri_relative():
    assert (
        utils.uri_relative("http://github.com/bcsaller/myapp/component.yaml")
        == "http://github.com/bcsaller/myapp"
    )
    assert (
        utils.uri_relative(
            "http://github.com/bcsaller/myapp/component.yaml", "README.md"
        )
        == "http://github.com/bcsaller/myapp/README.md"
    )


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
    r = list(utils.filter_iter(filter_data, True, name="foo"))
    assert r[0] == filter_data[1]
    assert r[1] == filter_data[2]

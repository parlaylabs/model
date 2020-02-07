import pytest

from model import entity
from model import schema
from model import store as store_impl


@pytest.fixture
def store():
    return store_impl.Store()


def test_facets(store):
    base = "tests/assets/entity-01"
    schema.load_config(store, base)
    e = store.environment.dev.entity
    assert e.serialized() == {
        "config": {
            "public_dns": "mystery.info",
            "services": {
                "ghost": {
                    "environment": [
                        {"name": "url", "value": "http://ghost.mystery.info"}
                    ]
                }
            },
        },
        "kind": "Environment",
        "name": "dev",
    }

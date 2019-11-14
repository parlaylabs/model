from model import entity
from model import schema

from jsonschema.exceptions import ValidationError
import pytest

exa_schema = schema.Schema(
    {
        "properties": {
            "name": {"type": "string"},
            "payload": {"type": "string"},
            "replicas": {"type": "integer", "default": 3},
        }
    }
)


def test_validate():
    exa_schema.validate(dict(name="test", payload="something:latest", replicas=1))

    with pytest.raises(ValidationError) as e:
        exa_schema.validate(
            dict(name="test", payload="something:latest", replicas="wrong")
        )


def test_validate_entity():
    data = dict(name="test", payload="something:latest", replicas=1)
    e = entity.Entity(data, schema=exa_schema, src_ref="<test>")
    e.validate()


def test_from_schema():
    data = dict(name="test", payload="something:latest")
    e = entity.Entity.from_schema(data, exa_schema, "<test")
    e.validate()
    assert e["replicas"] == 3


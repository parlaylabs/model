import model.entity as E
import model.store as S
from model import schema
from model import utils

import pytest
import yaml


def test_store_basic_example():
    s = S.Store()
    schema.load_and_store(open("examples/basic/components/ghost.yaml"), s)
    schema.load_and_store(open("examples/basic/components/mysql.yaml"), s)
    assert s.component.ghost


def test_extending_index():
    idx = S.ExtendingIndexer("kind", "name", normalize=str.lower)
    g = yaml.load(open("examples/basic/components/ghost.yaml"), Loader=yaml.SafeLoader)
    idx(g)
    assert idx.component.ghost.name == "ghost"


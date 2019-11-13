import model.entity as E
import model.store as S
from model import schema

import pytest


def test_store_basic_example():

    s = S.Store()
    schema.load_and_store(open("examples/basic/components/ghost.yaml"), s)
    schema.load_and_store(open("examples/basic/components/mysql.yaml"), s)
    assert s.qual_name["Component:ghost"]


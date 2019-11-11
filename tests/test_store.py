import pulp.entity as E
import pulp.store as S

import pytest


def test_store_basic_example():
     g = E.Entity.from_yaml_file(open("examples/basic/components/ghost.yaml"))
     m = E.Entity.from_yaml_file(open("examples/basic/components/mysql.yaml"))
     s = S.Store()
     s.add(g)
     s.add(m)
     breakpoint()

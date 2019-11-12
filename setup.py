#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import configparser
from pathlib import Path  # noqa

README = Path("README.md")
if README.exists():
    long_description = README.read_text(encoding="utf-8")


def _pipfile(fn="Pipfile"):
    p = Path(fn)
    cfg = configparser.ConfigParser()
    cfg.read_file(p.open())
    pkgs = list(cfg["packages"].keys())
    return pkgs


install_requires = _pipfile()

setup(
    packages=find_packages(exclude=["ez_setup", "tests"]),
    package_data={"model": ["py.typed"]},
    include_package_data=True,
    python_requires=">=3.7.0",
    keywords=[],
    zip_safe=False,
    install_requires=install_requires,
    long_description=long_description,
    entry_points={"console_scripts": ["model = model.cli.main:main"]},
)

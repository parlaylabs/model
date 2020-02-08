from model import docker

docker_tags = [
    "registry/image-name",
    "image-name",
    "registry/org/image-name:version",
    "registry/image-name:version",
]


def test_parse_tags():
    def expect(s, result):
        m = docker.parse_docker_tag(s)
        if not m:
            print(f"Unable to parse {s}")
        assert m == result

    expect(
        "registry.example.com/org/image-name",
        dict(
            domain="registry.example.com", org="org", image="image-name", version=None
        ),
    )

    expect(
        "registry.example.com/org/image-name:version",
        dict(
            domain="registry.example.com",
            org="org",
            image="image-name",
            version="version",
        ),
    )

    expect("image-name", dict(domain=None, org=None, image="image-name", version=None))

    expect(
        "image-name:version",
        dict(domain=None, org=None, image="image-name", version="version"),
    )

    expect(
        "registry/org/image-name",
        dict(domain="registry", org="org", image="image-name", version=None),
    )

    expect(
        "registry/org/image-name:version",
        dict(domain="registry", org="org", image="image-name", version="version"),
    )

    expect(
        "registry/image-name:version",
        dict(domain="registry", org=None, image="image-name", version="version"),
    )

    expect(
        "registry/image-name",
        dict(domain="registry", org=None, image="image-name", version=None),
    )

    expect(
        "docker.fatline.io/analytics:v2.87.0-50-gfb5f8fa-SNAPSHOT",
        dict(
            domain="docker.fatline.io",
            org=None,
            image="analytics",
            version="v2.87.0-50-gfb5f8fa-SNAPSHOT",
        ),
    )


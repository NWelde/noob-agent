import noob_agent


def test_package_exposes_project_version() -> None:
    assert noob_agent.__version__ == "0.1.0"

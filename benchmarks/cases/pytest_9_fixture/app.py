import pytest


@pytest.fixture
def client():
    return object()


@pytest.fixture(scope="function")
def session():
    return object()


@pytest.fixture("module")  # BREAK
def database():
    return object()

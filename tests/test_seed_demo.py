import pytest

from app.config import Settings
from scripts.seed_demo import check_safe_to_seed


def test_allows_local_and_docker_dev():
    check_safe_to_seed(Settings(public_base_url="http://localhost:8000"), argv=[])


def test_refuses_production_unless_forced():
    prod = Settings(public_base_url="https://price.example.my")
    with pytest.raises(SystemExit):
        check_safe_to_seed(prod, argv=[])
    check_safe_to_seed(prod, argv=["--force"])

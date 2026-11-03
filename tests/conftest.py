import pytest

from support.maps import PROFILE, cloud_payload
from tuya_lds_vacuum_map.profiles import Profile


@pytest.fixture
def profile() -> Profile:
    return PROFILE


@pytest.fixture
def payload() -> dict:
    return cloud_payload()

import os

import pytest
from hypothesis import settings

from support.maps import PROFILE, cloud_payload
from tuya_lds_vacuum_map.profiles import Profile

settings.register_profile("default", deadline=None)
settings.register_profile("mutation", deadline=None, derandomize=True, database=None)
settings.load_profile("mutation" if "MUTANT_UNDER_TEST" in os.environ else "default")


@pytest.fixture
def profile() -> Profile:
    return PROFILE


@pytest.fixture
def payload() -> dict:
    return cloud_payload()

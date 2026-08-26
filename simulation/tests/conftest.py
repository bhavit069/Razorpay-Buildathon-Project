import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = os.environ.get("RECLAIMIFY_DATA", "data300k")


def _data_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for d in (DATA, "data300k", "data"):
        p = os.path.join(root, d)
        if os.path.exists(os.path.join(p, "appeal_queue.csv")):
            return p
    pytest.skip("no generated dataset; run `python run.py data300k`")


@pytest.fixture(scope="session")
def data_dir():
    return _data_dir()


@pytest.fixture(scope="session")
def store(data_dir):
    from core.feature_store import FeatureStore
    return FeatureStore.load(data_dir)


@pytest.fixture(scope="session")
def vault(data_dir):
    from core.truth import TruthVault
    return TruthVault(data_dir)


@pytest.fixture(scope="session")
def model(store, vault):
    from core.model import Adjudicator
    return Adjudicator().fit(store, vault)

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


@pytest.fixture(scope="session")
def ledger(store, model):
    """The shipped decision over the holdout, replayed once and shared. Session
    scoped because fitting and replaying costs a couple of seconds and every
    recovery test wants the same one."""
    from core.backtest import run as backtest_run
    from core.policy import PolicyConfig
    return backtest_run(store, model, PolicyConfig(cap=0.20), split="holdout")


def _lightgbm_error():
    """None if the model stack loads. LightGBM ships a native library and this
    machine's Application Control policy can refuse it; when that happens every
    test touching the model fails with the same OSError, which buries the ones
    that still say something."""
    try:
        import lightgbm  # noqa: F401
    except Exception as e:
        return f"lightgbm unavailable: {type(e).__name__}: {str(e)[:70]}"
    return None


LGB_ERROR = _lightgbm_error()
# these import the model at module scope, so they have to be skipped before
# collection rather than marked afterwards
_NEEDS_MODEL = {"test_agent.py", "test_leakage.py", "test_policy.py",
                "test_replay_determinism.py"}


@pytest.fixture
def needs_model():
    """Skip when the model stack will not load. pytest.importorskip is no help
    here: lightgbm raises OSError from its native loader, not ImportError."""
    if LGB_ERROR:
        pytest.skip(LGB_ERROR)


def pytest_ignore_collect(collection_path, config):
    if LGB_ERROR and collection_path.name in _NEEDS_MODEL:
        return True
    return None


def pytest_collection_modifyitems(config, items):
    if not LGB_ERROR:
        return
    # data_dir only locates the dataset, so tests that take just that still run
    needs = {"store", "vault", "model", "ledger"}
    mark = pytest.mark.skip(reason=LGB_ERROR)
    for item in items:
        if needs & set(getattr(item, "fixturenames", ())):
            item.add_marker(mark)


def pytest_report_header(config):
    if LGB_ERROR:
        return ("NOTE: " + LGB_ERROR
                + "; model-dependent tests are skipped, the rest still run")
    return None

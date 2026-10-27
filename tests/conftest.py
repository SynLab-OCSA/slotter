import os
import sys

import pytest

# A fixed configuration (tests/config/), and no user file: the developer's environment and
# ~/.config/slotter.toml must not change the results. This runs before
# src.config is imported.
# The developer's own configuration, checked by test_every_definition_is_valid
LOCAL_SLOTTER_DIR = os.environ.get('SLOTTER_DIR')
os.environ['SLOTTER_DIR'] = os.path.join(os.path.dirname(__file__), 'config')
os.environ.pop('ROUTINE_CLUSTER', None)
os.environ['SLOTTER_USER_CONFIG'] = os.path.join(os.path.dirname(__file__), 'no-user-config.toml')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from src.config import CONFIG, USER_CONFIG  # noqa: E402

# Modulefile tree used by the tests: hidden files are templates, not modules
MODULE_TREE = [
    'core/openmpi/4.1.8', 'core/openmpi/.version',
    'apps/orca/5.0.4', 'apps/orca/6.1.1', 'apps/orca/.common.tcl', 'apps/orca/.version',
    'apps/gaussian/16A03', 'apps/gaussian/.common.tcl',
    'apps/crest/3.0.2', 'apps/crest/.common.tcl',
    'apps/xtb/6.7.1', 'apps/openqp/1.0', 'apps/openmolcas/24.02',
    'envs/conda/grimmelab', 'envs/conda/nwchem', 'envs/conda/.conda_env.tcl',
    'group/enan/1.1', 'group/rot8/0.9',
]


@pytest.fixture
def modulepath(tmp_path, monkeypatch):
    root = tmp_path / 'modules'
    for f in MODULE_TREE:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text('#%Module1.0\n')
    dirs = [str(root / d) for d in ('core', 'apps', 'envs', 'group')]
    monkeypatch.setenv('MODULEPATH', ':'.join(dirs))
    monkeypatch.setitem(CONFIG['modules'], 'modulepath', [])
    return root


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    d = tmp_path / 'calc'
    d.mkdir()
    monkeypatch.chdir(d)
    return d


@pytest.fixture(autouse=True)
def cluster_config(monkeypatch):
    # Values the tests rely on, whatever slotter.toml says
    monkeypatch.setitem(CONFIG['cluster'], 'max_cpu', 16)
    monkeypatch.setitem(CONFIG['cluster'], 'python', '/usr/bin/python3')
    monkeypatch.setitem(CONFIG['modules'], 'purge', True)
    monkeypatch.setitem(USER_CONFIG, 'readme', {'parse_input': True, 'parse_output': True})
    monkeypatch.setitem(USER_CONFIG, 'job', {'check_folder': True, 'qm_all': True})

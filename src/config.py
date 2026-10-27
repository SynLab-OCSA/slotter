"""Slotter configuration, split in two levels.

Cluster ($SLOTTER_DIR/setup.toml): paths and every setting that does not
depend on the user (Python, scratch, CPU limit, #SBATCH defaults, SMTP,
modules). The programs are defined by $SLOTTER_DIR/calculators/*.toml
(src/calculators.py). See src/bootstrap.py.

User (~/.config/slotter.toml, or $SLOTTER_USER_CONFIG): only personal
preferences, i.e. the [readme] and [job] sections. Anything else is ignored,
so a user cannot change the cluster settings.
"""

import copy
import os
import sys
import tomllib

# The folder is chosen in bootstrap.py, shared with the scripts' start-up
from src.bootstrap import REPO_DIR, SETUP, CALCULATORS, slotter_dir  # noqa: F401

CLUSTER_DEFAULTS = {
    'cluster': {
        'python': sys.executable,
        'scratch': '/scratch',
        'max_cpu': 16,
        'sbatchrc': '~/.sbatchrc',
    },
    'slurm': {
        'nodes': '1-1',
        'error': 'err-%j-%N.slurm',
        'output': 'out-%j-%N.slurm',
    },
    'email': {
        'smtp_server': '',
        'sender': 'slurm@localhost',
    },
    'modules': {
        'modulepath': [],
        'purge': True,
        'interactive': [],
    },
}

USER_DEFAULTS = {
    'readme': {
        # Describe the input (commands, kind of calculation) in README.md
        'parse_input': True,
        # At the end of the job, write in README.md whether the calculation ended well
        'parse_output': True,
    },
    'job': {
        # Stop if the folder already holds .out or .log files (src/check.py)
        'check_folder': True,
        # At the end of the job, move the files to qm_all/ except calculators/<name>.toml keep
        'qm_all': True,
        # Job array of inputs in one folder: keep them there instead of a folder for each (src/job_array.py)
        'same_folder': False,
    },
}


def _merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _read(path):
    with open(path, 'rb') as f:
        return tomllib.load(f)


def load_cluster_config(path):
    return _merge(copy.deepcopy(CLUSTER_DEFAULTS), _read(path))


def user_config_file():
    return os.environ.get('SLOTTER_USER_CONFIG', os.path.expanduser('~/.config/slotter.toml'))


def load_user_config(path):
    config = copy.deepcopy(USER_DEFAULTS)
    if not os.path.isfile(path):
        return config
    data = _read(path)
    ignored = sorted(set(data) - set(USER_DEFAULTS))
    if ignored:
        print(f'WARNING: {path}: [{"], [".join(ignored)}] ignored, only [{"] and [".join(USER_DEFAULTS)}] '
              'can be set by the user; cluster settings are in $SLOTTER_DIR/setup.toml', file=sys.stderr)
    return _merge(config, {k: v for k, v in data.items() if k in USER_DEFAULTS})


def user_choice(section, key, calculation):
    """User choice for `key` of `section`, optionally per calculator ([job.orca] wins over [job])."""
    values = USER_CONFIG[section]
    specific = values.get(calculation)
    if isinstance(specific, dict) and key in specific:
        return bool(specific[key])
    return bool(values.get(key, USER_DEFAULTS[section][key]))


def readme_parsing(calculation, which):
    """User choice for 'parse_input' / 'parse_output', optionally per calculator ([readme.orca])."""
    return user_choice('readme', which, calculation)


# $SLOTTER_DIR
CONFIG_DIR = slotter_dir()
SETUP_FILE = os.path.join(CONFIG_DIR, SETUP)
CONFIG = load_cluster_config(SETUP_FILE)
USER_CONFIG = load_user_config(user_config_file())

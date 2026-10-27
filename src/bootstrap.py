"""Configuration folder ($SLOTTER_DIR), and re-run of the scripts with its Python.

SLOTTER_DIR is the folder of the configuration, anywhere:

    $SLOTTER_DIR/setup.toml         settings (paths, Python, SLURM, email, modules)
    $SLOTTER_DIR/calculators/*.toml one file per program: orca.toml -> job.py orca

The scripts start with "#!/usr/bin/env python3", which may be a system Python
without what slotter needs (tomllib, InquirerPy, ...). Before importing
any of it, use_cluster_python() reads "python" in the [cluster] section of
setup.toml and, if it is another interpreter, runs the script again with it.

This module only uses the standard library and works on Python 3.6+, so it
reads the TOML with a minimal parser: the python value must be a plain
string on one line, as in clusters/setup.example.toml.
"""

import os
import re
import sys

# Folder of slotter itself (job.py, src/, ...), not to be confused with $SLOTTER_DIR
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = 'setup.toml'
CALCULATORS = 'calculators'

# Set before re-running, so that the new process does not re-run again
REEXEC_GUARD = 'SLOTTER_PYTHON_REEXEC'


def _legacy_dir():
    """Configuration of the jobs written before SLOTTER_DIR existed.

    They pass ROUTINE_CLUSTER=<repository>/clusters/<name>.toml to the scripts
    run at the end of the job: <repository>/clusters/<name>/ is used, if it
    holds a setup.toml. Remove once no such job is left.
    """
    old = os.environ.get('ROUTINE_CLUSTER')
    if not old:
        return None
    name = os.path.splitext(os.path.basename(old.rstrip('/')))[0]
    path = os.path.join(REPO_DIR, 'clusters', name)
    return path if os.path.isfile(os.path.join(path, SETUP)) else None


def slotter_dir(path=None):
    """The configuration folder: `path`, or $SLOTTER_DIR."""
    path = path or os.environ.get('SLOTTER_DIR') or _legacy_dir()
    if not path:
        raise SystemExit('ERROR: set SLOTTER_DIR to the folder with {} and {}/ '
                         '(templates in {})'.format(SETUP, CALCULATORS, os.path.join(REPO_DIR, 'clusters')))
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(os.path.join(path, SETUP)):
        raise SystemExit('ERROR: SLOTTER_DIR={} has no {} (template: {})'.format(
            path, SETUP, os.path.join(REPO_DIR, 'clusters', 'setup.example.toml')))
    return path


def setup_file(path=None):
    return os.path.join(slotter_dir(path), SETUP)


def cluster_python(path):
    """The "python" key of the [cluster] section of a setup.toml, or None."""
    section = None
    with open(path) as f:
        for line in f:
            header = re.match(r'\s*\[([^\]]+)\]', line)
            if header:
                section = header.group(1).strip()
                continue
            if section == 'cluster':
                value = re.match(r'''\s*python\s*=\s*(["'])(.*?)\1''', line)
                if value:
                    return value.group(2)
    return None


def use_cluster_python():
    """Run the current script again with the cluster Python, if it is another one.

    Nothing happens if the configured Python does not exist here (e.g. on a
    laptop), if it is the one already running, or after a re-run.
    """
    if os.environ.get(REEXEC_GUARD):
        return
    path = setup_file()
    python = cluster_python(path)
    if not python or not os.access(python, os.X_OK):
        if sys.version_info < (3, 11):
            raise SystemExit('ERROR: the Python of {} ({}) is not available here, and {} is '
                             'Python {}.{}: slotter needs 3.11 or later'.format(
                                 path, python, sys.executable, *sys.version_info[:2]))
        return
    if os.path.realpath(python) == os.path.realpath(sys.executable):
        return
    os.environ[REEXEC_GUARD] = '1'
    os.execv(python, [python, os.path.abspath(sys.argv[0])] + sys.argv[1:])

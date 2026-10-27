"""Environment Modules discovery through MODULEPATH.

Works with any layout of the modulefile tree (flat, or split in core/, apps/,
envs/, ...): a module is found by name in any MODULEPATH directory. Hidden
files (.version, .common.tcl, ...) are never reported as modules.
"""

import os
import re

from src.config import CONFIG


def modulepath():
    """Existing MODULEPATH directories (from the config, or $MODULEPATH)."""
    dirs = CONFIG['modules']['modulepath'] or os.environ.get('MODULEPATH', '').split(':')
    return [d for d in dirs if d and os.path.isdir(d)]


def _version_key(version):
    # Natural order: 6.10.0 after 6.9.1, 16C02 after 16A03
    return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', version)]


def versions(name):
    """Available versions of a module, e.g. versions('orca') -> ['5.0.4', '6.1.1']."""
    found = set()
    for d in modulepath():
        path = os.path.join(d, name)
        if os.path.isdir(path):
            found.update(v for v in os.listdir(path)
                         if not v.startswith('.') and os.path.isfile(os.path.join(path, v)))
    return sorted(found, key=_version_key)


def exists(name):
    """True if `name` is a module: 'orca', 'orca/6.1.1' or 'conda/grimmelab'."""
    for d in modulepath():
        path = os.path.join(d, name)
        if os.path.isfile(path) and not os.path.basename(path).startswith('.'):
            return True
    return bool(versions(name))


def available():
    """All modules as name/version, e.g. for the interactive menu."""
    found = set()
    for d in modulepath():
        for root, dirs, files in os.walk(d):
            dirs[:] = [i for i in dirs if not i.startswith('.')]
            for f in files:
                if not f.startswith('.'):
                    found.add(os.path.relpath(os.path.join(root, f), d))
    return sorted(found, key=_version_key)


def resolve(names, version=None):
    """Modules to load, with `version` applied to the last one.

    Raises ValueError if a module (or the requested version) does not exist.
    The check is skipped when no MODULEPATH is known, e.g. outside the cluster.
    """
    names = list(names)
    if version:
        if not names:
            raise ValueError('version= given, but no module to apply it to')
        names[-1] = f'{names[-1]}/{version}'

    if not modulepath():
        return names

    for i, name in enumerate(names):
        if exists(name):
            continue
        if version and i == len(names) - 1:
            base = name.rsplit('/', 1)[0]
            raise ValueError(f'Required version of {base} ({version}) is missing. '
                             f'Available: {", ".join(versions(base)) or "none"}')
        raise ValueError(f'Module {name} not found in MODULEPATH ({":".join(modulepath())})')
    return names

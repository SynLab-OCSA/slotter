"""The programs job.py can run, each defined by $SLOTTER_DIR/calculators/<name>.toml.

<name>.toml defines "job.py <name>". A definition is plain data, the keys are
described in clusters/README.md: this module turns it into CPU and memory for
SLURM, the commands of the job, the shell test of the outcome and the
description of the input in README.md.
"""

import glob
import math
import os
import re
import shlex
import tomllib
from collections.abc import Mapping

from src.config import CONFIG, CONFIG_DIR, CALCULATORS
from assets.md_templates import default_template

KEYS = {'abbreviation', 'requires', 'module', 'output', 'keep', 'command', 'pre', 'post',
        'success', 'failure', 'cpu', 'memory', 'readme'}
TABLE_KEYS = {
    'cpu': {'option', 'consume', 'input', 'limit', 'default'},
    'memory': {'input', 'unit', 'default', 'per_cpu'},
    'readme': {'command', 'describe'},
}
RULE_KEYS = {'match', 'text', 'group', 'source'}

# Memory units in MB: a word is 8 bytes
UNITS = {'b': 1e-6, 'kb': 1e-3, 'mb': 1, 'gb': 1e3, 'tb': 1e6,
         'w': 8e-6, 'kw': 8e-3, 'mw': 8, 'gw': 8e3, 'tw': 8e6}

# Only these are replaced, so that the shell ${...} of pre/post stays as it is
PLACEHOLDER = re.compile(r'\{(input|stem|args|output|cpu|mem)\}')


def _as_list(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _fill(template, values):
    return PLACEHOLDER.sub(lambda m: str(values[m[1]]), template)


def _reader(path):
    """Function returning the text of `path`, read once and only if needed."""
    text = []
    def read():
        if not text:
            with open(path) as f:
                text.append(f.read())
        return text[0]
    return read


def check_definition(name, spec):
    """Raise ValueError for unknown or missing keys, e.g. a typo in a TOML file."""
    if 'modules' in spec:
        raise ValueError(f'calculator {name}: "modules" is replaced by "requires" (loaded first) '
                         'and "module" (the program, takes version=)')
    unknown = {k for k in spec if k not in KEYS}
    for table, allowed in TABLE_KEYS.items():
        unknown |= {f'{table}.{k}' for k in spec.get(table, {}) if k not in allowed}
    for rule in spec.get('readme', {}).get('describe', []):
        unknown |= {f'readme.describe.{k}' for k in rule if k not in RULE_KEYS}
        if not {'match', 'text'} <= set(rule):
            raise ValueError(f'calculator {name}: every readme.describe rule needs "match" and "text"')
    if unknown:
        raise ValueError(f'calculator {name}: unknown keys {", ".join(sorted(unknown))}')
    if name != 'run' and not spec.get('command'):
        raise ValueError(f'calculator {name}: "command" is missing')


class Calculator:
    md_template = default_template

    def __init__(self, name, spec):
        check_definition(name, spec)
        self.name = name
        self.abbreviation = spec.get('abbreviation', name)
        # Modules loaded first, as they are written (e.g. conda/grimmelab, openmpi/4.1.8)
        self.requires = tuple(spec.get('requires', ()))
        # Module of the program: version=... on the command line applies to it
        self.module = spec.get('module')
        self.output = spec.get('output', '')
        self.keep = list(spec.get('keep', ()))
        self.command = spec.get('command', '')
        self.pre = spec.get('pre', '').strip()
        self.post = spec.get('post', '').strip()
        self.success = _as_list(spec.get('success'))
        self.failure = _as_list(spec.get('failure'))
        self.cpu = spec.get('cpu', {})
        self.memory = spec.get('memory', {})
        self.readme = spec.get('readme', {})

    @property
    def modules(self):
        """Modules to load: the program last, so that it wins in PATH and
        resolve() applies the version to it."""
        return self.requires + ((self.module,) if self.module else ())

    def output_file(self, input_file):
        return _fill(self.output, {'stem': os.path.splitext(input_file)[0], 'input': input_file})

    def _cpu_option(self, words):
        """Value of the CPU option among `words`, and `words` without it."""
        names = _as_list(self.cpu.get('option'))
        # "-T=8" never gets here: arguments with "=" are for slotter (parser_input.py)
        for i, word in enumerate(words):
            if word not in names:
                continue
            if i + 1 < len(words):
                return words[i + 1], words[:i] + words[i + 2:]
            raise ValueError(f'{word} needs the number of CPUs')
        return None, words

    def arguments(self, calc_cmd):
        """Options after the input file ("<program> <input> <options>"), without
        the CPU option when it is for slotter only (cpu.consume)."""
        words = calc_cmd.split()[2:]
        if self.cpu.get('consume'):
            words = self._cpu_option(words)[1]
        return ' '.join(words)

    def get_cpu_memory(self, input_file, calc_commands, force):
        read = _reader(input_file)

        value = self._cpu_option(calc_commands.split()[2:])[0]
        if value is not None:
            if not value.isdigit():
                raise ValueError(f'{self.name}: the number of CPUs must be a number, not "{value}"')
            cpu = int(value)
        else:
            cpu = self.cpu.get('default', 1)
            for pattern in _as_list(self.cpu.get('input')):
                text = read()
                match = re.search(pattern, text)
                if not match:
                    continue
                cpu = int(match[1])
                max_cpu = CONFIG['cluster']['max_cpu']
                if self.cpu.get('limit') and cpu > max_cpu and not force:
                    # Only the number changes: the rest of the input keeps its case
                    with open(input_file, 'w') as f:
                        f.write(text[:match.start(1)] + str(max_cpu) + text[match.end(1):])
                    cpu = max_cpu
                break

        mem = self.memory.get('default', 1000)
        if 'input' in self.memory:
            match = re.search(self.memory['input'], read())
            if match:
                unit = (match[2] if match.re.groups > 1 else None) or self.memory.get('unit', 'mb')
                mem = int(match[1]) * UNITS[unit.lower()]
        if self.memory.get('per_cpu'):
            mem *= cpu

        return cpu, max(1, math.ceil(mem))

    def job_commands(self, calc_cmd, input_file, cpu, mem):
        """(pre, command, post) of the job, with the placeholders filled in."""
        args = self.arguments(calc_cmd)
        values = {
            'input': shlex.quote(input_file),
            'stem': shlex.quote(os.path.splitext(input_file)[0]),
            'args': args,
            'output': shlex.quote(self.output_file(input_file)),
            'cpu': cpu,
            'mem': mem,
        }
        command = self.command if args else self.command.replace(' {args}', '')
        return tuple(_fill(t, values) for t in (self.pre, command, self.post))

    def outcome_test(self, output_file):
        """Shell condition, true if the calculation ended well.

        The success texts must all be in the output and the failure texts none;
        without success texts the exit code of the program ($STATUS) decides.
        """
        out = shlex.quote(output_file)
        tests = [f'grep -qsF -- {shlex.quote(s)} {out}' for s in self.success]
        if not self.success:
            tests.append('[[ $STATUS -eq 0 ]]')
        tests += [f'! grep -qsF -- {shlex.quote(s)} {out}' for s in self.failure]
        return ' && '.join(tests)

    def parse_input_for_readme(self, input_file, cmd_line):
        """The commands, and the numbered list of what the calculation does."""
        read = _reader(input_file)
        cmd_line = cmd_line.strip()

        pattern = self.readme.get('command')
        cmd = '\n'.join(m[0].strip() for m in re.finditer(pattern, read())) if pattern else cmd_line

        steps, groups_done = [], set()
        for rule in self.readme.get('describe', []):
            group = rule.get('group')
            # In a group only the first rule that matches is used
            if group in groups_done:
                continue
            text = cmd_line if rule.get('source') == 'command' else read()
            matches = [re.search(p, text) for p in _as_list(rule['match'])]
            if not all(matches):
                continue
            # {1}, {2}, ... in the text: the groups of the last pattern
            steps.append(re.sub(r'\{(\d)\}', lambda m: matches[-1][int(m[1])] or '', rule['text']))
            if group:
                groups_done.add(group)

        return cmd, '\n'.join(f'{idx}. {step}' for idx, step in enumerate(steps, start=1))


def read_definitions(directory=os.path.join(CONFIG_DIR, CALCULATORS)):
    """{name: definition} of the TOML files in `directory`."""
    found = {}
    for path in sorted(glob.glob(os.path.join(directory, '*.toml'))):
        with open(path, 'rb') as f:
            found[os.path.splitext(os.path.basename(path))[0]] = tomllib.load(f)
    return found


DEFINITIONS = read_definitions()

# job.py run: any command, given interactively
RUN = Calculator('run', {'abbreviation': 'r'})


class Calculators(Mapping):
    """CALCULATORS['orca']: the Calculator of orca.toml, checked when it is
    used, so that a mistake in one file does not stop the other programs."""

    def __getitem__(self, name):
        if name == 'run':
            return RUN
        return Calculator(name, DEFINITIONS[name])

    def __iter__(self):
        return iter(sorted(DEFINITIONS) + ['run'])

    def __len__(self):
        return len(DEFINITIONS) + 1


CALCULATORS = Calculators()

POSS_CALC = list(CALCULATORS)

"""Job arrays: several calculations of one program with a single sbatch.

    job.py orca conf_*/opt.inp        one task per folder: each folder must hold
                                      only that input (by extension)
    job.py orca *.inp                 each input of this folder is moved to a
                                      folder of its own, <stem>/, with the files
                                      <stem>.*; the other files are copied there
    job.py orca *.inp --same-folder   they all stay here: the files of each
                                      calculation get its name (job-slurm-<stem>.sh,
                                      README-<stem>.md, .originals-<stem>), update
                                      and live the job id; no qm_all/

Every calculation gets its job and README.md as with job.py alone, so it can
be submitted again by itself; job-array.sh runs, in task i, the job of
calculation i. SLURM gives every task the same resources: the largest CPUs and
memory among the inputs. array=%N, or [cluster] array_limit, limits the tasks
running at a time.
"""

import os
import re
import shlex
import shutil

from src.calculators import CALCULATORS
from src.config import CONFIG
from src.initiate_readme import write_md
from src.parser_input import prepare
from src.write_job_file import (convert_slurm_cmd, missing_for_shared_folder, program_modules, read_template,
                                template_file, write_job_file)

ARRAY_JOB = 'job-array.sh'
# Files of slotter and of the jobs: never copied to the folder of a calculation
OWN_FILES = re.compile(r'^(README.*\.md|job-.*\.sh|.*\.slurm|update(-.*)?|live(-.*)?)$')
# The name ends up in the job: README-<stem>.md, .originals-<stem>, ...
PLAIN_NAME = re.compile(r'^[\w.+-]+$')


def is_array(files):
    """More than one input, or one in another folder."""
    return len(files) > 1 or any(os.path.dirname(f) for f in files)


def outputs(folder):
    """Files of the folder that look like outputs, as src/check.py."""
    return sorted(f for f in os.listdir(folder)
                  if os.path.isfile(os.path.join(folder, f)) and f.endswith(('out', 'log')))


def plan(files, same_folder):
    """(folder, input, tag) of every calculation, and the inputs of this
    folder that get a folder of their own."""
    ext = os.path.splitext(files[0])[1]
    tasks, split = [], []
    for path in files:
        folder, name = os.path.split(path)
        stem = os.path.splitext(name)[0]
        if same_folder:
            if not PLAIN_NAME.match(stem):
                raise ValueError(f'{path}: with --same-folder the name can hold only letters, digits and . _ + -')
            tasks.append((folder or '.', name, f'-{stem}'))
        elif not folder:
            if os.path.exists(stem):
                raise ValueError(f'{stem} already exists: it cannot become the folder of {name}')
            split.append(name)
            tasks.append((stem, name, ''))
        else:
            others = sorted(f for f in os.listdir(folder) if f.endswith(ext) and f != name)
            if others:
                raise ValueError(f'{folder} holds other {ext} files ({", ".join(others)}): '
                                 'one input per folder, or --same-folder')
            tasks.append((folder, name, ''))
    return tasks, split


def present_outputs(calc_obj, tasks, split):
    """Outputs already there, which would stop job.py in a single folder."""
    found = [f'./{f}' for f in outputs('.')] if split else []
    for folder, name, tag in tasks:
        if tag:
            # Shared folder: only the output of this calculation counts
            output = os.path.join(folder, calc_obj.output_file(name))
            if calc_obj.output and os.path.isfile(output):
                found.append(output)
        elif name not in split:
            found += [os.path.join(folder, f) for f in outputs(folder)]
    return found


def split_folder(inputs, ext):
    """Move each input, with the files of the same name, to a folder of its own;
    copy the other files to every folder."""
    stems = [os.path.splitext(name)[0] for name in inputs]
    for stem in stems:
        os.mkdir(stem)
    for f in sorted(os.listdir('.')):
        if not os.path.isfile(f) or f.startswith('.') or OWN_FILES.match(f):
            continue
        # a.inp, a.xyz, a.gbw go to a/ (the longest name wins: a.b.inp is not of a)
        owner = max((s for s in stems if f.startswith(s + '.')), key=len, default=None)
        if owner:
            shutil.move(f, owner)
        elif not f.endswith(ext):
            # Other inputs, not in the array, stay here
            for stem in stems:
                shutil.copy2(f, stem)


def no_array_option(slurm_commands):
    """array= is for job arrays: a single job with it would be refused by sbatch."""
    if 'array' in slurm_commands:
        raise ValueError(f'array={slurm_commands["array"]} is for job arrays, with more than one input')


def array_limit(slurm_commands):
    """%N of --array: array=%N of the command line, else [cluster] array_limit."""
    limit = str(slurm_commands.get('array') or '')
    if limit:
        if not re.fullmatch(r'%\d+', limit):
            raise ValueError(f'array={limit}: only the tasks at a time can be given, e.g. array=%4')
        return limit
    default = CONFIG['cluster']['array_limit']
    if not isinstance(default, int) or isinstance(default, bool) or default < 0:
        raise ValueError(f'[cluster] array_limit = {default!r}: it must be a whole number, 0 for no limit')
    return f'%{default}' if default else ''


def array_sbatch(calc_obj, slurm_commands, resources, n_tasks):
    """#SBATCH lines of job-array.sh: the largest resources, one task per calculation."""
    sbatch = {**CONFIG['slurm'], **slurm_commands}
    # Handled by the job of each calculation
    sbatch.pop('version', None)
    sbatch.pop('mail-user', None)
    sbatch.pop('array', None)
    limit = array_limit(slurm_commands)
    sbatch['ntasks'] = max(cpu for cpu, _ in resources)
    sbatch['mem'] = max(mem for _, mem in resources)
    # %j is the job of one task: %A_%a keeps their files apart
    for key in ('output', 'error'):
        if sbatch.get(key):
            sbatch[key] = str(sbatch[key]).replace('%j', '%A_%a')
    sbatch['array'] = f'0-{n_tasks - 1}{limit}'
    name = f'{calc_obj.abbreviation}-{os.path.basename(os.getcwd())}'
    return f'#SBATCH --job-name={name}\n{convert_slurm_cmd(sbatch)}'


def write_array(calculation, files, options, slurm_commands, choices):
    """Write the job and README.md of every calculation, then job-array.sh.

    Everything is checked before a file is moved or written: ValueError if
    something is wrong.
    """
    calc_obj = CALCULATORS[calculation]
    files = list(dict.fromkeys(os.path.normpath(f) for f in files))
    same_folder = choices['same_folder']
    if same_folder and calc_obj.output and calc_obj.output_file('a.x') == calc_obj.output_file('b.x'):
        raise ValueError(f'{calculation} always writes {calc_obj.output_file("a.x")}: '
                         'the calculations need a folder each, without --same-folder')

    tasks, split = plan(files, same_folder)
    program_modules(calc_obj, slurm_commands.get('version'))
    missing = same_folder and missing_for_shared_folder(read_template())
    if missing:
        raise ValueError(f'{template_file()}: no {", ".join(missing)}, needed by --same-folder')
    if choices['check_folder']:
        found = present_outputs(calc_obj, tasks, split)
        if found:
            raise ValueError(f'output files already present ({", ".join(found)}): nothing submitted. '
                             'Use one folder for one calculation, or --no-check')
    # Before moving the inputs: the CPU limit may correct them
    prepared = [prepare(calculation, f, options, slurm_commands, choices['force']) for f in files]
    resources = [(s['ntasks'], s['mem']) for _, s in prepared]
    sbatch_lines = array_sbatch(calc_obj, slurm_commands, resources, len(tasks))

    if split:
        split_folder(split, os.path.splitext(files[0])[1])

    here = os.getcwd()
    for (folder, name, tag), (calc_cmd, sbatch) in zip(tasks, prepared):
        # Only job-array.sh is an array: each job must be submittable by itself
        sbatch = {k: v for k, v in sbatch.items() if k != 'array'}
        os.chdir(folder)
        try:
            write_job_file(name, calculation, calc_cmd, sbatch, job_file=f'job-slurm{tag}.sh', tag=tag,
                           qm_all=choices['qm_all'] and not tag)
            write_md(calculation, name, calc_cmd, readme=f'README{tag}.md')
        finally:
            os.chdir(here)

    if len(set(resources)) > 1:
        cpu, mem = max(r[0] for r in resources), max(r[1] for r in resources)
        print(f'The inputs ask for different resources: every task gets {cpu} CPUs and {mem} MB')
    jobs = ' '.join(shlex.quote(os.path.join(folder, f'job-slurm{tag}.sh')) for folder, _, tag in tasks)
    with open(ARRAY_JOB, 'w') as f:
        f.write(f'''#!/bin/bash
{sbatch_lines}

# Task i runs the job of calculation i, written as by job.py alone: each one
# can also be submitted again by itself
JOBS=({jobs})
JOB=${{JOBS[$SLURM_ARRAY_TASK_ID]}}

# The job runs as if it had been submitted from the folder of its calculation
export SLURM_SUBMIT_DIR="$SLURM_SUBMIT_DIR/$(dirname "$JOB")"
cd "$SLURM_SUBMIT_DIR" || exit 1
exec bash "$(basename "$JOB")"
''')
    for folder, name, tag in tasks:
        print(f'{os.path.join(folder, name)}: {os.path.join(folder, f"job-slurm{tag}.sh")}')
    return ARRAY_JOB

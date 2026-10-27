#!/usr/bin/env python3

import os
import re
import shlex

from src.config import CONFIG, CONFIG_DIR, REPO_DIR, readme_parsing, user_choice
from src.calculators import CALCULATORS
from src.modules import resolve

# Template of job-slurm.sh: $SLOTTER_DIR/job.sh, or the default of the repository
JOB_TEMPLATE = 'job.sh'
DEFAULT_TEMPLATE = os.path.join(REPO_DIR, 'assets', JOB_TEMPLATE)

# Only these braces are replaced, any other (e.g. ${ext[@]}) is bash
# Calculations of a job array kept in one folder (--same-folder), else empty:
# {tag} is -<stem> (.originals-<stem>), {job_tag} is -$SLURM_JOB_ID (update-<job id>, live-<job id>)
OPTIONAL = ('scratch', 'output', 'cpu', 'mem', 'command_quoted', 'tag', 'job_tag')
SHARED_FOLDER = ('tag', 'job_tag')
REQUIRED = ('sbatch', 'modules', 'pre', 'command', 'qm_all', 'outcome', 'email', 'post')
PLACEHOLDER = re.compile(r'\{(' + '|'.join(OPTIONAL + REQUIRED) + r')\}')
# The outcome needs the exit code of the calculation
STATUS = re.compile(r'^\{command\}\n\s*STATUS=\$\?\s*$', re.M)


def template_file(directory=CONFIG_DIR):
    path = os.path.join(directory, JOB_TEMPLATE)
    return path if os.path.isfile(path) else DEFAULT_TEMPLATE


def check_template(text, path):
    """Stop if the template would lose the calculation, its outcome or the email."""
    missing = [f'{{{name}}}' for name in REQUIRED if f'{{{name}}}' not in text]
    if missing:
        raise ValueError(f'{path}: missing {", ".join(missing)}')
    if not STATUS.search(text):
        raise ValueError(f'{path}: {{command}} must be alone on its line, followed by the line STATUS=$?')


def missing_for_shared_folder(text):
    """Placeholders of SHARED_FOLDER missing from the template text."""
    return [f'{{{name}}}' for name in SHARED_FOLDER if f'{{{name}}}' not in text]


def read_template(path=None):
    path = path or template_file()
    with open(path) as f:
        text = f.read()
    check_template(text, path)
    return text


def convert_slurm_cmd(slurm_cmd):
    # An option without value (e.g. exclusive) becomes "#SBATCH --exclusive"
    return '\n'.join(f'#SBATCH --{k}' if v is None else f'#SBATCH --{k}={v}' for k, v in slurm_cmd.items())

def module_commands(modules):
    """Shell lines loading the job modules: the job stops if loading fails."""
    if not modules:
        return ''
    mods = ' '.join(modules)
    lines = ['module purge'] if CONFIG['modules']['purge'] else []
    lines.append(f'module load {mods} || {{ echo "ERROR: cannot load modules: {mods}"; exit 1; }}')
    lines.append('module list 2>&1')
    return '\n'.join(lines)

def helper(script):
    """Command running one of the slotter scripts with the configured Python.

    SLOTTER_DIR is passed explicitly, so the script on the node reads the
    same configuration used to write the job.
    """
    return (f"SLOTTER_DIR={shlex.quote(CONFIG_DIR)} "
            f"{shlex.quote(CONFIG['cluster']['python'])} {shlex.quote(os.path.join(REPO_DIR, script))}")

def create_qm_all(calc_obj, input_file_no_extention, output_file, enabled=True):
    if not enabled or calc_obj.name == 'run' or not calc_obj.keep:
        return ''

    cmd = f'ext=({" ".join(calc_obj.keep)})\n'
    cmd += '''mkdir qm_all
mv ./* qm_all/
cp qm_all/{output} ./
for i in ${ext[@]}; do
    echo {input}.$i
    if [[ -e qm_all/{input}.$i ]]; then
	    cp -v qm_all/{input}.$i ./
    fi
done
'''.replace('{output}', output_file).replace('{input}', input_file_no_extention)
    return cmd

COMPLETED = 'CALCULATION COMPLETED CORRECTLY'
NOT_COMPLETED = 'CALCULATION NOT COMPLETED'

def outcome_commands(calc_obj, output_file, readme, readme_file='README.md'):
    """Shell lines setting $OUTCOME (and appending it to readme_file if `readme`).

    Only the end of the calculation is checked (calc_obj.outcome_test): the
    results are in the output, and are not parsed.
    """
    readme_file = shlex.quote(readme_file)
    lines = [f'if {calc_obj.outcome_test(output_file or "")}; then',
             f'    OUTCOME="{COMPLETED}"',
             'else',
             f'    OUTCOME="{NOT_COMPLETED}"',
             'fi',
             'echo "$OUTCOME (exit code $STATUS)"']
    if readme:
        lines.append(f"printf -- '\\n--------------------\\n%s\\n--------------------\\n' \"$OUTCOME\" >> {readme_file}")
        if output_file:
            # The end of the output usually says why it stopped
            out = shlex.quote(output_file)
            lines.append(f'if [[ $OUTCOME == "{NOT_COMPLETED}" && -f {out} ]]; then '
                         f'echo >> {readme_file}; tail -n 10 {out} >> {readme_file}; fi')
    return '\n'.join(lines)

def program_modules(calc_obj, version):
    """Modules of the program, with version= applied: ValueError if it cannot be loaded."""
    if version and not calc_obj.module:
        raise ValueError(f'{calc_obj.name} has no module to apply version={version} to')
    return resolve(calc_obj.modules, version)

def write_job_file(input_file, calculation, calc_cmd='', slurm_cmd=None, run=False, module=(), command_1='', command_2='', job_file='job-slurm.sh', qm_all=None, tag=''):
    """Write job_file from the template.

    qm_all: move the files to qm_all/ at the end; None is the user's choice
    (~/.config/slotter.toml, [job] qm_all).
    tag: -<stem> when several calculations share the folder (src/job_array.py):
    their files get it (README-<stem>.md, .originals-<stem>), while update
    and live get the job id.
    """
    calc_obj = CALCULATORS[calculation]
    template = read_template()
    if tag and missing_for_shared_folder(template):
        raise ValueError(f'{template_file()}: no {", ".join(missing_for_shared_folder(template))}, '
                         'needed by several calculations in one folder')
    if qm_all is None:
        qm_all = user_choice('job', 'qm_all', calculation)

    # Work on a copy: callers (e.g. fluo.py) reuse the same dictionary
    slurm_cmd = dict(slurm_cmd or {})
    command_1 = command_1 or ''
    command_2 = command_2 or ''

    version = slurm_cmd.pop('version', None)
    email_address = slurm_cmd.pop('mail-user', None)

    sbatch = {**CONFIG['slurm'], **slurm_cmd}
    cpu = sbatch.get('ntasks', 1)

    if not run:
        modules = program_modules(calc_obj, version)
        input_file_no_extention = os.path.splitext(input_file)[0]
        output_file = calc_obj.output_file(input_file)
        pre, cm, post = calc_obj.job_commands(calc_cmd, input_file, cpu, sbatch.get('mem', ''))
    else:
        modules = resolve(module, version)
        input_file_no_extention, output_file = None, None
        pre, cm, post = '', calc_cmd, ''

    command_1 = '\n'.join(c for c in (command_1, pre) if c)
    command_2 = '\n'.join(c for c in (command_2, post) if c)

    readme = f'README{tag}.md'
    job_name = f'{calc_obj.abbreviation}-{input_file_no_extention if not run else "run"}'
    values = {
        'sbatch': f'#SBATCH --job-name={job_name}\n{convert_slurm_cmd(sbatch)}',
        'scratch': CONFIG['cluster']['scratch'],
        'output': output_file if not run else '',
        'cpu': cpu,
        'mem': sbatch.get('mem', ''),
        'modules': module_commands(modules),
        'pre': command_1,
        'command': cm,
        'command_quoted': shlex.quote(cm),
        'qm_all': create_qm_all(calc_obj, input_file_no_extention, output_file, qm_all),
        # The user can turn the README update off in ~/.config/slotter.toml ([readme] parse_output)
        'outcome': outcome_commands(calc_obj, output_file, not run and readme_parsing(calculation, 'parse_output'), readme),
        # Several calculations in the folder: the README.md of this one
        'email': f'{helper("email_sender.py")} {email_address} $SLURM_SUBMIT_DIR{"/" + shlex.quote(readme) if tag else ""} $SLURM_JOB_ID "$OUTCOME"',
        'tag': tag,
        'job_tag': '-$SLURM_JOB_ID' if tag else '',
        'post': command_2,
    }
    with open(job_file, 'w') as f:
        f.write(PLACEHOLDER.sub(lambda m: str(values[m[1]]), template))

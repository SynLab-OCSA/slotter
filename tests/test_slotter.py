import glob
import os
import re
import shutil
import tomllib
import subprocess
import sys

import pytest

from src.config import (CONFIG, USER_CONFIG, CONFIG_DIR, SETUP_FILE, REPO_DIR, slotter_dir,
                        load_cluster_config, load_user_config, readme_parsing)
from src.bootstrap import cluster_python
from src import modules
from src.parser_input import read_sbatchrc
from src.write_job_file import write_job_file, outcome_commands
from src import calculators
from src.calculators import CALCULATORS, Calculator, read_definitions


# --- module discovery ------------------------------------------------------

def test_versions_skip_hidden_files_and_sort_naturally(modulepath):
    (modulepath / 'apps/orca/6.10.0').write_text('#%Module1.0\n')
    assert modules.versions('orca') == ['5.0.4', '6.1.1', '6.10.0']


def test_exists(modulepath):
    assert modules.exists('orca')
    assert modules.exists('orca/6.1.1')
    assert modules.exists('conda/grimmelab')
    assert not modules.exists('orca/.common.tcl')
    assert not modules.exists('orca/9.9')
    assert not modules.exists('turbomole')


def test_available_lists_modules_in_every_directory(modulepath):
    found = modules.available()
    assert {'openmpi/4.1.8', 'orca/6.1.1', 'conda/grimmelab', 'enan/1.1'} <= set(found)
    assert not [m for m in found if '/.' in m]


def test_resolve_applies_version_to_last_module(modulepath):
    assert modules.resolve(['conda/grimmelab', 'crest'], '3.0.2') == ['conda/grimmelab', 'crest/3.0.2']


def test_resolve_missing_version_lists_available(modulepath):
    with pytest.raises(ValueError, match=r'orca \(7.0\) is missing. Available: 5.0.4, 6.1.1'):
        modules.resolve(['orca'], '7.0')


def test_resolve_unknown_module(modulepath):
    with pytest.raises(ValueError, match='Module turbomole not found'):
        modules.resolve(['turbomole'])


def test_resolve_without_modulepath_skips_the_check(monkeypatch):
    monkeypatch.delenv('MODULEPATH', raising=False)
    monkeypatch.setitem(CONFIG['modules'], 'modulepath', [])
    assert modules.resolve(['orca'], '7.0') == ['orca/7.0']


# --- configuration ---------------------------------------------------------

def make_config(path, setup=''):
    """A configuration folder: setup.toml and an empty calculators/."""
    (path / 'calculators').mkdir(parents=True)
    (path / 'setup.toml').write_text(setup)
    return path


def test_slotter_dir(tmp_path, monkeypatch):
    d = make_config(tmp_path / 'anywhere' / 'slotter')
    monkeypatch.setenv('SLOTTER_DIR', str(d))
    assert slotter_dir() == str(d)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('SLOTTER_DIR', '~/anywhere/slotter')
    assert slotter_dir() == str(d)

    monkeypatch.setenv('SLOTTER_DIR', str(tmp_path))
    with pytest.raises(SystemExit, match='has no setup.toml'):
        slotter_dir()
    monkeypatch.delenv('SLOTTER_DIR')
    with pytest.raises(SystemExit, match='set SLOTTER_DIR to the folder with setup.toml and calculators/'):
        slotter_dir()


def test_slotter_dir_of_jobs_written_before_it(tmp_path, monkeypatch):
    # They pass ROUTINE_CLUSTER=<repository>/clusters/<name>.toml
    from src import bootstrap
    monkeypatch.setattr(bootstrap, 'REPO_DIR', str(tmp_path))
    make_config(tmp_path / 'clusters' / 'synlab')
    monkeypatch.delenv('SLOTTER_DIR')
    monkeypatch.setenv('ROUTINE_CLUSTER', str(tmp_path / 'clusters' / 'synlab.toml'))
    assert slotter_dir() == str(tmp_path / 'clusters' / 'synlab')


def test_the_test_configuration_is_used():
    assert CONFIG_DIR == os.path.join(REPO_DIR, 'tests', 'config')
    assert SETUP_FILE == os.path.join(CONFIG_DIR, 'setup.toml')


def test_cluster_config_keeps_the_defaults(tmp_path):
    f = tmp_path / 'cluster.toml'
    f.write_text('[cluster]\nmax_cpu = 8\n')
    config = load_cluster_config(str(f))
    assert config['cluster']['max_cpu'] == 8
    assert config['cluster']['scratch'] == '/scratch'
    assert config['slurm']['nodes'] == '1-1'


def test_user_config_reads_only_readme_and_job(tmp_path, capsys):
    f = tmp_path / 'user.toml'
    f.write_text('[readme]\nparse_output = false\n\n[job.crest]\nqm_all = false\n\n'
                 '[cluster]\nscratch = "/home/me"\n')
    assert load_user_config(str(f)) == {
        'readme': {'parse_input': True, 'parse_output': False},
        'job': {'check_folder': True, 'qm_all': True, 'same_folder': False, 'crest': {'qm_all': False}}}
    assert '[cluster] ignored, only [readme] and [job]' in capsys.readouterr().err


def test_user_config_missing_file_uses_defaults(tmp_path):
    assert load_user_config(str(tmp_path / 'missing.toml')) == {
        'readme': {'parse_input': True, 'parse_output': True},
        'job': {'check_folder': True, 'qm_all': True, 'same_folder': False}}


def test_readme_parsing_per_calculator(monkeypatch):
    monkeypatch.setitem(USER_CONFIG, 'readme',
                        {'parse_input': True, 'parse_output': True, 'crest': {'parse_output': False}})
    assert readme_parsing('orca', 'parse_output')
    assert not readme_parsing('crest', 'parse_output')
    assert readme_parsing('crest', 'parse_input')


@pytest.mark.parametrize('extra, version, slurm, program', [
    (['version=3.0.2'], '3.0.2', {}, ''),
    (['--version=3.0.2'], '3.0.2', {}, ''),
    # Anything with "=" is for slotter, never for the program
    (['--T=6', 'partition=long'], None, {'T': '6', 'partition': 'long'}, ''),
    # Without "=" it goes to the program
    (['--version', '3.0.2'], None, {}, ' --version 3.0.2'),
])
def test_arguments_with_equals_are_for_slotter(workdir, monkeypatch, extra, version, slurm, program):
    from src.parser_input import parse_args
    (workdir / 'mol.xyz').write_text('1\n\nH 0 0 0\n')
    monkeypatch.setattr(sys, 'argv', ['job.py', 'crest', 'mol.xyz', '-T', '4'] + extra)
    files, _, options, slurm_cmd, _, _ = parse_args()
    assert files == ['mol.xyz'] and options == '-T 4' + program
    assert slurm_cmd.get('version') == version
    assert {k: v for k, v in slurm_cmd.items() if k in ('T', 'partition')} == slurm


@pytest.mark.parametrize('extra, user, choices', [
    ([], {}, {'check_folder': True, 'qm_all': True, 'same_folder': False}),
    # One calculation from the command line
    (['--no-check', '--no-qm-all', '--same-folder'], {}, {'check_folder': False, 'qm_all': False, 'same_folder': True}),
    # Every calculation, or every calculation of one program, from ~/.config/slotter.toml
    ([], {'qm_all': False}, {'check_folder': True, 'qm_all': False, 'same_folder': False}),
    ([], {'crest': {'check_folder': False, 'same_folder': True}}, {'check_folder': False, 'qm_all': True, 'same_folder': True}),
    ([], {'orca': {'check_folder': False}}, {'check_folder': True, 'qm_all': True, 'same_folder': False}),
])
def test_folder_check_and_qm_all_can_be_turned_off(workdir, monkeypatch, extra, user, choices):
    from src.parser_input import parse_args
    monkeypatch.setitem(USER_CONFIG, 'job', {'check_folder': True, 'qm_all': True, **user})
    (workdir / 'mol.xyz').write_text('1\n\nH 0 0 0\n')
    monkeypatch.setattr(sys, 'argv', ['job.py', 'crest', 'mol.xyz', '-T', '4'] + extra)
    _, _, options, _, _, found = parse_args()
    assert found == {**choices, 'force': False}
    assert options == '-T 4'


def test_options_of_the_program_are_not_abbreviations_of_ours(workdir, monkeypatch):
    from src.parser_input import parse_args
    (workdir / 'mol.xyz').write_text('1\n\nH 0 0 0\n')
    monkeypatch.setattr(sys, 'argv', ['job.py', 'crest', 'mol.xyz', '--no', '--wr'])
    _, _, options, _, write, found = parse_args()
    assert options == '--no --wr'
    assert not write and found['check_folder']


def test_read_sbatchrc(tmp_path):
    rc = tmp_path / 'sbatchrc'
    rc.write_text('#SBATCH --mail-user=me@example.org\n'
                  '\n'
                  '# a comment\n'
                  '#SBATCH --partition long\n'
                  '#SBATCH --exclusive   # whole node\n'
                  '#SBATCH --time=1-00:00:00\n')
    assert read_sbatchrc(str(rc)) == {
        'mail-user': 'me@example.org', 'partition': 'long', 'exclusive': None, 'time': '1-00:00:00'}


def test_read_sbatchrc_default_without_file(tmp_path):
    assert read_sbatchrc(str(tmp_path / 'missing')) == {'mail-type': 'NONE'}


# --- job file --------------------------------------------------------------

INPUTS = {
    'orca':     ('mol.inp', '! B3LYP def2-SVP Opt PAL4\n%maxcore 3000\n* xyzfile 0 1 Mol.xyz\n', 'orca mol.inp', 'orca'),
    'gaussian': ('mol.gjf', '%nprocshared=4\n%mem=8GB\n# opt b3lyp/6-31g(d)\n', 'gaussian mol.gjf', 'gaussian'),
    'crest':    ('mol.xyz', '1\n\nH 0 0 0\n', 'crest mol.xyz -T 4', 'conda/grimmelab'),
    'xtb':      ('mol.xyz', '1\n\nH 0 0 0\n', 'xtb mol.xyz -P 4', 'conda/grimmelab xtb'),
    'censo':    ('ens.xyz', '1\n\nH 0 0 0\n', 'censo ens.xyz --maxcores 4', 'conda/grimmelab'),
    'enan':     ('ens.xyz', '1\n\nH 0 0 0\n', 'enan ens.xyz -cpu 4', 'enan'),
    'rot8':     ('ens.xyz', '1\n\nH 0 0 0\n', 'rot8 ens.xyz --cpu 4', 'rot8'),
    'molcas':   ('mol.input', '* MOLCAS_NPROCS = 4\n&GATEWAY\n', 'molcas mol.input', 'openmolcas'),
    'openqp':   ('mol.inp', '[input]\n', 'openqp mol.inp --cpu 4', 'openqp'),
    'nwchem':   ('mol.nw', 'memory total 2 gb\n', 'nwchem mol.nw --cpu 4', 'conda/nwchem'),
}


def make_job(calc, workdir, **slurm):
    fname, content, cmd, _ = INPUTS[calc]
    (workdir / fname).write_text(content)
    cpu, mem = CALCULATORS[calc].get_cpu_memory(fname, cmd, False)
    write_job_file(fname, calc, calc_cmd=cmd, slurm_cmd={'ntasks': cpu, 'mem': mem, **slurm})
    return (workdir / 'job-slurm.sh').read_text()


@pytest.mark.parametrize('calc', sorted(INPUTS))
def test_job_file_for_every_calculator(calc, modulepath, workdir):
    job = make_job(calc, workdir)
    module = INPUTS[calc][3]

    subprocess.run(['bash', '-n', 'job-slurm.sh'], check=True)
    assert not re.search(r'\{[a-z_]+\}', job), 'unformatted placeholder in the job file'
    assert 'module purge\n' in job
    assert f'module load {module} || {{ echo' in job
    assert '#SBATCH --ntasks=4' in job
    assert '#SBATCH --nodes=1-1' in job


def test_version_goes_to_the_module_not_to_sbatch(modulepath, workdir):
    job = make_job('orca', workdir, version='5.0.4')
    assert 'module load orca/5.0.4 ||' in job
    assert '--version' not in job


def test_missing_version_stops_before_writing(modulepath, workdir):
    with pytest.raises(ValueError, match='Available: 5.0.4, 6.1.1'):
        make_job('orca', workdir, version='7.0')
    assert not (workdir / 'job-slurm.sh').exists()


def test_version_goes_to_the_program_module(modulepath, workdir, monkeypatch):
    # requires first, as written; the program module last, with the version
    monkeypatch.setitem(calculators.DEFINITIONS, 'crest', {
        **calculators.DEFINITIONS['crest'], 'requires': ['conda/grimmelab'], 'module': 'crest'})
    job = make_job('crest', workdir, version='3.0.2')
    assert 'module load conda/grimmelab crest/3.0.2 ||' in job
    assert 'module load conda/grimmelab xtb/6.7.1 ||' in make_job('xtb', workdir, version='6.7.1')


def test_version_without_a_program_module_stops_before_writing(modulepath, workdir):
    with pytest.raises(ValueError, match='censo has no module to apply version=2.0 to'):
        make_job('censo', workdir, version='2.0')
    assert not (workdir / 'job-slurm.sh').exists()


def test_purge_can_be_disabled(modulepath, workdir, monkeypatch):
    monkeypatch.setitem(CONFIG['modules'], 'purge', False)
    assert 'module purge' not in make_job('orca', workdir)


def test_fluo_style_call(modulepath, workdir):
    # fluo.py / phospho.py: calc_cmd as third positional argument, command_1=None,
    # and the same slurm dictionary reused for several jobs
    (workdir / 'THF-step3.gjf').write_text('%nprocshared=4\n# td\n')
    sc = {'ntasks': 4, 'mem': '6000MB', 'mail-user': 'me@example.org'}
    write_job_file('THF-step3.gjf', 'gaussian', 'g16 THF-step3.gjf', slurm_cmd=sc, command_1=None, command_2=None)
    job = (workdir / 'job-slurm.sh').read_text()
    assert 'export GAUSS_SCRDIR=$SCRATCH_DIR' in job
    assert 'me@example.org $SLURM_SUBMIT_DIR $SLURM_JOB_ID' in job
    assert sc == {'ntasks': 4, 'mem': '6000MB', 'mail-user': 'me@example.org'}


def test_helper_scripts_get_the_cluster(modulepath, workdir):
    job = make_job('orca', workdir)
    lines = [l for l in job.splitlines() if '.py' in l]
    # The outcome is checked by the job itself: Python only sends the email
    assert len(lines) == 1 and 'email_sender.py' in lines[0]
    assert lines[0].startswith(f'SLOTTER_DIR={CONFIG_DIR} /usr/bin/python3 ')
    assert lines[0].endswith('$SLURM_SUBMIT_DIR $SLURM_JOB_ID "$OUTCOME"')


def test_command_of_every_calculator(modulepath, workdir):
    commands = {}
    for calc in INPUTS:
        job = make_job(calc, workdir)
        commands[calc] = job.split('# RUNNING THE CALCULATION\n\n')[1].split('\n')[0]
    assert commands == {
        'orca': '$(which orca) mol.inp > mol.out',
        'gaussian': 'g16 mol.gjf',
        'crest': 'crest mol.xyz -T 4 > crest.out 2> crest.error',
        'xtb': 'xtb mol.xyz -P 4 > xtb.out 2> xtb.error',
        'censo': 'censo ens.xyz --maxcores 4 --inprc ./.censo2rc > censo.out 2> censo.error',
        'enan': 'ensemble_analyzer -e ens.xyz -cpu 4 -o output.out',
        'rot8': 'rot8 ens.xyz --cpu 4',
        'molcas': 'pymolcas mol.input > mol.out',
        # --cpu is for slotter only
        'openqp': 'mpirun -np 4 openqp mol.inp',
        'nwchem': 'mpirun -np 4 nwchem mol.nw > mol.out',
    }


def test_pre_and_post_commands(modulepath, workdir):
    job = make_job('molcas', workdir)
    assert 'export WorkDir=$SCRATCH_DIR\nfor i in $(seq 0 4); do mkdir tmp_$i; done' in job
    assert job.index('rm -rf tmp_*') > job.index('email_sender.py')


def test_exports_are_set_by_the_program_not_by_the_template(modulepath, workdir, monkeypatch):
    job = make_job('crest', workdir)
    assert 'export OMP_NUM_THREADS=4\nexport MKL_NUM_THREADS=4\n' in job
    assert job.index('export OMP_NUM_THREADS') > job.index('module load')
    # One thread per MPI process
    for calc in ('orca', 'nwchem', 'openqp'):
        job = make_job(calc, workdir)
        assert 'export OMP_NUM_THREADS=1' in job and 'export OPENBLAS_NUM_THREADS=1' in job
        assert '_NUM_THREADS=4' not in job
    # A program without pre gets no variable at all
    monkeypatch.setitem(calculators.DEFINITIONS, 'mytool', {'command': 'mytool {input}'})
    (workdir / 'a.in').write_text('')
    write_job_file('a.in', 'mytool', 'mytool a.in', {'ntasks': 2})
    job = (workdir / 'job-slurm.sh').read_text()
    assert 'export' not in job and 'ulimit' not in job


def test_copy_to_the_scratch(modulepath, workdir, tmp_path):
    job = make_job('orca', workdir)
    copy = job[job.index('rsync -a'):job.index('exit 1; }', job.index('rsync -a')) + len('exit 1; }')]
    write_files(workdir, {name: '' for name in (
        '.censo2rc', 'update.xyz', 'sub/update', 'sub/README.md',          # inputs, hidden ones too
        'README.md', 'README-a.md', 'out-1-n.slurm', '.err-29100.slurm', 'job-array.sh',  # slotter and the job
        'update', 'live', 'update-12', 'live-12', '.originals/mol.inp', '.originals-a/x', 'qm_all/old.out')})
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    env = {**os.environ, 'SLURM_SUBMIT_DIR': str(workdir), 'SCRATCH_DIR': str(scratch)}
    subprocess.run(['bash', '-c', copy], env=env, check=True)
    copied = sorted(str(p.relative_to(scratch)) for p in scratch.rglob('*') if p.is_file())
    assert copied == ['.censo2rc', 'mol.inp', 'sub/README.md', 'sub/update', 'update.xyz']
    # A scratch that cannot be written stops the job
    (tmp_path / 'not-a-folder').write_text('')
    env['SCRATCH_DIR'] = str(tmp_path / 'not-a-folder' / 'scratch')
    result = subprocess.run(['bash', '-c', copy], env=env, capture_output=True, text=True)
    assert result.returncode == 1 and 'ERROR: cannot copy the folder to' in result.stdout


def test_start_time_is_taken_when_the_job_runs(modulepath, workdir):
    assert 'echo "Job execution start: $(date ' in make_job('orca', workdir)


@pytest.mark.parametrize('where', ['user', 'argument'])
def test_qm_all_can_be_turned_off(modulepath, workdir, monkeypatch, where):
    assert 'mkdir qm_all' in make_job('orca', workdir)
    if where == 'user':
        monkeypatch.setitem(USER_CONFIG, 'job', {'check_folder': True, 'qm_all': True, 'orca': {'qm_all': False}})
        assert 'mkdir qm_all' in make_job('gaussian', workdir)
        job = make_job('orca', workdir)
    else:
        (workdir / 'mol.inp').write_text('! PAL4\n')
        write_job_file('mol.inp', 'orca', 'orca mol.inp', {'ntasks': 4}, qm_all=False)
        job = (workdir / 'job-slurm.sh').read_text()
    assert 'mkdir qm_all' not in job
    subprocess.run(['bash', '-n', 'job-slurm.sh'], check=True)


# --- template of job-slurm.sh ----------------------------------------------

def test_template_of_the_cluster_folder(modulepath, workdir, monkeypatch, tmp_path):
    from src import write_job_file as wjf
    d = make_config(tmp_path / 'cluster')
    (d / 'job.sh').write_text(
        '#!/bin/bash\n{sbatch}\n# ${HOME} {unknown} stay as they are\n{modules}\n{pre}\n'
        'cd {scratch}\n{command}\nSTATUS=$?\n{qm_all}\n{outcome}\n{email}\n{post}\n')
    assert wjf.template_file(str(d)) == str(d / 'job.sh')
    assert wjf.template_file(str(tmp_path)) == wjf.DEFAULT_TEMPLATE
    monkeypatch.setattr(wjf, 'template_file', lambda: str(d / 'job.sh'))
    job = make_job('orca', workdir)
    assert job.startswith('#!/bin/bash\n#SBATCH --job-name=o-mol\n')
    assert '# ${HOME} {unknown} stay as they are\nmodule purge\n' in job
    assert 'cd /scratch\n$(which orca) mol.inp > mol.out\nSTATUS=$?\n' in job


@pytest.mark.parametrize('change, error', [
    (('{email}\n', ''), r'missing \{email\}'),
    (('{outcome}\n', ''), r'missing \{outcome\}'),
    (('{command}\nSTATUS=$?\n', '{command} && echo done\nSTATUS=$?\n'), r'followed by the line STATUS=\$\?'),
    (('STATUS=$?\n', ''), r'followed by the line STATUS=\$\?'),
])
def test_template_must_keep_the_calculation_outcome_and_email(tmp_path, change, error):
    from src.write_job_file import read_template, DEFAULT_TEMPLATE
    text = open(DEFAULT_TEMPLATE).read()
    assert change[0] in text
    path = tmp_path / 'job.sh'
    path.write_text(text.replace(change[0], change[1]))
    with pytest.raises(ValueError, match=error):
        read_template(str(path))


# --- job arrays ------------------------------------------------------------

ORCA = '! B3LYP def2-SVP Opt PAL{}\n%maxcore {}\n'


def array_choices(**choices):
    return {'check_folder': True, 'qm_all': True, 'same_folder': False, 'force': False, **choices}


def write_files(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


@pytest.mark.parametrize('argv, files, options', [
    (['crest', 'a.xyz', 'b.xyz', '-T', '4', 'version=1'], ['a.xyz', 'b.xyz'], '-T 4'),
    (['crest', 'c/a.xyz', '-T', '4'], ['c/a.xyz'], '-T 4'),
    # Inputs: the files right after the program, with the extension of the first
    (['orca', 'a.inp', 'a.xyz'], ['a.inp'], 'a.xyz'),
    (['rot8', 'a.xyz', 'word', 'b.xyz', '--cpu', '8'], ['a.xyz'], 'word b.xyz --cpu 8'),
])
def test_inputs_and_options_of_the_program(workdir, monkeypatch, argv, files, options):
    from src.parser_input import parse_args
    write_files(workdir, {'a.xyz': '', 'b.xyz': '', 'c/a.xyz': '', 'a.inp': ''})
    monkeypatch.setattr(sys, 'argv', ['job.py'] + argv)
    found, _, found_options, slurm, _, _ = parse_args()
    assert (found, found_options) == (files, options)
    monkeypatch.setattr(sys, 'argv', ['job.py', 'crest', 'missing.xyz'])
    with pytest.raises(IOError, match='does not exist: missing.xyz'):
        parse_args()


def test_array_one_task_per_folder(modulepath, workdir):
    from src.job_array import is_array, write_array
    write_files(workdir, {'conf_1/opt.inp': ORCA.format(4, 3000), 'conf_2/opt.inp': ORCA.format(8, 2000),
                          'conf_2/opt.xyz': ''})
    files = ['conf_1/opt.inp', 'conf_2/opt.inp']
    assert is_array(files) and is_array(files[:1]) and not is_array(['opt.inp'])
    assert write_array('orca', files, '', {'partition': 'long', 'version': '6.1.1'}, array_choices()) == 'job-array.sh'

    array = (workdir / 'job-array.sh').read_text()
    # The largest resources among the inputs, a task for each folder
    for line in ('#SBATCH --job-name=o-calc', '#SBATCH --ntasks=8', '#SBATCH --mem=16000',
                 '#SBATCH --partition=long', '#SBATCH --array=0-1',
                 # SLURM keeps only its own messages, the task writes its files in its folder
                 '#SBATCH --output=/dev/null', '#SBATCH --error=.err-%A.slurm', '#SBATCH --open-mode=append',
                 'JOBS=(conf_1/job-slurm.sh conf_2/job-slurm.sh)',
                 'exec bash "$(basename "$JOB")" > "out-${SLURM_JOB_ID}-${SLURMD_NODENAME}.slurm" '
                 '2> "err-${SLURM_JOB_ID}-${SLURMD_NODENAME}.slurm"'):
        assert line + '\n' in array
    assert 'version' not in array
    # Each folder: its own job, as by job.py alone
    for folder, cpu in (('conf_1', 4), ('conf_2', 8)):
        job = (workdir / folder / 'job-slurm.sh').read_text()
        assert f'#SBATCH --ntasks={cpu}\n' in job and 'module load orca/6.1.1 ||' in job
        assert 'mkdir qm_all' in job and '.originals\n' in job
        assert (workdir / folder / 'README.md').exists()
        subprocess.run(['bash', '-n', str(workdir / folder / 'job-slurm.sh')], check=True)
    subprocess.run(['bash', '-n', 'job-array.sh'], check=True)


def test_array_task_runs_the_job_of_its_folder(modulepath, workdir):
    from src.job_array import write_array
    write_files(workdir, {'conf_1/opt.inp': ORCA.format(4, 3000), 'conf 2/opt.inp': ORCA.format(4, 3000)})
    write_array('orca', ['conf_1/opt.inp', 'conf 2/opt.inp'], '', {}, array_choices())
    # Instead of the calculation: where it runs, and from where it was submitted
    (workdir / 'conf 2' / 'job-slurm.sh').write_text('echo "$PWD|$SLURM_SUBMIT_DIR"\necho problem >&2\n')
    env = {**os.environ, 'SLURM_ARRAY_TASK_ID': '1', 'SLURM_SUBMIT_DIR': str(workdir),
           'SLURM_JOB_ID': '29103', 'SLURMD_NODENAME': 'node1'}
    subprocess.run(['bash', 'job-array.sh'], env=env, check=True)
    # Its out/err files are in the folder of the calculation, as for a single job
    folder = workdir / 'conf 2'
    here, submit = (folder / 'out-29103-node1.slurm').read_text().strip().split('|')
    assert os.path.realpath(here) == os.path.realpath(submit) == os.path.realpath(folder)
    assert (folder / 'err-29103-node1.slurm').read_text() == 'problem\n'
    assert not list(workdir.glob('*.slurm'))


def test_array_inputs_of_one_folder_get_a_folder_each(modulepath, workdir):
    from src.job_array import write_array
    write_files(workdir, {'a.inp': ORCA.format(4, 3000), 'b.inp': ORCA.format(4, 3000), 'a.xyz': 'geometry of a',
                          'shared.xyz': 'for both', 'other.inp': 'not in the array', 'README.md': 'old'})
    write_array('orca', ['a.inp', 'b.inp'], '', {}, array_choices())
    assert sorted(os.listdir('a')) == ['README.md', 'a.inp', 'a.xyz', 'job-slurm.sh', 'shared.xyz']
    assert sorted(os.listdir('b')) == ['README.md', 'b.inp', 'job-slurm.sh', 'shared.xyz']
    assert sorted(os.listdir('.')) == ['README.md', 'a', 'b', 'job-array.sh', 'other.inp', 'shared.xyz']
    assert (workdir / 'README.md').read_text() == 'old'
    assert 'JOBS=(a/job-slurm.sh b/job-slurm.sh)' in (workdir / 'job-array.sh').read_text()


def test_array_in_the_same_folder(modulepath, workdir):
    from src.job_array import write_array
    write_files(workdir, {'a.inp': ORCA.format(4, 3000), 'b.inp': ORCA.format(4, 3000)})
    write_array('orca', ['a.inp', 'b.inp'], '', {'mail-user': 'me@example.org'}, array_choices(same_folder=True))
    assert sorted(os.listdir('.')) == ['README-a.md', 'README-b.md', 'a.inp', 'b.inp',
                                       'job-array.sh', 'job-slurm-a.sh', 'job-slurm-b.sh']
    assert 'JOBS=(./job-slurm-a.sh ./job-slurm-b.sh)' in (workdir / 'job-array.sh').read_text()
    job = (workdir / 'job-slurm-a.sh').read_text()
    # The files of the calculation get its name, and qm_all/ would mix them
    # update and live get the job id, the other files the name of the calculation
    for text in ('mkdir .originals-a\n', '$SLURM_SUBMIT_DIR/update-$SLURM_JOB_ID\n', '$SLURM_SUBMIT_DIR/live-$SLURM_JOB_ID\n',
                 'rm $SLURM_SUBMIT_DIR/update-$SLURM_JOB_ID $SLURM_SUBMIT_DIR/live-$SLURM_JOB_ID',
                 '>> README-a.md\n', 'me@example.org $SLURM_SUBMIT_DIR/README-a.md $SLURM_JOB_ID'):
        assert text in job
    assert 'mkdir qm_all' not in job
    subprocess.run(['bash', '-n', 'job-slurm-a.sh'], check=True)


@pytest.mark.parametrize('files, choices, slurm, error', [
    ({'c/a.inp': '! PAL4\n', 'c/b.inp': ''}, {}, {}, r'c holds other .inp files \(b.inp\): one input per folder'),
    ({'c/a.inp': '! PAL4\n', 'c/old.out': ''}, {}, {}, r'already present \(c/old.out\)'),
    ({'a.inp': '! PAL4\n', 'b.inp': '', 'a/x': ''}, {}, {}, 'a already exists'),
    ({'a.inp': '! PAL4\n', 'b.inp': '', 'old.log': ''}, {}, {}, r'already present \(./old.log\)'),
    ({'a.inp': '! PAL4\n', 'b.inp': '', 'a.out': ''}, {'same_folder': True}, {}, r'already present \(./a.out\)'),
    ({'a.inp': '! PAL4\n', 'b.inp': ''}, {}, {'version': '7.0'}, 'Available: 5.0.4, 6.1.1'),
    ({'a.inp': '! PAL4\n', 'b.inp': ''}, {}, {'array': '0-3'}, 'only the tasks at a time'),
])
def test_array_stops_before_moving_or_writing(modulepath, workdir, files, choices, slurm, error):
    from src.job_array import write_array
    write_files(workdir, files)
    before = sorted(str(p.relative_to(workdir)) for p in workdir.rglob('*'))
    with pytest.raises(ValueError, match=error):
        write_array('orca', ['a.inp', 'b.inp'] if 'b.inp' in files else ['c/a.inp'], '', slurm, array_choices(**choices))
    assert sorted(str(p.relative_to(workdir)) for p in workdir.rglob('*')) == before


@pytest.mark.parametrize('slurm, redirections', [
    ({'output': 'out-%j-%N.slurm', 'error': 'err-%j-%N.slurm'},
     '> "out-${SLURM_JOB_ID}-${SLURMD_NODENAME}.slurm" 2> "err-${SLURM_JOB_ID}-${SLURMD_NODENAME}.slurm"'),
    # Without --error, or the same file: one file, as SLURM does
    ({'output': 'log-%x-%A_%a.txt', 'error': None}, '> "log-${SLURM_JOB_NAME}-${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.txt" 2>&1'),
    ({'output': 'o-%j', 'error': 'o-%j'}, '> "o-${SLURM_JOB_ID}" 2>&1'),
    ({'output': None, 'error': None}, '> "slurm-${SLURM_JOB_ID}.out" 2>&1'),
    # Literal text stays literal in bash
    ({'output': '100%%-$HOME "x".out', 'error': None}, '> "100%-\\$HOME \\"x\\".out" 2>&1'),
])
def test_out_and_err_files_of_a_task(slurm, redirections):
    from src.job_array import task_redirections
    assert task_redirections(slurm) == redirections


def test_file_name_codes_a_task_cannot_expand(modulepath, workdir):
    from src.job_array import write_array
    write_files(workdir, {'c/a.inp': '! PAL4\n'})
    with pytest.raises(ValueError, match='output = out-%s.slurm: %s cannot be used in a job array'):
        write_array('orca', ['c/a.inp'], '', {'output': 'out-%s.slurm'}, array_choices())
    assert not (workdir / 'c' / 'job-slurm.sh').exists()


def test_array_options(modulepath, workdir):
    from src.job_array import write_array
    write_files(workdir, {'c/a.inp': ORCA.format(4, 3000), 'c/old.out': ''})
    # Outputs already there are fine with --no-check; at most 2 tasks at a time
    write_array('orca', ['c/a.inp'], '', {'array': '%2'}, array_choices(check_folder=False, qm_all=False))
    assert '#SBATCH --array=0-0%2\n' in (workdir / 'job-array.sh').read_text()
    job = (workdir / 'c' / 'job-slurm.sh').read_text()
    assert 'mkdir qm_all' not in job
    # Only job-array.sh is an array: the job of the calculation can be submitted by itself
    assert '--array' not in job
    # A program writing always the same output cannot share the folder
    write_files(workdir, {'a.xyz': '', 'b.xyz': ''})
    with pytest.raises(ValueError, match='crest always writes crest.out'):
        write_array('crest', ['a.xyz', 'b.xyz'], '', {}, array_choices(same_folder=True))


@pytest.mark.parametrize('default, slurm, array', [
    (0, {}, '0-1'),
    (4, {}, '0-1%4'),
    # The command line wins over the default of the cluster
    (4, {'array': '%2'}, '0-1%2'),
])
def test_tasks_at_a_time(modulepath, workdir, monkeypatch, default, slurm, array):
    from src.job_array import write_array
    monkeypatch.setitem(CONFIG['cluster'], 'array_limit', default)
    write_files(workdir, {'c1/a.inp': '! PAL4\n', 'c2/a.inp': '! PAL4\n'})
    write_array('orca', ['c1/a.inp', 'c2/a.inp'], '', slurm, array_choices())
    assert f'#SBATCH --array={array}\n' in (workdir / 'job-array.sh').read_text()


@pytest.mark.parametrize('default', [-1, '4', 2.5, True])
def test_array_limit_must_be_a_whole_number(modulepath, workdir, monkeypatch, default):
    from src.job_array import write_array
    monkeypatch.setitem(CONFIG['cluster'], 'array_limit', default)
    write_files(workdir, {'c1/a.inp': '! PAL4\n'})
    with pytest.raises(ValueError, match='array_limit .* must be a whole number'):
        write_array('orca', ['c1/a.inp'], '', {}, array_choices())
    assert not (workdir / 'c1' / 'job-slurm.sh').exists()


def test_array_option_without_an_array():
    from src.job_array import no_array_option
    no_array_option({'partition': 'long'})
    with pytest.raises(ValueError, match='array=%2 is for job arrays'):
        no_array_option({'array': '%2'})


def test_same_folder_needs_the_tag_in_the_template(modulepath, workdir, monkeypatch, tmp_path):
    from src import write_job_file as wjf
    from src.job_array import write_array
    template = tmp_path / 'job.sh'
    template.write_text(open(wjf.DEFAULT_TEMPLATE).read().replace('{job_tag}', ''))
    monkeypatch.setattr(wjf, 'template_file', lambda: str(template))
    monkeypatch.setattr('src.job_array.template_file', lambda: str(template))
    write_files(workdir, {'a.inp': '! PAL4\n', 'b.inp': ''})
    with pytest.raises(ValueError, match=r'no \{job_tag\}, needed by --same-folder'):
        write_array('orca', ['a.inp', 'b.inp'], '', {}, array_choices(same_folder=True))
    assert not (workdir / 'job-slurm-a.sh').exists()


def test_programs_are_the_toml_files_of_the_cluster_folder(tmp_path):
    assert set(read_definitions()) == set(INPUTS)
    d = make_config(tmp_path / 'other', '[cluster]\nmax_cpu = 8\n')
    (d / 'calculators' / 'mytool.toml').write_text('command = "mytool {input}"\n')
    assert read_definitions(str(d / 'calculators')) == {'mytool': {'command': 'mytool {input}'}}


def test_a_new_program(modulepath, workdir, monkeypatch):
    monkeypatch.setitem(calculators.DEFINITIONS, 'mytool', {
        'command': 'mytool {input} {args} > {output}', 'output': '{stem}.log',
        'module': 'openmpi', 'cpu': {'option': '-n'}})
    assert 'mytool' in CALCULATORS
    (workdir / 'a.in').write_text('')
    assert CALCULATORS['mytool'].get_cpu_memory('a.in', 'mytool a.in -n 6', False) == (6, 1000)
    write_job_file('a.in', 'mytool', 'mytool a.in -n 6', {'ntasks': 6})
    assert 'mytool a.in -n 6 > a.log\nSTATUS=$?' in (workdir / 'job-slurm.sh').read_text()


def test_unknown_key_is_an_error():
    with pytest.raises(ValueError, match=r'calculator orca: unknown keys cpu.optoin'):
        Calculator('orca', {'command': 'orca {input}', 'cpu': {'optoin': '-n'}})
    with pytest.raises(ValueError, match='"command" is missing'):
        Calculator('mytool', {'module': 'mytool'})
    with pytest.raises(ValueError, match='"modules" is replaced by "requires"'):
        Calculator('mytool', {'command': 'mytool {input}', 'modules': ['mytool']})


def config_folders():
    """The test configuration, $SLOTTER_DIR of whoever runs the tests and
    the folders in clusters/ (not in git)."""
    from conftest import LOCAL_SLOTTER_DIR
    found = [os.path.dirname(f) for f in glob.glob(os.path.join(REPO_DIR, 'clusters', '*', 'setup.toml'))]
    if LOCAL_SLOTTER_DIR and os.path.isfile(os.path.join(LOCAL_SLOTTER_DIR, 'setup.toml')):
        found.append(os.path.abspath(os.path.expanduser(LOCAL_SLOTTER_DIR)))
    return [CONFIG_DIR] + sorted(set(found) - {CONFIG_DIR})


@pytest.mark.parametrize('folder', config_folders())
def test_every_definition_is_valid(folder):
    # Also checks the configuration of this installation
    config = load_cluster_config(os.path.join(folder, 'setup.toml'))
    assert set(config) == {'cluster', 'slurm', 'email', 'modules'}, 'unknown section in setup.toml'
    for name, spec in read_definitions(os.path.join(folder, 'calculators')).items():
        Calculator(name, spec)
    # job.sh of the folder, or the default one
    from src.write_job_file import read_template, template_file
    read_template(template_file(folder))


def test_the_examples_are_valid():
    config = load_cluster_config(os.path.join(REPO_DIR, 'clusters', 'setup.example.toml'))
    defaults = load_cluster_config(SETUP_FILE)
    assert {k: set(v) for k, v in config.items()} == {k: set(v) for k, v in defaults.items()}

    with open(os.path.join(REPO_DIR, 'clusters', 'calc.example.toml'), 'rb') as f:
        example = Calculator('example', tomllib.load(f))
    assert example.command == '$(which orca) {input} {args} > {output}'
    assert example.success == ['ORCA TERMINATED NORMALLY'] and example.failure == ['ERROR !!!']


# --- outcome of the calculation --------------------------------------------

def run_outcome(calc, tmp_path, output, status):
    """Run the outcome lines of the job; return $OUTCOME and the README.md."""
    (tmp_path / 'README.md').write_text('# calc\n')
    if output is not None:
        (tmp_path / 'mol.out').write_text(output)
    script = f'STATUS={status}\n' + outcome_commands(CALCULATORS[calc], 'mol.out', readme=True)
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path, capture_output=True, text=True, check=True)
    return result.stdout.split(' (exit code')[0], (tmp_path / 'README.md').read_text()


@pytest.mark.parametrize('output, status, completed', [
    ('...\n****ORCA TERMINATED NORMALLY****\n', 0, True),
    ('ERROR !!!\n****ORCA TERMINATED NORMALLY****\n', 0, False),
    ('SCF NOT CONVERGED\naborting the run\n', 1, False),
    (None, 1, False),
])
def test_outcome_from_the_output(tmp_path, output, status, completed):
    outcome, readme = run_outcome('orca', tmp_path, output, status)
    assert outcome == ('CALCULATION COMPLETED CORRECTLY' if completed else 'CALCULATION NOT COMPLETED')
    assert f'--------------------\n{outcome}\n--------------------\n' in readme
    # When it did not end well, the end of the output says why
    assert ('aborting the run' in readme) == (output is not None and not completed and status == 1)


@pytest.mark.parametrize('status, completed', [(0, True), (1, False)])
def test_outcome_from_the_exit_code(tmp_path, status, completed):
    # xtb has no success text: the exit code decides
    outcome, _ = run_outcome('xtb', tmp_path, 'whatever\n', status)
    assert (outcome == 'CALCULATION COMPLETED CORRECTLY') == completed


def test_update_readme_for_jobs_written_by_older_versions(tmp_path):
    (tmp_path / 'README.md').write_text('# calc\n')
    (tmp_path / 'mol.log').write_text(' Normal termination of Gaussian 16\n')
    env = {**os.environ, 'SLOTTER_PYTHON_REEXEC': '1'}
    subprocess.run([sys.executable, os.path.join(REPO_DIR, 'update_readme.py'), 'gaussian', 'mol.log'],
                   cwd=tmp_path, env=env, check=True, capture_output=True)
    assert 'CALCULATION COMPLETED CORRECTLY' in (tmp_path / 'README.md').read_text()


# --- README parsing chosen by the user -------------------------------------

def test_parse_output_off_skips_the_readme_update(modulepath, workdir, monkeypatch):
    assert '>> README.md' in make_job('orca', workdir)
    monkeypatch.setitem(USER_CONFIG, 'readme', {'parse_input': True, 'parse_output': False})
    job = make_job('orca', workdir)
    assert '>> README.md' not in job
    assert 'OUTCOME="CALCULATION COMPLETED CORRECTLY"' in job   # still in the email subject


def test_parse_input_off_writes_only_the_command(workdir, monkeypatch):
    from src.initiate_readme import write_md
    (workdir / 'mol.inp').write_text('! B3LYP Opt Freq PAL4\n')

    write_md('orca', 'mol.inp', 'orca mol.inp')
    assert 'Geometry optimisation' in (workdir / 'README.md').read_text()

    monkeypatch.setitem(USER_CONFIG, 'readme', {'parse_input': False, 'parse_output': True})
    write_md('orca', 'mol.inp', 'orca mol.inp')
    readme = (workdir / 'README.md').read_text()
    assert 'orca mol.inp' in readme
    assert 'Geometry optimisation' not in readme


def test_readme_description(workdir):
    (workdir / 'mol.inp').write_text('! B3LYP OptTS Freq PAL4\n%geom maxstep 0.1 end\n')
    assert CALCULATORS['orca'].parse_input_for_readme('mol.inp', 'orca mol.inp') == (
        '! B3LYP OptTS Freq PAL4',
        '1. Transition State optimisation\n2. Reduced step to 0.1 a.u.\n3. Frequency calculation')

    (workdir / 'mol.gjf').write_text('%chk=std.chk\n# opt=modredundant freq b3lyp/6-31g(d)\n')
    assert CALCULATORS['gaussian'].parse_input_for_readme('mol.gjf', 'gaussian mol.gjf') == (
        '# opt=modredundant freq b3lyp/6-31g(d)', '1. Constrained optimisation\n2. Frequency calculation')

    (workdir / 'mol.xyz').write_text('1\n\nH 0 0 0\n')
    assert CALCULATORS['crest'].parse_input_for_readme('mol.xyz', 'crest mol.xyz --cinp c.inp --nci -T 4 ') == (
        'crest mol.xyz --cinp c.inp --nci -T 4',
        '1. Constrained conformational search\n2. Considering NCI ellipsoid')


# --- scripts run with the cluster Python -----------------------------------

def test_cluster_python_reads_only_the_cluster_section(tmp_path):
    f = tmp_path / 'cluster.toml'
    f.write_text('# python = "/no/comment"\n'
                 '[email]\npython = "/no/email"\n'
                 '[cluster]\n# python = "/no/commented"\nscratch = "/scratch"\n'
                 "python = '/opt/py/bin/python'   # cluster Python\n"
                 '[slurm]\npython = "/no/slurm"\n')
    assert cluster_python(str(f)) == '/opt/py/bin/python'


def test_cluster_python_agrees_with_the_toml_reader():
    assert cluster_python(SETUP_FILE) == load_cluster_config(SETUP_FILE)['cluster']['python']


def run_with_bootstrap(tmp_path, python, env=None):
    """Run a small script that calls use_cluster_python(); return its output."""
    fake = tmp_path / 'fakepython'
    fake.write_text('#!/bin/sh\necho "REEXEC $@"\n')
    fake.chmod(0o755)
    cluster = make_config(tmp_path / 'slotter', f'[cluster]\npython = "{python}"\n')
    script = tmp_path / 'script.py'
    script.write_text(f'import sys\nsys.path.insert(0, {REPO_DIR!r})\n'
                      'from src.bootstrap import use_cluster_python\n'
                      'use_cluster_python()\n'
                      'print("NOT REEXEC")\n')
    full_env = {k: v for k, v in os.environ.items() if k != 'SLOTTER_PYTHON_REEXEC'}
    full_env.update({'SLOTTER_DIR': str(cluster), **(env or {})})
    result = subprocess.run([sys.executable, str(script), 'a', 'b'], env=full_env,
                            capture_output=True, text=True, check=True)
    return result.stdout.strip(), script


def test_script_reruns_with_the_cluster_python(tmp_path):
    out, script = run_with_bootstrap(tmp_path, tmp_path / 'fakepython')
    assert out == f'REEXEC {script} a b'


@pytest.mark.parametrize('python', [sys.executable, '/nonexistent/bin/python'])
def test_no_rerun_with_the_same_or_a_missing_python(tmp_path, python):
    assert run_with_bootstrap(tmp_path, python)[0] == 'NOT REEXEC'


def test_no_second_rerun(tmp_path):
    out, _ = run_with_bootstrap(tmp_path, tmp_path / 'fakepython', {'SLOTTER_PYTHON_REEXEC': '1'})
    assert out == 'NOT REEXEC'


def test_old_python_without_the_cluster_python_stops_clearly(tmp_path):
    old = '/usr/bin/python3'
    check = subprocess.run([old, '-c', 'import sys; print(sys.version_info < (3, 11))'],
                           capture_output=True, text=True) if os.path.exists(old) else None
    if not check or check.stdout.strip() != 'True':
        pytest.skip('no system python3 older than 3.11')
    cluster = make_config(tmp_path / 'slotter', '[cluster]\npython = "/nonexistent/bin/python"\n')
    env = {k: v for k, v in os.environ.items() if k != 'SLOTTER_PYTHON_REEXEC'}
    env['SLOTTER_DIR'] = str(cluster)
    result = subprocess.run([old, os.path.join(REPO_DIR, 'job.py'), 'orca', 'mol.inp'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert '(/nonexistent/bin/python) is not available here' in result.stderr
    assert 'needs 3.11 or later' in result.stderr


def test_job_py_started_by_an_old_python(tmp_path):
    # A system Python without tomllib (e.g. 3.9) must hand over to the cluster Python
    old = '/usr/bin/python3'
    if not os.path.exists(old) or os.path.realpath(old) == os.path.realpath(sys.executable):
        pytest.skip('no other system python3 to start job.py with')
    # A copy of the test configuration (setup.toml and the programs), with this Python
    cluster = tmp_path / 'slotter'
    shutil.copytree(CONFIG_DIR, cluster)
    (cluster / 'setup.toml').write_text(open(SETUP_FILE).read().replace(
        load_cluster_config(SETUP_FILE)['cluster']['python'], sys.executable))
    calc = tmp_path / 'calc'
    calc.mkdir()
    (calc / 'mol.inp').write_text('! B3LYP def2-SVP Opt PAL4\n')
    env = {k: v for k, v in os.environ.items() if k not in ('SLOTTER_PYTHON_REEXEC', 'MODULEPATH')}
    env['SLOTTER_DIR'] = str(cluster)

    subprocess.run([old, os.path.join(REPO_DIR, 'job.py'), 'orca', 'mol.inp', '--write'],
                   cwd=calc, env=env, check=True, capture_output=True)
    assert f'{sys.executable} {REPO_DIR}/email_sender.py' in (calc / 'job-slurm.sh').read_text()


# --- scripts in .scripts/ --------------------------------------------------

@pytest.mark.parametrize('name', ['fluo', 'phospho'])
def test_scripts_import_slotter(name, tmp_path):
    pytest.importorskip('cclib')
    pytest.importorskip('periodictable')
    # Each script in its own process, as when it is run (both define the same
    # periodictable table, which can be created only once per process)
    path = os.path.join(REPO_DIR, '.scripts', f'{name}.py')
    code = ('import importlib.util, sys\n'
            f'spec = importlib.util.spec_from_file_location("{name}", {path!r})\n'
            'script = importlib.util.module_from_spec(spec)\n'
            'spec.loader.exec_module(script)\n'
            'from src.write_job_file import write_job_file\n'
            'assert script.write_job_file is write_job_file\n')
    subprocess.run([sys.executable, '-c', code], check=True, cwd=tmp_path)


# --- CPU limits ------------------------------------------------------------

def test_orca_reads_pal_not_other_digits(workdir):
    (workdir / 'mol.inp').write_text('! B3LYP def2-SVP Opt PAL4\n')
    assert CALCULATORS['orca'].get_cpu_memory('mol.inp', '', False) == (4, 16000)


def test_orca_pal_on_a_later_line(workdir):
    (workdir / 'mol.inp').write_text('# comment\n! B3LYP def2-SVP Opt PAL8\n')
    assert CALCULATORS['orca'].get_cpu_memory('mol.inp', '', False)[0] == 8


def test_orca_cpu_limit_keeps_the_case_of_the_input(workdir):
    (workdir / 'mol.inp').write_text('! B3LYP def2-SVP PAL32\n* xyzfile 0 1 Mol.xyz\n')
    assert CALCULATORS['orca'].get_cpu_memory('mol.inp', '', False)[0] == 16
    assert (workdir / 'mol.inp').read_text() == '! B3LYP def2-SVP PAL16\n* xyzfile 0 1 Mol.xyz\n'


def test_orca_force_keeps_the_requested_cpus(workdir):
    (workdir / 'mol.inp').write_text('! B3LYP PAL32\n')
    assert CALCULATORS['orca'].get_cpu_memory('mol.inp', '', True)[0] == 32
    assert (workdir / 'mol.inp').read_text() == '! B3LYP PAL32\n'


def test_gaussian_cpu_limit_keeps_the_case_of_the_input(workdir):
    (workdir / 'mol.gjf').write_text('%chk=Mol.chk\n%NProcShared=32\n%mem=8GB\n# opt\n')
    assert CALCULATORS['gaussian'].get_cpu_memory('mol.gjf', '', False) == (16, 8000)
    assert (workdir / 'mol.gjf').read_text() == '%chk=Mol.chk\n%NProcShared=16\n%mem=8GB\n# opt\n'


def test_crest_cpus_from_t_not_from_other_options(workdir):
    # --opt also ends with "t": it used to be read as the CPU option
    assert CALCULATORS['crest'].get_cpu_memory('mol.xyz', 'crest mol.xyz --opt vtight -T 8', False) == (8, 10000)


def test_cpu_option_must_be_a_number(workdir):
    with pytest.raises(ValueError, match='not "vtight"'):
        CALCULATORS['crest'].get_cpu_memory('mol.xyz', 'crest mol.xyz -T vtight', False)
    with pytest.raises(ValueError, match='-T needs the number of CPUs'):
        CALCULATORS['crest'].get_cpu_memory('mol.xyz', 'crest mol.xyz -T', False)


def test_molcas_cpus_with_a_zero(workdir):
    (workdir / 'mol.input').write_text('* MOLCAS_NPROCS = 10\n* MOLCAS_MEM = 2000\n')
    assert CALCULATORS['molcas'].get_cpu_memory('mol.input', 'molcas mol.input', False) == (10, 2000)


@pytest.mark.parametrize('mem, mb', [('%mem=8GB', 8000), ('%Mem=500MB', 500), ('%mem=100MW', 800),
                                     ('%mem=250000000', 2000)])
def test_gaussian_memory_units(workdir, mem, mb):
    # Without a unit Gaussian reads words (8 bytes)
    (workdir / 'mol.gjf').write_text(f'%nprocshared=2\n{mem}\n# opt\n')
    assert CALCULATORS['gaussian'].get_cpu_memory('mol.gjf', '', False) == (2, mb)


# --- email and interactive menu --------------------------------------------

def test_email_without_address_or_server(tmp_path, monkeypatch, capsys):
    from email_sender import send_mail
    send_mail('None', str(tmp_path), '123')
    monkeypatch.setitem(CONFIG['email'], 'smtp_server', '')
    send_mail('me@example.org', str(tmp_path), '123')
    out = capsys.readouterr().out
    assert 'No email address' in out and 'No SMTP server configured' in out


def test_email_subject_has_the_outcome(tmp_path, monkeypatch):
    import email_sender
    sent = []
    class FakeSMTP:
        def __init__(self, server): pass
        def sendmail(self, sender, to, text): sent.append(text)
    monkeypatch.setattr(email_sender.smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setitem(CONFIG['email'], 'smtp_server', 'localhost:25')
    (tmp_path / 'README.md').write_text('# calc\n')
    email_sender.send_mail('me@example.org', str(tmp_path), '123', 'CALCULATION NOT COMPLETED')
    email_sender.send_mail('me@example.org', str(tmp_path), '124')
    assert 'Subject: Calculation of 123: CALCULATION NOT COMPLETED\n' in sent[0]
    assert 'Subject: Calculation of 124\n' in sent[1]
    # Several calculations in one folder: the README of one of them
    (tmp_path / 'README-a.md').write_text('# calc a\n')
    email_sender.send_mail('me@example.org', str(tmp_path / 'README-a.md'), '125')
    assert '# calc a\n' in sent[2]


def test_interactive_choices(modulepath, monkeypatch):
    from src.run_interactive import get_choises
    monkeypatch.setitem(CONFIG['modules'], 'interactive', ['orca', 'conda'])
    assert get_choises(None) == ['conda/grimmelab', 'conda/nwchem', 'orca/5.0.4', 'orca/6.1.1']

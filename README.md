# slotter: computational chemistry jobs on SLURM

`job.py` prepares and submits computational chemistry jobs on SLURM, for any
program described by a TOML file. For each calculation it:

1. reads the input and works out the CPUs and memory to ask SLURM for;
2. writes a `README.md` describing the calculation;
3. writes `job-slurm.sh`, which loads the right modules and works in the scratch of the node;
4. submits the job with `sbatch`.

- [Installation](#installation)
- [Usage](#usage)
- [What the job does](#what-the-job-does)
- [Your own programs](#your-own-programs)
- [Your own scripts](#your-own-scripts)
- [Configuration](#configuration)
- [Tests](#tests)

---

## Installation

### On a cluster (once, by an administrator)

1. Clone the repository in a folder visible from the login and compute nodes:
   ```bash
   git clone <url> /opt/slotter
   ```
2. Choose a Python 3.11 or later (it needs `tomllib`) and install the
   dependencies in it. `pip install .` installs only the dependencies: slotter
   runs from its folder.
   ```bash
   /path/to/python -m pip install .                # InquirerPy, for job.py run
   /path/to/python -m pip install ".[scripts]"     # also numpy, scipy, cclib, periodictable, for .scripts/
   ```
3. Create the configuration folder, anywhere (e.g. `/data/slotter`):
   ```
   /data/slotter/
   ├── setup.toml          # settings of the cluster
   ├── job.sh              # optional: template of job-slurm.sh, instead of assets/job.sh
   └── calculators/        # one file per program: <program>.toml makes job.py <program> available
   ```
   - `setup.toml` from [`clusters/setup.example.toml`](clusters/setup.example.toml),
     with the Python of step 2 in `[cluster] python` (see
     [Cluster configuration](#cluster-configuration));
   - one `calculators/<program>.toml` per installed program, see
     [Your own programs](#your-own-programs).
4. Give the folder and slotter to all users, e.g. in `/etc/profile.d/slotter.sh`:
   ```bash
   export SLOTTER_DIR=/data/slotter
   export PATH=/opt/slotter:$PATH
   ```
5. Check it: `python3 -m pytest` in the repository also validates the folder in `$SLOTTER_DIR`.

The scripts start with `#!/usr/bin/env python3`, so any system `python3` (3.6
is enough) starts them: first of all they read `[cluster] python` and, if it
is another interpreter, they re-run themselves with it (`src/bootstrap.py`).
The configuration folder is not part of the repository; if you keep it inside
it, use `clusters/<name>/`, which is in `.gitignore`.

### For each user (optional)

- `~/.sbatchrc`: personal SLURM options, e.g. the address for the end-of-job
  email (see [`~/.sbatchrc`](#sbatchrc));
- `~/.config/slotter.toml`: personal preferences, from
  [`user.toml.example`](user.toml.example) (see [User configuration](#user-configuration)).

Without them everything works, with no email and every option on.

---

## Usage

One calculation per folder: go into the folder with the input and run

```bash
job.py <program> <input> [program options] [key=value ...] [--write] [--force] [--no-check] [--no-qm-all]
job.py <program> <input> <input> ... [same options] [--same-folder]      # job array
```

| Argument | Meaning |
|---|---|
| `<program>` | one of the programs of the cluster (`job.py -h` lists them), or `run` |
| `<input>` | input file, in the current folder; more than one, or one in another folder, make a [job array](#several-calculations-job-arrays) |
| program options | passed as they are to the program, e.g. `-T 8` |
| `version=6.1.1` | version of the module of the program (default: the default version of the module); `--version=6.1.1` works too |
| `key=value` | any other SLURM option, e.g. `partition=long`, `time=2-00:00:00`, `mail-user=me@unibo.it` |
| `--write` | writes `job-slurm.sh` and `README.md` without submitting the job |
| `--force` | does not apply the CPU limit of the cluster (see below) |
| `--no-check` | submits even if the folder already holds `.out` or `.log` files (see [Folder already used](#folder-already-used)) |
| `--no-qm-all` | at the end, leaves all the files in the folder instead of moving them to `qm_all/` |
| `--same-folder` | job array: the inputs stay in their folder, even several in one, instead of getting a folder each |

`--force`, `--write`, `--no-check`, `--no-qm-all` and `--same-folder` are for
slotter, written in full (no abbreviations): any other option goes to the program.
`--no-check` and `--no-qm-all` act on one calculation; to turn them off for
every calculation, or for every calculation of a program, see
[User configuration](#user-configuration).

Examples:

```bash
job.py orca opt.inp                          # default version, CPUs from the input
job.py orca opt.inp version=6.0.1            # a specific version
job.py orca opt.inp partition=long           # extra SLURM option
job.py crest mol.xyz -T 8 --gfn2             # CPUs from an option, passed to the program
job.py orca opt.inp --write                  # check job-slurm.sh before submitting it
```

Anything with `=` is for slotter and never reaches the program: `version=`
(or `--version=`) is the version of the module, anything else becomes
`#SBATCH --key=value` (dashes are optional). Everything else goes to the
program as it is: give program options with a space (`-T 8`, not `-T=8`), and
note that `--version 3.0.2`, with a space, is passed to the program.

### CPUs, memory and CPU limit

CPUs and memory are read from the input or from the options, as the
[definition of the program](#your-own-programs) says, and become
`#SBATCH --ntasks` and `#SBATCH --mem`. Without CPUs in the options or in the
input, 1 CPU is asked for. For the programs with `limit = true`, if the CPUs
exceed `max_cpu` of the cluster the input is **corrected** to the maximum
allowed; with `--force` it is left as it is. Only the number changes: the rest
of the file, file names included, stays the same.

### Versions

Without `version=` the default version of the module is loaded (`module load orca`).
`version=` applies to the module of the program (`module` in its definition),
not to the modules it requires (e.g. `job.py xtb mol.xyz -P 4 version=6.6.1`
loads `conda/grimmelab xtb/6.6.1`). Slotter checks that the version exists
**before** writing any file; if it does not, it stops:

```
ERROR: Required version of orca (7.0) is missing. Available: 5.0.4, 6.0.1, 6.1.0, 6.1.1
```

The available versions are also listed by `module avail <program>`. A program
without a module of its own (e.g. one that only uses a conda environment,
through `requires`) stops with `ERROR: <program> has no module to apply version=… to`.

### Folder already used

If the folder already holds `.out` or `.log` files, slotter submits
nothing: it creates a new folder `../<name>.<n>` with the original inputs
(from `.originals/`) and the current input, where to run again. With
`--no-check`, or `check_folder = false` in the
[user configuration](#user-configuration), the folder is not checked and the
job is submitted there.

### Several calculations: job arrays

With more than one input, or one in another folder, `job.py` submits a single
SLURM job array, one task per input. The inputs are the files right after the
program with the same extension (the first other word goes to the program,
with the rest); options and `key=value` apply to all of them.

```bash
job.py orca conf_*/opt.inp                   # one task per folder
job.py orca *.inp                            # each input gets a folder of its own
job.py orca *.inp --same-folder              # all in this folder
job.py crest conf_*/mol.xyz -T 8 array=%4    # at most 4 tasks at a time
```

| Inputs | Each calculation |
|---|---|
| in other folders (`conf_*/opt.inp`) | runs in its folder, which must hold no other file with the extension of the input |
| in the current folder (`*.inp`) | is moved to `<name>/` with the files of the same name (`a.inp`, `a.xyz`, `a.gbw` go to `a/`); the other files are copied to every folder, except other inputs and the files of slotter |
| in one folder, `--same-folder` | stays there, even with other inputs: its files get its name (`job-slurm-a.sh`, `README-a.md`, `.originals-a`), `update` and `live` the job id of its task (`./update-<job id>`, `./live-<job id>`), and there is no `qm_all/` |

Every calculation gets its own `job-slurm.sh` and `README.md`, as with
`job.py` alone, so it can also be submitted again by itself (`cd conf_3 &&
sbatch job-slurm.sh`). `job-array.sh`, in the current folder, is what is
submitted: task *i* runs the job of calculation *i* in its folder. Then:

- SLURM gives every task the same resources: the largest CPUs and memory among
  the inputs (the command of each one still uses its own, e.g. `PAL4`);
- the `.slurm` files of the tasks are in the current folder, as
  `out-<array>_<task>-<node>.slurm`;
- each calculation sends its own email, with its outcome;
- `array=%4` runs at most 4 tasks at a time; `scancel <array>` stops them all.

Nothing is moved or written if something is wrong: an input with others in
its folder, outputs already there (unless `--no-check`), a missing module
version, or, with `--same-folder`, a program that always writes the same
output (e.g. `crest.out`). `same_folder = true` in the
[user configuration](#user-configuration) makes `--same-folder` the default.

### `job.py run`: any command

```bash
job.py run
```

Asks interactively for the modules to load (taken from `MODULEPATH`, conda
environments included), CPUs, memory and the command to run, then submits it
with the same scheme as the other calculations. Handy for a one-off command;
for a program used often, [write its definition](#your-own-programs).

---

## What the job does

`job-slurm.sh` is written from the template
[`assets/job.sh`](assets/job.sh), or from `$SLOTTER_DIR/job.sh` if the cluster
has its own (see [`clusters/README.md`](clusters/README.md#job-template)). With
the default template, in order:

1. creates the scratch folder `<scratch>/slurm-<job id>` on every node of the job;
2. saves the original inputs in `.originals/` and copies the folder to the
   scratch with `rsync`: hidden files too (e.g. `.censo2rc`), but not
   `README.md`, `job-*.sh`, `*.slurm`, `update`, `live`, `.originals` and
   `qm_all/` of a previous run. If the copy fails (e.g. a full scratch) the
   job stops at once;
3. writes two commands in the calculation folder, useful while the job runs:
   - `./live`: follows the output in real time (`tail -f` on the node);
   - `./update`: copies the current output from the node to the folder;
4. loads the modules: `module purge`, then `module load …`. If loading fails
   the job stops at once with `ERROR: cannot load modules: …`. `module list`,
   `PATH` and `LD_LIBRARY_PATH` are printed in `out-<job id>-<node>.slurm`, so
   it is always known which versions the calculation used;
5. runs the `pre` lines of the program, then the calculation. The template
   sets no variable: threads (`OMP_NUM_THREADS`, `MKL_NUM_THREADS`), stack
   (`OMP_STACKSIZE`, `ulimit -s`) and the rest are in `pre` of
   `calculators/<program>.toml`;
6. moves everything to `qm_all/` and keeps only the main files in the folder
   (input, output, geometries, ...), unless `qm_all` is off;
7. copies the results from the scratch back to the calculation folder and deletes the scratch;
8. checks whether the calculation ended well (`success`, `failure` or the
   exit code of the program) and, if `parse_output` is on, appends the outcome
   to `README.md`: `CALCULATION COMPLETED CORRECTLY`, or
   `CALCULATION NOT COMPLETED` followed by the last 10 lines of the output.
   The output is not parsed any further: energies, frequencies and the rest are in the output;
9. sends `README.md` by email, if `mail-user` is set, with the outcome in the subject
   (`Calculation of <job id>: CALCULATION COMPLETED CORRECTLY`).

---

## Your own programs

A program is a TOML file, no Python: `$SLOTTER_DIR/calculators/<program>.toml`
makes `job.py <program>` available. To add one:

1. Copy [`clusters/calc.example.toml`](clusters/calc.example.toml), which
   uses every key, or start from a short one like this:
   ```toml
   # $SLOTTER_DIR/calculators/crest.toml
   abbreviation = "s"                    # job name: s-<input>
   requires = ["conda/grimmelab"]        # modules loaded first
   # module = "crest"                    # module of the program: version= applies to it
   output = "crest.out"
   keep = ["xyz", "out"]                 # <input>.<ext> kept out of qm_all/
   command = "crest {input} {args} > {output} 2> crest.error"
   pre = """
   export OMP_NUM_THREADS={cpu}
   export OPENBLAS_NUM_THREADS=1
   ulimit -s unlimited
   """
   success = "CREST terminated normally" # without it, the exit code decides

   [cpu]
   option = ["-T", "--T", "--threads"]   # CPUs from "-T 8"; or input = '<regex>' to read them from the input

   [memory]
   default = 10000                       # MB; or input = '<regex>' to read it from the input
   ```
2. Fill in what the program needs:
   - `command`: how the calculation is run, with the placeholders `{input}`,
     `{stem}` (input without extension), `{args}` (options after the input),
     `{output}`, `{cpu}` and `{mem}`;
   - `pre`: threads, stack and any other environment, since the job template
     sets none. With MPI (one process per CPU) the threads are 1; with threads
     only, `{cpu}`;
   - `[cpu]` and `[memory]`: where to read CPUs and memory, so that SLURM is
     asked for what the calculation uses;
   - `success`/`failure`: texts in the output that tell whether it ended well;
   - `[readme]` (optional): what to write in the `README.md` of the calculation.

   Every key is described in [`clusters/README.md`](clusters/README.md); an
   unknown key (e.g. a typo) stops `job.py` with a message.
3. Try it without submitting anything, and read the job:
   ```bash
   job.py crest mol.xyz -T 8 --write
   cat job-slurm.sh
   ```
4. Check the folder: `python3 -m pytest` validates every definition in
   `$SLOTTER_DIR`. To cover the program with the tests of the repository too,
   add the file to `tests/config/calculators/` and an input to `INPUTS` in
   `tests/test_slotter.py`.

---

## Your own scripts

For a workflow that `job.py` alone does not cover (several calculations, one
after the other, or inputs written from a previous output), write a Python
script that uses slotter as a library: it writes `job-slurm.sh` with the same
modules, scratch, outcome and email as `job.py`.

```python
#!/usr/bin/env python3
import os
import subprocess
import sys

# The folder of slotter, to import src/
sys.path.insert(0, '/opt/slotter')

if __name__ == '__main__':
    # Re-run with [cluster] python of $SLOTTER_DIR/setup.toml, before the imports that need it
    from src.bootstrap import use_cluster_python
    use_cluster_python()

from src.calculators import CALCULATORS
from src.initiate_readme import write_md
from src.parser_input import read_sbatchrc
from src.write_job_file import write_job_file


def submit(program, input_file, options='', before='', after='', **slurm):
    """Submit <program> on <input_file> (in the current folder), like job.py."""
    calc_cmd = f'{program} {input_file} {options}'
    cpu, mem = CALCULATORS[program].get_cpu_memory(input_file, calc_cmd, False)
    sbatch = {**read_sbatchrc(), 'ntasks': cpu, 'mem': mem, **slurm}
    write_job_file(input_file, program, calc_cmd, slurm_cmd=sbatch,
                   command_1=before, command_2=after)
    write_md(program, input_file, calc_cmd)
    subprocess.run(['sbatch', 'job-slurm.sh'], check=True)


if __name__ == '__main__':
    for folder in sys.argv[1:]:
        os.chdir(folder)
        submit('orca', 'opt.inp', partition='long')
        os.chdir('..')
```

| `write_job_file` argument | Meaning |
|---|---|
| `input_file`, `calculation` | input and program, as on the `job.py` command line |
| `calc_cmd` | `"<program> <input> [options]"`: the options become `{args}` |
| `slurm_cmd` | `#SBATCH` options; `ntasks` and `mem` (MB) are the CPUs and memory; `version` chooses the module version |
| `command_1`, `command_2` | shell lines before `pre` and after `post` of the program, in the scratch |
| `qm_all` | `False`: no `qm_all/` (default: the user's choice) |
| `tag` | `-<name>` when several calculations share the folder: it goes in the names of their files |
| `job_file` | name of the job (default `job-slurm.sh`) |

To chain calculations, submit the next job at the end of this one, from
`command_2` (`$SLURM_SUBMIT_DIR` is the calculation folder):

```python
after = 'cd $SLURM_SUBMIT_DIR/../step_2 && sbatch --dependency=afterok:$SLURM_JOB_ID job-slurm.sh'
```

A wrong program or module version raises `ValueError`, before any file is written.

`.scripts/` holds examples of such scripts: `fluo.py` and `phospho.py` (chains
of Gaussian calculations) and `supporting.py`, which collects final energies
and geometries of several outputs. The folder is in `.gitignore`, so new
scripts there stay personal; their extra dependencies are installed with
`pip install ".[scripts]"`.

---

## Configuration

The configuration has two levels.

| Level | File | Contains | Edited by |
|---|---|---|---|
| Cluster | folder `$SLOTTER_DIR` | paths, programs and everything that does not depend on the user | administrators |
| User | `~/.config/slotter.toml` | only personal preferences: `README.md`, folder check, `qm_all/` | each user |

On top of that, each user can have personal SLURM options in `~/.sbatchrc`.

### Cluster configuration

`$SLOTTER_DIR/setup.toml`. Without `SLOTTER_DIR`, or without `setup.toml` in
it, `job.py` stops with a message. The job passes `SLOTTER_DIR` to the scripts
it runs on the node, so they read the same configuration. All keys are
optional (template: [`clusters/setup.example.toml`](clusters/setup.example.toml)):

| Section and key | Meaning | Default |
|---|---|---|
| `[cluster] python` | Python of slotter: `job.py` and the other scripts re-run themselves with it, and the jobs use it for `email_sender.py` | the running Python |
| `[cluster] scratch` | scratch folder on the nodes | `/scratch` |
| `[cluster] max_cpu` | max CPUs for the programs with `limit = true` (unless `--force`) | `16` |
| `[cluster] sbatchrc` | file with the personal SLURM options | `~/.sbatchrc` |
| `[slurm]` | `#SBATCH` options in every job (`nodes`, `error`, `output`, ...) | `nodes = "1-1"`, ... |
| `[email] smtp_server`, `sender` | server and sender of the end-of-job emails; no server, no email | empty |
| `[modules] modulepath` | modulefile folders; empty = `$MODULEPATH` | empty |
| `[modules] purge` | `module purge` before loading the modules of the job | `true` |
| `[modules] interactive` | modules offered by `job.py run` (empty = all) | empty |

Slotter finds modules and versions in `MODULEPATH`, whatever the layout of
the modulefile folder (e.g. `core/`, `apps/`, `envs/`, `group/`). Hidden files
(`.version`, `.common.tcl`, ...) are never taken as modules.

### User configuration

`~/.config/slotter.toml` (or the file given by `SLOTTER_USER_CONFIG`). Start
from [`user.toml.example`](user.toml.example):

```toml
[readme]
parse_input = true      # describe the input (commands, kind of calculation) in README.md
parse_output = true     # at the end of the job, write in README.md whether the calculation ended well

[readme.crest]          # choice for a single program, wins over the general one
parse_output = false

[job]
check_folder = true     # stop if the folder already holds .out or .log files
qm_all = true           # at the end, move the files to qm_all/
same_folder = false     # job array of inputs in one folder: keep them there

[job.crest]             # choice for a single program
qm_all = false
```

- `parse_input = false`: `README.md` holds only the command that was run.
- `parse_output = false`: the job does not update `README.md` at the end (the outcome is still in the email subject).
- `check_folder = false`: the job is submitted even if the folder already holds `.out` or `.log` files.
- `qm_all = false`: all the files stay in the folder at the end of the job.
- `same_folder = true`: the inputs of a [job array](#several-calculations-job-arrays) in one folder stay there.

For a single calculation, `--no-check` and `--no-qm-all` on the command line
win over this file.

Without the file, everything is on. Any other section is ignored with a
warning: the cluster settings cannot be changed from here.

### `~/.sbatchrc`

SLURM options added to every job, one per line:

```
#SBATCH --mail-user=name.surname@unibo.it
#SBATCH --mail-type=END
#SBATCH --partition=long
```

Empty lines and comments are ignored. The options on the command line
(`key=value`) win over those of `.sbatchrc`. Without the file,
`--mail-type=NONE` is used.

---

## Tests

```bash
python3 -m pip install pytest
python3 -m pytest
```

The tests use the configuration in `tests/config/` (whatever `SLOTTER_DIR`
says), a fake modulefile tree, and do not read `~/.config/slotter.toml`.
Among other things they check that `job-slurm.sh` is valid (`bash -n`) for
every program, and that the folder in your `SLOTTER_DIR` (and any
`clusters/<name>/`) is valid.

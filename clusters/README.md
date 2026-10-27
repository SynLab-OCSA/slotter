# Configuration folder

The configuration of a cluster is a folder, anywhere, given by the
`SLOTTER_DIR` environment variable. It is not part of the repository (inside
it, `clusters/<name>/` is in `.gitignore`):

- `setup.toml`: settings of the cluster (Python, scratch, SLURM, email,
  modules), from [`setup.example.toml`](setup.example.toml) and described in
  the [main README](../README.md#cluster-configuration);
- `calculators/<program>.toml`: a program that runs with `job.py <program>`,
  from [`calc.example.toml`](calc.example.toml);
- `job.sh` (optional): template of `job-slurm.sh`, see [Job template](#job-template).

This file describes the definitions of the programs. They are plain data:
`src/calculators.py` reads them, and adding or changing a program needs no
Python. A program exists on a cluster only if `calculators/` has the matching
file. `python3 -m pytest` checks the folder in `SLOTTER_DIR`.

A key slotter does not know (e.g. a typo) stops `job.py` with a message:
`ERROR: calculator orca: unknown keys cpu.optoin`.

## Keys

| Key | Meaning | Default |
|---|---|---|
| `abbreviation` | prefix of the job name (`o-mol`) | the name |
| `requires` | modules loaded first, as they are written (e.g. `conda/grimmelab`, `openmpi/4.1.8`) | none |
| `module` | module of the program, loaded last: `version=` on the command line applies to it; without it, `version=` is an error | none |
| `output` | output file | none |
| `keep` | extensions of the `<input>.<ext>` files kept out of `qm_all/` (empty = no `qm_all/`) | empty |
| `command` | command running the calculation (required) | |
| `pre`, `post` | shell lines before the calculation (in the scratch, after the modules) and at the end of the job. The job template sets no variable: threads, stack and any other environment of the program go in `pre` | empty |
| `success` | text, or list of texts, that must **all** be in the output if the calculation ended well | |
| `failure` | text, or list of texts, that must **not** be there | |

Without `success`, the exit code of the program decides the outcome.

In `output`, `command`, `pre` and `post` these placeholders are replaced:

| Placeholder | Value |
|---|---|
| `{input}` | input file (`mol.inp`) |
| `{stem}` | input without extension (`mol`) |
| `{args}` | options written after the input on the `job.py` command line (if empty, the space before it goes too) |
| `{output}` | the `output` file |
| `{cpu}`, `{mem}` | CPUs and memory asked to SLURM |

Any other braces (e.g. `${ext[@]}`) are left as they are.

### `[cpu]`

| Key | Meaning |
|---|---|
| `option` | option, or list of options, followed by the number of CPUs: `-T 8` (`-T=8` would be a SLURM option) |
| `consume` | `true`: the option is for slotter only (e.g. `mpirun -np {cpu}`) and does not go in `{args}` |
| `input` | regex, or list of regexes, searched in the input: the first group is the number; the first one that matches counts |
| `limit` | `true`: above `max_cpu` of the cluster the input is corrected (only the number), unless `--force` |
| `default` | CPUs when nothing is found (1) |

### `[memory]`

| Key | Meaning |
|---|---|
| `input` | regex searched in the input: first group the number, second (optional) the unit |
| `unit` | unit of the number when the regex finds none (`mb`) |
| `default` | MB when nothing is found (1000) |
| `per_cpu` | `true`: the memory found is per CPU (e.g. `%maxcore` of ORCA) |

Units: `b`, `kb`, `mb`, `gb`, `tb`, and in 8-byte words `w`, `kw`, `mw`, `gw`, `tw`.

### `[readme]`

What to write in the `README.md` of the calculation, unless the user set
`parse_input = false`.

| Key | Meaning |
|---|---|
| `command` | regex of the input lines shown as the commands (e.g. the `!` lines of ORCA); without it, the command that was run |
| `describe` | list of rules `{ match, text }`: if `match` finds something, `text` goes in the numbered list |

In a rule:

- `match` can be a list: all of them must find something;
- `{1}`, `{2}`, ... in `text` are the groups of the last regex;
- `source = "command"` searches the command line instead of the input;
- among the rules with the same `group`, only the first that matches counts (e.g. TS, constrained or plain optimisation).

## Example

[`calc.example.toml`](calc.example.toml) uses every key. A short one:

```toml
# $SLOTTER_DIR/calculators/crest.toml
abbreviation = "s"
requires = ["conda/grimmelab"]
output = "crest.out"
keep = ["xyz", "out"]
command = "crest {input} {args} > {output} 2> crest.error"
pre = "export OPENBLAS_NUM_THREADS=1"
success = "CREST terminated normally"

[cpu]
option = ["-T", "--T", "--threads"]

[memory]
default = 10000
```

## Job template

`job-slurm.sh` is written from [`assets/job.sh`](../assets/job.sh). To change
it on a cluster (e.g. how the scratch is made), copy it to
`$SLOTTER_DIR/job.sh`: that file wins over the default one.

These placeholders are replaced; any other brace (`${VAR}`, `{ ...; }`) is
left as it is, so the template is plain bash:

| Placeholder | Value | Required |
|---|---|---|
| `{sbatch}` | `#SBATCH` lines: job name, `.sbatchrc`, options of `job.py` | yes |
| `{modules}` | `module purge` and `module load` | yes |
| `{pre}` | `pre` of the program (exports, ...) | yes |
| `{command}` | the calculation: alone on its line, followed by the line `STATUS=$?` | yes |
| `{qm_all}` | moves the files to `qm_all/` (empty when it is off) | yes |
| `{outcome}` | sets `$OUTCOME`, and writes it in `README.md` | yes |
| `{email}` | sends the notification email | yes |
| `{post}` | `post` of the program | yes |
| `{scratch}` | `[cluster] scratch` of `setup.toml` | no |
| `{output}` | output file (empty for `job.py run`) | no |
| `{cpu}`, `{mem}` | CPUs and memory asked to SLURM | no |
| `{command_quoted}` | the calculation, quoted for `echo` | no |
| `{tag}` | `-<input name>` when several calculations share the folder (`--same-folder`), else empty: it goes in the name of `.originals` | for `--same-folder` |
| `{job_tag}` | `-$SLURM_JOB_ID` when several calculations share the folder, else empty: it goes in the names of `update` and `live` | for `--same-folder` |

In a job array each calculation has its own `job-slurm.sh`, written from
this template: the task runs it from the folder of the calculation, with
`SLURM_SUBMIT_DIR` set to that folder. With `--same-folder` the README of each
calculation is `README{tag}.md`, and the template does not copy `README*.md`
to the scratch (so the copy back never overwrites the one being edited).

If a required placeholder is missing, `job.py` stops with a message, e.g.
`ERROR: /data/slotter/job.sh: missing {email}`: a template without them would
lose the calculation, its outcome or the email. `python3 -m pytest` checks the
template of the folder in `SLOTTER_DIR` too.

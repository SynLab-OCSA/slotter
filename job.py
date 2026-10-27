#!/usr/bin/env python3

if __name__ == '__main__':
    # Re-run with "python" of $SLOTTER_DIR/setup.toml before the
    # imports that need it: see src/bootstrap.py
    from src.bootstrap import use_cluster_python
    use_cluster_python()

import os
import sys

from src.parser_input import parse_args, prepare
from src.write_job_file import write_job_file
from src.initiate_readme import write_md
from src.check import check_folder
from src.run_interactive import interactive
from src.job_array import is_array, no_array_option, write_array

def main():

    files, calculation, options, slurm_cmd, write, choices = parse_args()

    if calculation == 'run':
        module, input_file, calc_cmd, slurm_cmd = interactive(slurm_cmd)
        write_job_file(input_file=input_file, calculation=calculation, slurm_cmd=slurm_cmd, calc_cmd=calc_cmd,
                       module=module, run=True, qm_all=choices['qm_all'])
        job = 'job-slurm.sh'
    elif is_array(files):
        # Several inputs, or one in another folder: one task per calculation
        job = write_array(calculation, files, options, slurm_cmd, choices)
        print("Take your time to edit the README.md of each calculation to explain its purpose")
    else:
        no_array_option(slurm_cmd)
        input_file = files[0]
        calc_cmd, slurm_cmd = prepare(calculation, input_file, options, slurm_cmd, choices['force'])
        if choices['check_folder'] and check_folder(input_file):
            return
        # The job file first: it checks the modules, so a wrong version leaves no README behind
        write_job_file(input_file=input_file, calculation=calculation, slurm_cmd=slurm_cmd, calc_cmd=calc_cmd,
                       qm_all=choices['qm_all'])
        write_md(calculation, input_file, calc_cmd)
        job = 'job-slurm.sh'
        print("Take your time to edit the README.md file to explain the purpose of this calculation")

    if not write:
        os.system(f'sbatch {job}')


if __name__ == '__main__':

    try:
        main()
    except (ValueError, OSError) as e:
        # e.g. a missing input, module or module version: a message, not a traceback
        sys.exit(f'ERROR: {e}')

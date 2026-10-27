#!/usr/bin/env python3

if __name__ == '__main__':
    # Re-run with "python" of $SLOTTER_DIR/setup.toml before the
    # imports that need it: see src/bootstrap.py
    from src.bootstrap import use_cluster_python
    use_cluster_python()

import os
import sys

from src.parser_input import parse_args
from src.write_job_file import write_job_file
from src.initiate_readme import write_md
from src.check import check_folder
from src.run_interactive import interactive

def main():
    
    input_file, calculation, calc_cmd, slurm_cmd, write, choices = parse_args()

    module = []

    if calculation == 'run':

        module, input_file, calc_cmd, slurm_cmd = interactive(slurm_cmd)
    elif choices['check_folder'] and check_folder(input_file):
        return

    # The job file first: it checks the modules, so a wrong version leaves no README behind
    write_job_file(input_file=input_file, calculation=calculation, slurm_cmd=slurm_cmd, calc_cmd=calc_cmd, module=module,
                   run=calculation=='run', qm_all=choices['qm_all'])

    if calculation != 'run':
        write_md(calculation, input_file, calc_cmd)

    print("Take your time to edit the README.md file to explain the purpose of this calculation")

    if not write:
        os.system('sbatch job-slurm.sh')
    

if __name__ == '__main__':

    try:
        main()
    except ValueError as e:
        # e.g. a missing module or module version: a message, not a traceback
        sys.exit(f'ERROR: {e}')


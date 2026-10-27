#!/usr/bin/env python3
"""Outcome of the calculation in README.md, for jobs written by older versions.

Those jobs end with "update_readme.py <calculation> <output>". New jobs check
the outcome themselves (outcome_commands() in src/write_job_file.py): this runs
the same check, and can be removed once no such job is left.
"""

if __name__ == '__main__':
    # Re-run with "python" of $SLOTTER_DIR/setup.toml before the
    # imports that need it: see src/bootstrap.py
    from src.bootstrap import use_cluster_python
    use_cluster_python()

import subprocess
import sys

from src.calculators import CALCULATORS
from src.write_job_file import outcome_commands

if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit('Usage: update_readme.py <calculation_type> <output_file>')
    calculation, output = sys.argv[1:3]
    if calculation not in CALCULATORS:
        sys.exit(f"Calculator '{calculation}' not found. Exiting.")
    # The exit code of the program is not known here: STATUS=0, the output decides
    subprocess.run(['bash', '-c', 'STATUS=0\n' + outcome_commands(CALCULATORS[calculation], output, readme=True)])

#!/usr/bin/env python3

import argparse, os, sys

from src.config import CONFIG, user_choice
from src.calculators import CALCULATORS, POSS_CALC

def is_slurm(k):
    if '=' in k: return k

def parse_args():
    # No abbreviations: an unknown option goes to the program, it must not be
    # taken for one of these (e.g. "--no" for "--no-check")
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('calc', choices=POSS_CALC)
    parser.add_argument('file', nargs='?', default='None')
    parser.add_argument('--force', action="store_true", default=False)
    parser.add_argument('--write', action="store_true", help="Write the sbatch file only")
    parser.add_argument('--no-check', action="store_true",
                        help="Do not stop if the folder already holds .out or .log files")
    parser.add_argument('--no-qm-all', action="store_true", help="Do not move the files to qm_all/ at the end")

    args, unknown = parser.parse_known_args()
    # The command line wins over ~/.config/slotter.toml ([job])
    choices = {
        'check_folder': not args.no_check and user_choice('job', 'check_folder', args.calc),
        'qm_all': not args.no_qm_all and user_choice('job', 'qm_all', args.calc),
    }
    slurm_commands = read_sbatchrc()

    for i in filter(is_slurm, unknown):
        slurm_commands[i.split('=')[0].strip('-')] = i.split('=')[1] 

    if args.calc != 'run':
        if not os.path.exists(os.path.join(os.getcwd(), args.file)):
            raise IOError('Input file does not exist')

        sl = list(filter(is_slurm, unknown))
        calc_commands = f'{args.calc} {args.file} ' + ' '.join([i for i in unknown if i not in sl])

        calc_obj = CALCULATORS[args.calc]
        cpu, mem = calc_obj.get_cpu_memory(args.file, calc_commands, args.force)

        slurm_commands['ntasks'] = cpu
        slurm_commands['mem'] = mem
    else: 
        calc_commands = ''

    return args.file, args.calc, calc_commands, slurm_commands, args.write, choices

def read_sbatchrc(path=None):
    """#SBATCH options of the user's .sbatchrc, as {key: value}.

    Lines are "#SBATCH --key=value", "#SBATCH --key value" or "#SBATCH --flag"
    (value None); any other line is ignored. Without the file: --mail-type=NONE.
    """
    rc_path = os.path.expanduser(path or CONFIG['cluster']['sbatchrc'])
    if not os.path.exists(rc_path):
        return {'mail-type': 'NONE'}

    options = {}
    with open(rc_path) as f:
        for line in f:
            words = line.split()
            if len(words) < 2 or words[0] != '#SBATCH' or not words[1].startswith('-'):
                continue
            key, eq, value = words[1].lstrip('-').partition('=')
            if not eq:
                value = words[2] if len(words) > 2 and not words[2].startswith('#') else None
            options[key] = value
    return options

if __name__ == '__main__':
    print(parse_args())
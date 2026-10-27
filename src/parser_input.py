#!/usr/bin/env python3

import argparse, os, sys

from src.config import CONFIG, user_choice
from src.calculators import CALCULATORS, POSS_CALC

def is_slurm(k):
    if '=' in k: return k

def input_files(words):
    """Inputs at the start of `words`: existing files with the extension of the
    first one (conf_*/mol.inp gives a job array). They are returned with the
    words left, which go to the program."""
    files = []
    for word in words:
        if '=' in word or not os.path.isfile(word):
            break
        if files and os.path.splitext(word)[1] != os.path.splitext(files[0])[1]:
            break
        files.append(word)
    return files, words[len(files):]

def parse_args():
    # No abbreviations: an unknown option goes to the program, it must not be
    # taken for one of these (e.g. "--no" for "--no-check")
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('calc', choices=POSS_CALC)
    parser.add_argument('files', nargs='*', metavar='file', help="Input file(s): more than one, or one in another folder, make a job array")
    parser.add_argument('--force', action="store_true", default=False)
    parser.add_argument('--write', action="store_true", help="Write the sbatch file only")
    parser.add_argument('--no-check', action="store_true",
                        help="Do not stop if the folder already holds .out or .log files")
    parser.add_argument('--no-qm-all', action="store_true", help="Do not move the files to qm_all/ at the end")
    parser.add_argument('--same-folder', action="store_true",
                        help="Job array: keep the inputs of this folder here, instead of a folder for each")

    args, unknown = parser.parse_known_args()
    files, words = input_files(args.files)
    unknown = words + unknown
    # The command line wins over ~/.config/slotter.toml ([job])
    choices = {
        'check_folder': not args.no_check and user_choice('job', 'check_folder', args.calc),
        'qm_all': not args.no_qm_all and user_choice('job', 'qm_all', args.calc),
        'same_folder': args.same_folder or user_choice('job', 'same_folder', args.calc),
        'force': args.force,
    }
    slurm_commands = read_sbatchrc()

    for i in filter(is_slurm, unknown):
        slurm_commands[i.split('=')[0].strip('-')] = i.split('=')[1]

    if args.calc != 'run':
        if not files:
            raise IOError(f'Input file does not exist: {unknown[0] if unknown else "none given"}')
        options = ' '.join(i for i in unknown if not is_slurm(i))
    else:
        options = ''

    return files, args.calc, options, slurm_commands, args.write, choices

def prepare(calculation, input_file, options, slurm_commands, force=False):
    """Command line of one calculation, and its #SBATCH options with the CPUs
    and memory read from the input (which may be in another folder)."""
    calc_commands = f'{calculation} {os.path.basename(input_file)} {options}'
    cpu, mem = CALCULATORS[calculation].get_cpu_memory(input_file, calc_commands, force)
    return calc_commands, {**slurm_commands, 'ntasks': cpu, 'mem': mem}

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

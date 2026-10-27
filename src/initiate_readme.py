#!/usr/bin/env python3

import os
from src.calculators import CALCULATORS
from src.config import readme_parsing

def write_md(calculation, input_file, cmd_line):
    calc_obj = CALCULATORS.get(calculation)
    if not calc_obj:
        return None

    # The user can turn the input parsing off in ~/.config/slotter.toml ([readme] parse_input)
    if readme_parsing(calculation, 'parse_input'):
        cmd, parse_input = calc_obj.parse_input_for_readme(input_file, cmd_line)
    else:
        cmd, parse_input = cmd_line, ''

    text = calc_obj.md_template.format(
        directory = os.getcwd(), 
        command = cmd,
        parsed_input = parse_input, 
    )

    with open('README.md', 'w') as f:
        f.write(text)
    
    return None

if __name__=='__main__':
    import sys
    calculation, input_file, cmd_line = sys.argv[1:]
    write_md(calculation, input_file, cmd_line)
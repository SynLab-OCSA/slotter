#!/usr/bin/env python3


import re
from os import listdir, getcwd, mkdir, system
from os.path import isfile, join, isdir, split

def check_folder(input_file):

    output = [f for f in listdir(getcwd()) if isfile(join(getcwd(), f)) and (str(f).endswith('out') or str(f).endswith('log'))]

    if not output:
        return False

    print('Output file(s) are present in this folder. Use one folder for one calculation.')

    dir_n = split(getcwd())[-1].split('.')[0]
    root = split(getcwd())[0]
    n = len([f for f in listdir(root) if isdir(join(root, f)) and re.search(dir_n, f)])

    n_dir = dir_n+'.'+str(n)
    print(f"Creating ../{n_dir} with input files stored in .originals/ and the new modified input")

    mkdir(join(root, n_dir))
    system(f'cp .originals/* ../{n_dir}')
    system(f'cp {input_file} ../{n_dir}')
    system(f'cp .originals/{input_file} .')

    return True


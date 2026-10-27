from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.validator import NumberValidator, PathValidator

from src.config import CONFIG
from src import modules as env_modules



def get_choises(_):
    # Modules found in MODULEPATH (conda environments included, e.g. conda/enan_1.1),
    # limited to the names listed in setup.toml [modules] interactive
    allowed = CONFIG['modules']['interactive']
    return [m for m in env_modules.available() if not allowed or m.split('/')[0] in allowed]


def interactive(slurm_cmd):

    modules = inquirer.checkbox('Select which module(s) has to be loaded:',
                                choices=get_choises, validate = lambda result: len(result)>0, invalid_message="should be at least 1 selection", instruction="(select at least 1)",).execute()

    cpu = inquirer.text('Number of CPUs to allocate:', validate=NumberValidator()).execute()
    mem = inquirer.text('Memory to allocate [MB]:', validate=NumberValidator()).execute()

    calc_cmd = inquirer.text(message="Enter the command to be executed:").execute()
    input_file = None

    slurm_cmd['ntasks'] = cpu
    slurm_cmd['mem'] = mem

    return modules, input_file, calc_cmd, slurm_cmd

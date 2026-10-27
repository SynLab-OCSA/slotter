#!/bin/bash
{sbatch}

SCRATCH_DIR="{scratch}/slurm-$SLURM_JOB_ID"

echo "Job execution cmd:   "{command_quoted}
echo "Job execution start: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Slurm Job ID is:     $SLURM_JOB_ID"
echo "Slurm Job name is:   $SLURM_JOB_NAME"
echo
echo "SLURM_JOB_NODELIST:  $SLURM_JOB_NODELIST"
echo "SLURM_SUBMIT_DIR:    $SLURM_SUBMIT_DIR"
echo "SLURM_SUBMIT_HOST:   $SLURM_SUBMIT_HOST"
echo "SLURMD_NODENAME:     $SLURMD_NODENAME"
echo
echo "SCRATCH DIR:         $SCRATCH_DIR"
echo


# CREATION OF ALL THE DIRECTORIES NEEDED ON EACH NODE

for node in $(scontrol show hostnames $SLURM_JOB_NODELIST)
do
        echo $node
        ssh $node mkdir -p $SCRATCH_DIR
done


# SETTING THE FOLDER FOR THE CALCULATION

## CREATE THE ORIGINAL'S FOLDER
mkdir .originals{tag}
cp -r * .originals{tag}
rm -f .originals{tag}/README*.md .originals{tag}/*slurm .originals{tag}/*sh


## COPYING THE FILES OF THE DIRECTORY IN THE SCRATCH
cp -v -r $SLURM_SUBMIT_DIR/* $SCRATCH_DIR


echo "scp $SLURMD_NODENAME:$SCRATCH_DIR/{output} $SLURM_SUBMIT_DIR" > $SLURM_SUBMIT_DIR/update{job_tag}
echo "ssh $SLURMD_NODENAME tail -f -n 1000 $SCRATCH_DIR/{output}" > $SLURM_SUBMIT_DIR/live{job_tag}
# echo "ssh $SLURMD_NODENAME cat $SCRATCH_DIR/{output} | less " > $SLURM_SUBMIT_DIR/list
chmod ug+x $SLURM_SUBMIT_DIR/update{job_tag} $SLURM_SUBMIT_DIR/live{job_tag} # $SLURM_SUBMIT_DIR/list

cd $SCRATCH_DIR
# Not copied back: the README.md can be edited while the job runs, and
# update/live of the calculations sharing the folder are removed by their own job
rm -f README*.md update live update-* live-*

# LOADING MODULES

{modules}

echo
echo "PATH:                $PATH"
echo "LD_LIBRARY_PATH:     $LD_LIBRARY_PATH"
echo

{pre}

# RUNNING THE CALCULATION

{command}
STATUS=$?

rm -f *.slurm *.sh
{qm_all}


# COPY BACK THE CALCULATION AND REMOVE THE SCRATCH FOLDER
cd $SLURM_SUBMIT_DIR
rsync -av --no-p --no-g --chmod=ugo=rwX --exclude '*.tmp' $SCRATCH_DIR/* ./

for node in $(scontrol show hostnames $SLURM_JOB_NODELIST)
do
    ssh $node rm -fr $SCRATCH_DIR
done


# OUTCOME: only whether the calculation ended well, the output is not parsed

{outcome}

# SEND NOTIFICATION EMAIL

{email}

{post}

rm $SLURM_SUBMIT_DIR/update{job_tag} $SLURM_SUBMIT_DIR/live{job_tag}


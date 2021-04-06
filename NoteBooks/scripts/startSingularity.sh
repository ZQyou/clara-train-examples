#!/bin/bash

# SPDX-License-Identifier: Apache-2.0

DOCKER_IMAGE=/fs/scratch/PZS0710/zyou/clara/train/clara-train-sdk_v3.1.sif

jnotebookPort=$1
AIAA_PORT=$2
#################################### check if parameters are empty
if [[ -z  $jnotebookPort ]]; then
    jnotebookPort=8890
fi
if [[ -z  $AIAA_PORT ]]; then
    AIAA_PORT=5000
fi

echo -----------------------------------
echo starting docker for ${DOCKER_IMAGE} using GPUS ${GPU_IDs} jnotebookPort ${jnotebookPort} and AIAA port ${AIAA_PORT}
echo -----------------------------------

extraFlag="--fakeroot --net "
cmd2run="/bin/bash"

extraFlag=${extraFlag}" --network-args "portmap=${jnotebookPort}:8890/tcp" --network-args "portmap=${AIAA_PORT}:80/tcp

echo starting please run "./installDashBoardInDocker.sh" to install the lab extensions then start the jupeter lab
echo once completed use web browser with token given yourip:${jnotebookPort} to access it

module purge
test ! -d $TMPDIR/compat && singularity exec ${DOCKER_IMAGE} cp -rp /usr/local/cuda/compat $TMPDIR/
#test ! -d ${PWD}/../jupyter && singularity exec ${DOCKER_IMAGE} cp -rp /usr/local/share/jupyter ${PWD}/../

singularity run ${extraFlag} \
  -B ${TMPDIR}/compat:/usr/local/cuda/compat \
  -B ${PWD}/../:/claraDevDay/ \
  -W /claraDevDay/scripts \
  --nv \
  ${DOCKER_IMAGE} \
  ${cmd2run}

echo -- exited from docker image

#!/bin/bash

# SPDX-License-Identifier: Apache-2.0

CLARA_IMAGE=/fs/scratch/PZS0710/zyou/clara/train/clara-train-sdk_v3.1.01.sif

cmd2run="/bin/bash"
echo starting please run "./startJupyterLabOnly.sh" to start the jupeter lab
echo once completed use web browser with token given yourip:${jnotebookPort} to access it

module purge
test ! -d $TMPDIR/compat && singularity exec ${DOCKER_IMAGE} cp -rp /usr/local/cuda/compat $TMPDIR/

#singularity run ${extraFlag} \

singularity exec --nv \
  -B ${TMPDIR}/compat:/usr/local/cuda/compat \
  -B ${PWD}/../:/claraDevDay/ \
  -W /claraDevDay/scripts \
  ${CLARA_IMAGE} \
  ${cmd2run}

echo -- exited from singularity image

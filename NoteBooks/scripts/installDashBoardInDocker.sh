#!/usr/bin/env bash

# SPDX-License-Identifier: Apache-2.0
#jupyter_home=$(pwd -P)/../jupyter
#if [ ! -d ${jupyter_home} ] ; then
#  python -m venv --without-pip ${jupyter_home}
#  source ${jupyter_home}/bin/activate
#  curl https://bootstrap.pypa.io/get-pip.py |python
#else
#  source ${jupyter_home}/bin/activate
#fi

#pip install jupyterlab==1.2.20 jupyter-console qtconsole ipywidgets jupytext jupyter-tensorboard


# https://stackoverflow.com/questions/2915471/install-a-python-package-into-a-different-directory-using-pip
# set PYTHONPATH=$PREFIX_PATH/lib/python2.6/site-packages pip freeze ?

export JUPYTERLAB_DIR=$(pwd -P)/../jupyter
export PATH=${JUPYTERLAB_DIR}/bin:$PATH
export PYTHONPATH=${JUPYTERLAB_DIR}/lib/python3.6/site-packages:${PYTHONPATH}
mkdir -p $JUPYTERLAB_DIR
# Need to ignore the installed packages because pip will try to uninstall tornado
pip install --prefix=${JUPYTERLAB_DIR} --ignore-installed --use-feature=2020-resolver --upgrade pip
pip install --prefix=${JUPYTERLAB_DIR} --ignore-installed --use-feature=2020-resolver jupyterlab-nvdashboard
jupyter labextension install jupyterlab-nvdashboard
echo ------jupyterlab intallation completed
nvidia-smi
echo ------------------

jupyter lab /claraDevDay --ip 0.0.0.0 --port 8890 --allow-root --no-browser

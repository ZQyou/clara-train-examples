#!/usr/bin/env bash

my_dir="$(dirname "$0")"
. $my_dir/set_env.sh

echo "MMAR_ROOT set to $MMAR_ROOT"

IMG_FOLDER=/CAMELYON16/RAW/testing/images
NPY_FOLDER=/CAMELYON16/RAW/testing/foregrounds_LV6

find $IMG_FOLDER -mindepth 1 -maxdepth 1 -type f | while read case
do
    imageID=`basename $case`
    imageID="${imageID%.*}"
    	
    echo -e "$imageID"

    img_path=$IMG_FOLDER/$imageID.tif
    npy_path=$NPY_FOLDER/$imageID.npy
    level=6
    
    now=$(date +"%T")
    echo "Current time : $now"
    python3 ${MMAR_ROOT}/custom/tissue_mask.py $img_path $npy_path --level $level
done




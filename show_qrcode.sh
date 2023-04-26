#!/bin/bash
#

# First version: provide the script with the names
# $1 serverkontext
# $2 client name
# next version will show a list of clients of which you can choose

[ -z "${1}" ] && exit 2
[ -z "${2}" ] && exit 2

if [ ! -e "configs/${1}/${2}.client.conf" ]; then
  echo "ERROR: client conf does not exist"
  exit
fi

qrencode -t ansiutf8 < configs/${1}/${2}.client.conf

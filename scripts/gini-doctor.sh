#!/bin/sh
# gini-doctor, from a checkout.
#
# The script itself lives in the gini-doctor distribution — doctor/src/gini_doctor/ — because it
# is shipped inside that wheel and a second copy here would be a second thing to keep in step.
# This wrapper exists so `sh scripts/gini-doctor.sh` keeps working from the repository, which is
# what every instruction in docs/ and scripts/README.md says to type.
#
#   sh scripts/gini-doctor.sh --help
#
# `exec` rather than a call: the real script's exit status is the number of failed checks, and
# the interactive menu needs the terminal.
set -u
_here=$(dirname "$0")
_real="$_here/../doctor/src/gini_doctor/gini-doctor.sh"
if [ ! -r "$_real" ]; then
    echo "gini-doctor: cannot find $_real — is this a full checkout?" >&2
    exit 2
fi
exec sh "$_real" "$@"

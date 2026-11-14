#!/bin/sh
set -eu
printf "%s\n" "$1" > ${RESULTS_DIR:-results/manual}/phase.next
mv ${RESULTS_DIR:-results/manual}/phase.next ${RESULTS_DIR:-results/manual}/phase

#!/bin/sh
# Runs a .blueql file through skysh inside the skytable container: one `skysh -e` per line
# (skysh has no script mode; each line is its own connection, so `use <space>` would not carry
# over and the files use space.model names). Lines starting with # are printed as headings.
# skysh prints an error as its bare code (e.g. 108); see the README for the ones used here.
#   sh /blueql/run.sh FILE [skysh options, e.g. --user app --password ...]
set -u
file=$1
shift
while IFS= read -r line || [ -n "$line" ]; do
	case $line in
	'') ;;
	'#'*) printf '\n%s\n' "$line" ;;
	*)
		printf 'skysh> %s\n' "$line"
		skysh "$@" -e "$line" || exit 1 # non-zero only when the client fails (connection, parse)
		;;
	esac
done <"$file"

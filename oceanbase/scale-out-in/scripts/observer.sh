#!/bin/bash
# Container entrypoint: run one observer in the foreground (-N), the way obd would start it
# (obd's start_pre plugin builds the same command line), but without obd or SSH.
# First start: lay out the home directory from the RPM files obd keeps in the image and
# pass the rootservice list (-r) so the three observers can be bootstrapped together
# (scripts/bootstrap.sh). Restart (`docker start`, `make failover`): the data directory
# and etc/observer.config.bin are already there, so the observer rejoins with its saved config.
set -euo pipefail

: "${OB_ZONE:?}" "${OB_RS_LIST:?}"
REPO=$(ls -d /root/.obd/repository/oceanbase-ce/*/[0-9a-f]*/ | head -1)
LIBS=$(ls -d /root/.obd/repository/oceanbase-ce-libs/*/[0-9a-f]*/ | head -1)
HOME_PATH=/root/ob
IP=$(hostname -i | awk '{print $1}')

mkdir -p $HOME_PATH/{etc,log,run,store/{clog,slog,sstable}}
cd $HOME_PATH
for d in bin admin; do [ -e $d ] || ln -s "$REPO$d" $d; done
[ -e lib ] || ln -s "$LIBS" lib
# timezone / SRS / help data and default parameters (read at bootstrap and tenant creation)
for f in "$REPO"etc/*; do [ -e "etc/$(basename "$f")" ] || cp "$f" etc/; done
export LD_LIBRARY_PATH=$HOME_PATH/lib


args=(-N -p 2881 -P 2882 -I "$IP" -z "$OB_ZONE" -n "${OB_CLUSTER_NAME:-obcluster}" -c "${OB_CLUSTER_ID:-1}"
	-d $HOME_PATH/store -l "${OB_SYSLOG_LEVEL:-WARN}")
if [ -z "$(ls -A $HOME_PATH/store/clog)" ]; then
	echo "observer.sh: first start in $OB_ZONE ($IP), rootservice list $OB_RS_LIST"
	args+=(-r "$OB_RS_LIST" -o "$OB_OPTS")
else
	echo "observer.sh: restart in $OB_ZONE ($IP) from existing data"
fi
echo "observer.sh: bin/observer ${args[*]}"
exec bin/observer "${args[@]}"

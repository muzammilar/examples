.PHONY: license up build run status down

# the q server needs a KDB-X license: KDB_LICENSE_B64 / KDB_LICENSE_K4B64 in the environment, or
# kc.lic / k4.lic in ../license (see ../README.md#license)
license:
	@[ -n "$$KDB_LICENSE_B64$$KDB_LICENSE_K4B64" ] || [ -f ../license/kc.lic ] || [ -f ../license/k4.lic ] || \
		{ echo 'no KDB-X license: export KDB_LICENSE_B64=<base64 kc.lic> or copy kc.lic to kdb-x/license/ (see kdb-x/README.md)'; exit 1; }

up: license
	docker compose up --detach --wait --build kdbx

# the C++ client image (k.h + c.o from KxSystems/kdb); needs no license
build:
	docker compose --profile run build showcase

# the showcase against the running server (override TRADES, QUOTES_PER_TRADE, SYMS, BATCH);
# each run replaces the tables, so it can be repeated
run:
	docker compose --progress quiet run --rm --build showcase

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

down:
	docker compose --profile run down --volumes --rmi local

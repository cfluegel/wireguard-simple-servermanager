#!/bin/bash
#
# Zeigt die Konfiguration eines Clients als QR-Code im Terminal an.
#
# Benötigt BEIDE Argumente:
#   $1 Serverkontext (Name aus server.txt)
#   $2 Clientname    (Name aus clients.txt)
#
# Gelesen wird die von generate.sh erzeugte Datei
#   configs/<SERVER>/<CLIENT>.client.conf

# Verfügbare Serverkontexte aus dem configs/-Verzeichnis auflisten
list_servers() {
  [ -d "configs" ] || return 0
  local found=0
  for d in configs/*/ ; do
    [ -d "$d" ] || continue
    [ $found -eq 0 ] && echo "" && echo "Available servers:" && found=1
    echo "  $(basename "$d")"
  done
}

# Verfügbare Clients eines Serverkontexts auflisten
list_clients() {
  [ -d "configs/${1}" ] || return 0
  local found=0
  for f in configs/"${1}"/*.client.conf ; do
    [ -e "$f" ] || continue
    [ $found -eq 0 ] && echo "" && echo "Available clients for server '${1}':" && found=1
    echo "  $(basename "$f" .client.conf)"
  done
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <SERVER> <CLIENT>

Show the WireGuard configuration of a client as a QR code.
Both arguments are required:

  SERVER   server context / name as used in server.txt  (e.g. wg-eu)
  CLIENT   client name as used in clients.txt           (e.g. alice)

Example: $(basename "$0") wg-eu alice

Reads configs/<SERVER>/<CLIENT>.client.conf, so run generate.sh first.
EOF
}

# -h/--help: Hilfe nach stdout, Exit 0
case "${1}" in
  -h|--help)
    usage
    list_servers
    exit 0
    ;;
esac

# Fehlende Argumente: Hilfe nach stderr, Exit 2.
# Ist der Serverkontext schon bekannt, zusätzlich dessen Clients auflisten.
if [ -z "${1}" ] || [ -z "${2}" ]; then
  if [ -z "${1}" ]; then
    echo "ERROR: missing argument SERVER (and CLIENT)" >&2
  else
    echo "ERROR: missing argument CLIENT" >&2
  fi
  echo "" >&2
  usage >&2
  if [ -n "${1}" ] && [ -d "configs/${1}" ]; then
    list_clients "${1}" >&2
  else
    list_servers >&2
  fi
  exit 2
fi

if ! command -v qrencode >/dev/null 2>&1 ; then
  echo "ERROR: 'qrencode' not found in PATH. Please install it first." >&2
  exit 1
fi

if [ ! -e "configs/${1}/${2}.client.conf" ]; then
  echo "ERROR: client conf does not exist: configs/${1}/${2}.client.conf" >&2
  if [ -d "configs/${1}" ]; then
    list_clients "${1}" >&2
  else
    echo "ERROR: unknown server context '${1}'" >&2
    list_servers >&2
  fi
  exit 1
fi

qrencode -t ansiutf8 < "configs/${1}/${2}.client.conf"

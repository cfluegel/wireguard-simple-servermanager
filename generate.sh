#!/bin/bash
#
# WireGuard Konfigurations-Generator
#
# Zweck
# - Erzeugt Server- und Client-Konfigurationsdateien aus Vorlagen
#   anhand der Eingabedateien `server.txt` und `clients.txt`.
# - Standardmäßig wird aller Traffic über den Tunnel geleitet (abhängig von den Vorlagen).
#
# Voraussetzungen
# - Bash, `sed`, `awk`, `wg` (WireGuard-Tools) müssen verfügbar sein.
# - Vorlagen im Verzeichnis `templates/`:
#   - `templates/server`       -> Basis-Serverkonfiguration
#   - `templates/server-peer`  -> Peer-Block für jeden Client im Server
#   - `templates/client`       -> Basis-Clientkonfiguration
#
# Eingabedateien (Semikolon-getrennt)
# - `server.txt` (eine Zeile pro Server):
#     NAME;ENDPOINT;INTERFACE;SERVER_ADDRESS/CIDR;NETWORK/CIDR;PORT;SERVER_PRIVATE_KEY;DEFAULT_TUNNEL_MODE
#   Beispiel:
#     wg-eu;vpn.example.com;wg0;10.0.0.1/24;10.0.0.0/24;51820;ABCDEF...=;split
#
# - `clients.txt` (eine Zeile pro Client):
#     CLIENT_IP/CIDR;CLIENT_NAME;CLIENT_PRIVATE_KEY;OPTIONALE_NETZE;TUNNEL_MODE
#   Hinweise:
#     - Das Feld `OPTIONALE_NETZE` ist optional; mehrere Netze können komma-separiert
#       angegeben werden (z. B. 192.168.0.0/16,172.16.0.0/12). Diese werden zu AllowedIPs
#       auf Serverseite ergänzt.
#   Beispiel:
#     10.0.0.2/32;alice;ABCDEF...=;192.168.0.0/16;split
#
# Ausgabe
# - Alle generierten Dateien werden unter `configs/` abgelegt.
# - Pro Server:
#     configs/<SERVERNAME>.server.conf
# - Pro Client (unterhalb des Server-Verzeichnisses):
#     configs/<SERVERNAME>/<CLIENTNAME>.client.conf
#
# Verhalten/Annahmen
# - Entfernt ein bestehendes `configs/`-Verzeichnis vollständig und erzeugt es neu.
# - Liest jeweils die vollständigen Dateien `server.txt` und `clients.txt` zeilenweise ein.
# - Aus Private Keys werden Public Keys via `wg pubkey` abgeleitet.
# - Exit-Code 2, falls `server.txt` oder `clients.txt` fehlen.
#
# Nutzung
#   chmod +x generate.sh
#   ./generate.sh
#
# Sicherheit
# - Die Dateien enthalten private Schlüssel. Bitte auf sichere Dateirechte und
#   sichere Ablage achten. Generierte Konfigurationen ggf. nur für berechtigte
#   Nutzer zugänglich machen.

# uses clients.txt to generated the client configurations
# defaults to redirect all traffic over the tunnel as I want to
# used it mostly for make public wifi great again (tm)

# Zielverzeichnis frisch anlegen (vorhandenes Verzeichnis wird gelöscht)
[ -e "configs" ] && rm -rf configs
[ -e "configs" ] || mkdir configs

## Abbruch, wenn Eingabedateien fehlen
[ -e "clients.txt" ] || exit 2
[ -e "server.txt" ] || exit 2


# Serverkonfiguration(en) generieren (eine pro Zeile in server.txt)
cat server.txt | while read line ; do
  SNAME=$(echo $line | awk -F";" '{ print $1 }')
  SEXTENDPOINT=$(echo $line | awk -F";" '{ print $2 }')
  SINT=$(echo $line | awk -F";" '{ print $3 }')
  SIP=$(echo $line | awk -F";" '{ print $4 }')
  SNETWORK=$(echo $line | awk -F";" '{ print $5 }')
  SPORT=$(echo $line | awk -F";" '{ print $6 }')
  SPRIVKEY=$(echo $line | awk -F";" '{ print $7 }')
  SDEFAULTMODE=$(echo $line | awk -F";" '{ print $8 }')
  SPUBKEY=$(echo $SPRIVKEY | wg pubkey)

  [ -e "configs/$SNAME/" ] || mkdir configs/$SNAME/

  echo "Generate Server Configuration for $SNAME"

  # Serverbasis aus Vorlage einfügen
  cat templates/server | \
	  sed "s|%SRVPRIVKEY%|$SPRIVKEY|" | \
	  sed "s|%SRVNETWORK%|$SNETWORK|g" | \
	  sed "s|%SRVADDRESS%|$SIP|g" | \
	  sed "s|%SRVINT%|$SINT|g" | \
	  sed "s|%SRVPORT%|$SPORT|" > configs/$SNAME.server.conf

  # Für jeden Client einen Peer-Block in die Serverkonfiguration anhängen
  cat clients.txt | while read line ; do
    CIP=$(echo $line | awk -F";" '{ print $1 }')
    CNAME=$(echo $line | awk -F";" '{ print $2 }')
    CPRIVKEY=$(echo $line | awk -F";" '{ print $3 }')
    CNETWORK="$(echo $line | awk -F";" '{ print $4 }')"
    [ ${#CNETWORK} -gt 0 ] && CNETWORK=",${CNETWORK}"
    CPUBKEY=$(echo $CPRIVKEY | wg pubkey)

    cat templates/server-peer | \
	    sed "s|%CLIENTPUBKEY%|$CPUBKEY|" | \
	    sed "s|%CLIENTNAME%|$CNAME|" | \
	    sed "s|%CLIENTIP%|$CIP|" | \
	    sed "s|%CLIENTNETWORKS%|$CNETWORK|" >> configs/$SNAME.server.conf
  done
  unset CIP
  unset CNAME
  unset CPRIVKEY

  echo ""
  # Clientkonfigurationen generieren (eine Datei pro Client)
  cat clients.txt | while read line ; do
    CIP=$(echo $line | awk -F";" '{ print $1 }')
    CNAME=$(echo $line | awk -F";" '{ print $2 }')
    CPRIVKEY=$(echo $line | awk -F";" '{ print $3 }')
    CNETWORK="$(echo $line | awk -F";" '{ print $4 }')"
    CMODE=$(echo $line | awk -F";" '{ print $5 }')
    MODE=$(echo "${CMODE:-$SDEFAULTMODE}" | tr 'A-Z' 'a-z')
    [ -z "$MODE" ] && MODE="split"
    if [ "$MODE" = "full" ]; then
      CALLOWEDIPS="0.0.0.0/0"
    else
      # Client AllowedIPs are based on server network only; OPTIONAL_NETWORKS are server-side only.
      CALLOWEDIPS="$SNETWORK"
    fi
    echo "Generate Client Configuration for $CNAME"

    # Clientbasis aus Vorlage einfügen
    cat templates/client |\
    	sed "s|%PRIVKEY%|$CPRIVKEY|" |\
	    sed "s|%SRVENDPOINT%|$SEXTENDPOINT|" | \
	    sed "s|%SRVPUBKEY%|$SPUBKEY|" | \
	    sed "s|%SRVPORT%|$SPORT|" | \
	    sed "s|%CLIENTIP%|$CIP|" | \
	    sed "s|%CLIENTALLOWEDIPS%|$CALLOWEDIPS|" > configs/$SNAME/$CNAME.client.conf

  done
done

#!/bin/bash
#

# uses clients.txt to generated the client configurations
# defaults to redirect all traffic over the tunnel as I want to
# used it mostly for make public wifi great again (tm)

[ -e "configs" ] && rm -rf configs
[ -e "configs" ] || mkdir configs

## stop if the config files are not present
[ -e "clients.txt" ] || exit 2
[ -e "server.txt" ] || exit 2


# generate the server config
cat server.txt|while read line ; do
  SNAME=$(echo $line | awk -F";" '{ print $1 }')
  SEXTENDPOINT=$(echo $line | awk -F";" '{ print $2 }')
  SINT=$(echo $line | awk -F";" '{ print $3 }')
  SIP=$(echo $line | awk -F";" '{ print $4 }')
  SPORT=$(echo $line | awk -F";" '{ print $5 }')
  SPRIVKEY=$(echo $line | awk -F";" '{ print $6 }')
  SPUBKEY=$(echo $SPRIVKEY | wg pubkey)

  [ -e "configs/$SNAME/" ] || mkdir configs/$SNAME/

  echo "Generate Server Configuration for $SNAME"

  cat templates/server | \
	  sed "s|%SRVPRIVKEY%|$SPRIVKEY|" | \
	  sed "s|%SRVNETWORK%|$SIP|g" | \
	  sed "s|%SRVINT%|$SINT|g" | \
	  sed "s|%SRVPORT%|$SPORT|" > configs/$SNAME.server.conf

  # Generate the peer configuration in the server config
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
  # generate client configurations
  cat clients.txt | while read line ; do
    CIP=$(echo $line | awk -F";" '{ print $1 }')
    CNAME=$(echo $line | awk -F";" '{ print $2 }')
    CPRIVKEY=$(echo $line | awk -F";" '{ print $3 }')
    echo "Generate Client Configuration for $CNAME"

    cat templates/client |\
    	sed "s|%PRIVKEY%|$CPRIVKEY|" |\
	sed "s|%SRVENDPOINT%|$SEXTENDPOINT|" | \
	sed "s|%SRVPUBKEY%|$SPUBKEY|" | \
	sed "s|%SRVPORT%|$SPORT|" | \
	sed "s|%CLIENTIP%|$CIP|" > configs/$SNAME/$CNAME.client.conf

  done
done


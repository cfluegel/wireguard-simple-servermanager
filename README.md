# wireguard-simple-servermanager
Simple Script to manage the configuration files of a simple wireguard server setup

## What is the goal 
I was fed up by how I ran my private wireguard server. I used it for accessing my private systems over 
unsecure networks, e.g. public wifi 

I occasionally add new clients to the network and it was tedious. Now I can generate all the configs 
(client and server) in one go and transfer the files to every place where it is necessary. 

The creation and preparation of the configuration files should be kept in a secure space. Like an encrypted 
hard disk space. 

## Usage
- Requirements: bash, sed, awk, and the WireGuard tools (`wg`) available in PATH.
- Templates required under `templates/`:
  - `templates/server`       – server base config; placeholders: `%SRVPRIVKEY%`, `%SRVADDRESS%`, `%SRVNETWORK%`, `%SRVINT%`, `%SRVPORT%`
  - `templates/server-peer`  – appended once per client; placeholders: `%CLIENTPUBKEY%`, `%CLIENTNAME%`, `%CLIENTIP%`, `%CLIENTNETWORKS%`
  - `templates/client`       – client base config; placeholders: `%PRIVKEY%`, `%SRVENDPOINT%`, `%SRVPUBKEY%`, `%SRVPORT%`, `%CLIENTIP%`, `%CLIENTALLOWEDIPS%`

### Prepare the input files
- Copy the samples and fill in your values:
  - `cp server.txt.sample server.txt`
  - `cp clients.txt.sample clients.txt`

### Formats
- `server.txt` (one line per server):
  - `NAME;ENDPOINT;INTERFACE;SERVER_ADDRESS/CIDR;NETWORK/CIDR;PORT;SERVER_PRIVATE_KEY;DEFAULT_TUNNEL_MODE`
  - `DEFAULT_TUNNEL_MODE` can be `split` (default) or `full`.
  - Example:
    - `wg-eu;vpn.example.com;wg0;10.0.0.1/24;10.0.0.0/24;51820;SERVER_PRIVATE_KEY_BASE64;split`

- `clients.txt` (one line per client):
  - `CLIENT_IP/CIDR;CLIENT_NAME;CLIENT_PRIVATE_KEY;OPTIONAL_NETWORKS;TUNNEL_MODE`
  - `OPTIONAL_NETWORKS` may be a comma-separated list (e.g. `192.168.0.0/16,172.16.0.0/12`) and is appended to AllowedIPs on the server side.
  - `TUNNEL_MODE` is optional; use `full` for `0.0.0.0/0` or `split` for `%SRVNETWORK%`. If empty, the server default is used.
  - Examples:
    - `10.0.0.2/32;alice;CLIENT_PRIVATE_KEY_BASE64;192.168.0.0/16;split`
    - `10.0.0.3/32;bob;CLIENT_PRIVATE_KEY_BASE64;;full`

### Generate configs
- Make the script executable and run it:
  - `chmod +x generate.sh`
  - `./generate.sh`

### Output
- All generated files are written to `configs/` (the folder will be recreated each run):
  - Per server: `configs/<SERVERNAME>.server.conf`
  - Per client: `configs/<SERVERNAME>/<CLIENTNAME>.client.conf`

### Security
- The input and generated files contain private keys. Keep them on encrypted storage and restrict file permissions appropriately.

## not finished 
- [] add a field to the client list that maps it to a specific server 
- [] secure the configurations files with some kind of encryption 

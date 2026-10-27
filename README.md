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
  - `templates/client`       – client base config; placeholders: `%PRIVKEY%`, `%SRVENDPOINT%`, `%SRVPUBKEY%`, `%SRVPORT%`, `%CLIENTIP%`, `%CLIENTDNS%`, `%CLIENTALLOWEDIPS%`

### Prepare the input files
- Either use the TUI (see *Managing servers and clients* below), or copy the samples and fill in your values by hand:
  - `cp server.txt.sample server.txt`
  - `cp clients.txt.sample clients.txt`

### Formats
- `server.txt` (one line per server):
  - `NAME;ENDPOINT;INTERFACE;SERVER_ADDRESS/CIDR;NETWORK/CIDR;PORT;SERVER_PRIVATE_KEY;DEFAULT_TUNNEL_MODE;DNS`
  - `DEFAULT_TUNNEL_MODE` can be `split` (default) or `full`.
  - `DNS` is optional; it sets the default DNS server for all clients of this server (falls back to `8.8.8.8`).
  - Example:
    - `wg-eu;vpn.example.com;wg0;10.0.0.1/24;10.0.0.0/24;51820;SERVER_PRIVATE_KEY_BASE64;split;10.0.0.1`

- `clients.txt` (one line per client):
  - `CLIENT_IP;CLIENT_NAME;CLIENT_PRIVATE_KEY;OPTIONAL_NETWORKS;TUNNEL_MODE;DNS;SERVER`
  - `CLIENT_IP` is a single address **without** a prefix (e.g. `10.0.0.2`) – `templates/client` and `templates/server-peer` append `/32` themselves.
  - `OPTIONAL_NETWORKS` may be a comma-separated list (e.g. `192.168.0.0/16,172.16.0.0/12`) and is appended to AllowedIPs on the server side.
  - `TUNNEL_MODE` is optional; use `full` for `0.0.0.0/0` or `split` for `%SRVNETWORK%`. If empty, the server default is used.
  - `DNS` is optional; it overrides the server's default DNS for this client (falls back to the server's `DNS`, then `8.8.8.8`).
  - `SERVER` is optional; it assigns the client to one server from `server.txt`. If empty, the client is included in *every* server's configuration (the original behaviour).
  - Examples:
    - `10.0.0.2;alice;CLIENT_PRIVATE_KEY_BASE64;192.168.0.0/16;split;9.9.9.9;wg-eu`
    - `10.0.0.3;bob;CLIENT_PRIVATE_KEY_BASE64;;full`

- In both input files, empty lines and lines starting with `#` are ignored, so you can comment out entries.

### Managing servers and clients
Editing the semicolon separated files by hand is error prone, so `wg-manager.py` provides a small
text UI for them:

- `./wg-manager.py`

It only needs **Python 3** – `curses` is part of the standard library, so there is no need to
install `dialog` or `whiptail`. `wg` is only required for generating keys; without it you can still
type keys in manually. If `server.txt` or `clients.txt` are missing, the tool offers to create them
(empty or from the `.sample` file).

What it does:
- add, edit and delete servers and clients, with validation before anything is written
  (duplicate names, IPs already in use, IPs outside the server network, bad ports, stray `;`)
- generate private keys via `wg genkey` and show the derived public key
- suggest the next free client IP from the server's network
- run `generate.sh` and show its output
- write atomically, keep a `.bak` of the previous version and `chmod 600` the files (they contain
  private keys)
- preserve your own comments and empty lines in both files

Keys:

| Key | Action |
|---|---|
| `↑` `↓` | select entry |
| `Enter` | on a server: show its clients |
| `a` / `e` / `d` | add / edit / delete entry |
| `g` | run `generate.sh` (asks first, since it recreates `configs/`) |
| `b` / `Esc` | back to the server list |
| `q` | quit |

In the add/edit form: `Tab` `↑` `↓` switch fields, `←` `→` change the mode and server fields,
`Ctrl-G` generates a new private key, `Ctrl-T` suggests a free IP, `F2` (or `Ctrl-S`) saves and
`Esc` cancels.

To check the data layer without starting the UI: `./wg-manager.py --selftest`.

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

### Show a client config as a QR code
- `./show_qrcode.sh <SERVER> <CLIENT>` – both arguments are required, e.g. `./show_qrcode.sh wg-eu alice`.
- Run it without arguments (or with `-h`) to see the usage text and a list of the servers and
  clients that currently exist under `configs/`. Needs `qrencode`.

## not finished 
- [x] add a field to the client list that maps it to a specific server 
- [] secure the configurations files with some kind of encryption 
- [] pick the client for show_qrcode.sh interactively from within the TUI

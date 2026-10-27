#!/usr/bin/env python3
"""
wg-manager -- kleine curses-Oberflaeche zum Verwalten von server.txt und clients.txt.

Verwendet ausschliesslich die Python-Standardbibliothek (curses gehoert dazu), es muss
also weder `dialog` noch `whiptail` installiert sein. `wg` wird nur zum Erzeugen von
Schluesseln benoetigt -- fehlt es, koennen Keys weiterhin manuell eingetragen werden.

Aufruf:
  ./wg-manager.py             Oberflaeche starten
  ./wg-manager.py --selftest  Datenschicht ohne Terminal pruefen
"""

import curses
import ipaddress
import os
import shutil
import subprocess
import sys
import tempfile
import termios

SERVER_FILE = "server.txt"
CLIENTS_FILE = "clients.txt"
GENERATE_SCRIPT = "generate.sh"

# Feldreihenfolge in server.txt (siehe Header-Kommentar in generate.sh)
S_NAME, S_ENDPOINT, S_INTERFACE, S_ADDRESS, S_NETWORK, S_PORT, S_PRIVKEY, S_MODE, S_DNS = range(9)
SERVER_NFIELDS = 9

# Feldreihenfolge in clients.txt
C_IP, C_NAME, C_PRIVKEY, C_NETWORKS, C_MODE, C_DNS, C_SERVER = range(7)
CLIENT_NFIELDS = 7

MODES = ["", "split", "full"]
MODE_LABELS = {"": "(server default)", "split": "split", "full": "full"}

MIN_COLS, MIN_LINES = 72, 18


# ---------------------------------------------------------------------------
# Datenschicht -- reine Funktionen, bewusst ohne curses, damit testbar
# ---------------------------------------------------------------------------

class Row:
    """Eine Zeile einer Eingabedatei: entweder Rohtext (Kommentar/Leerzeile) oder Felder."""

    __slots__ = ("raw", "fields")

    def __init__(self, raw=None, fields=None):
        self.raw = raw
        self.fields = fields

    @property
    def is_data(self):
        return self.fields is not None

    def get(self, idx):
        if self.fields is None or idx >= len(self.fields):
            return ""
        return self.fields[idx]


def is_skippable(line):
    """Leerzeile oder Kommentar -- dieselbe Regel wie is_skippable() in generate.sh."""
    stripped = line.strip()
    return stripped == "" or stripped.startswith("#")


def parse_line(line, nfields):
    """Semikolon-getrennte Zeile in Felder zerlegen und auf nfields auffuellen.

    Ueberzaehlige Felder bleiben erhalten, damit beim Speichern nichts verloren geht;
    die Validierung meldet sie als Fehler.
    """
    parts = [p.strip() for p in line.split(";")]
    if len(parts) < nfields:
        parts += [""] * (nfields - len(parts))
    return parts


def load(path, nfields):
    """Datei einlesen. Kommentar- und Leerzeilen werden positionsgetreu mitgefuehrt."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n").rstrip("\r")
            if is_skippable(line):
                rows.append(Row(raw=line))
            else:
                rows.append(Row(fields=parse_line(line, nfields)))
    return rows


def serialize(rows):
    """Zeilen zurueck in Dateiinhalt wandeln; leere Felder am Ende werden abgeschnitten."""
    out = []
    for row in rows:
        if not row.is_data:
            out.append(row.raw)
            continue
        fields = list(row.fields)
        while len(fields) > 1 and fields[-1] == "":
            fields.pop()
        out.append(";".join(fields))
    if not out:
        return ""
    return "\n".join(out) + "\n"


def save(path, rows):
    """Atomar schreiben (Temp-Datei + os.replace), vorher Backup, Modus 0600.

    Die Dateien enthalten Private Keys, daher restriktive Rechte und kein
    halb geschriebener Zustand bei einem Fehler.
    """
    text = serialize(rows)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if os.path.exists(path):
        backup = path + ".bak"
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".wg-manager-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def data_rows(rows):
    return [r for r in rows if r.is_data]


def server_names(servers):
    return [r.get(S_NAME) for r in data_rows(servers) if r.get(S_NAME)]


def clients_for(server_name, clients):
    """Clients, die fuer diesen Server gelten.

    Leeres SERVER-Feld bedeutet "gilt fuer alle Server". Dieselbe Regel setzt
    generate.sh ueber die CSERVER-Pruefung in beiden Client-Schleifen um -- wird die
    hier geaendert, muss sie dort mitgeaendert werden.
    """
    out = []
    for row in data_rows(clients):
        assigned = row.get(C_SERVER)
        if assigned == "" or assigned == server_name:
            out.append(row)
    return out


def scopes_overlap(a, b):
    """Zwei Server-Zuordnungen kollidieren, wenn mindestens eine global ist."""
    return a == "" or b == "" or a == b


# --- Validierung -----------------------------------------------------------

def _check_no_semicolon(value, label, errors):
    if ";" in value:
        errors.append("%s must not contain ';'" % label)


def _check_ip_list(value, label, errors):
    if value == "":
        return
    for part in value.split(","):
        part = part.strip()
        if not part:
            errors.append("%s has an empty entry" % label)
            continue
        try:
            ipaddress.ip_address(part)
        except ValueError:
            errors.append("%s: '%s' is not a valid IP address" % (label, part))


def _check_net_list(value, label, errors):
    if value == "":
        return
    for part in value.split(","):
        part = part.strip()
        if not part:
            errors.append("%s has an empty entry" % label)
            continue
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            errors.append("%s: '%s' is not a valid network" % (label, part))


def _check_mode(value, label, errors):
    if value.lower() not in MODES:
        errors.append("%s must be empty, 'split' or 'full'" % label)


def validate_server(fields, others):
    """Fehlerliste fuer einen Servereintrag. others = Felder der uebrigen Server."""
    errors = []
    for value in fields:
        _check_no_semicolon(value, "field", errors)
    if len(fields) > SERVER_NFIELDS:
        errors.append("too many fields (%d, expected %d)" % (len(fields), SERVER_NFIELDS))

    name = fields[S_NAME]
    if name == "":
        errors.append("Name must not be empty")
    elif "/" in name or name in (".", ".."):
        errors.append("Name is used as a directory name, so '/' is not allowed")
    if any(o[S_NAME] == name for o in others):
        errors.append("another server named '%s' already exists" % name)

    if fields[S_ENDPOINT] == "":
        errors.append("Endpoint must not be empty")
    if fields[S_INTERFACE] == "":
        errors.append("Interface must not be empty")

    address = None
    try:
        address = ipaddress.ip_interface(fields[S_ADDRESS])
    except ValueError:
        errors.append("Server address must look like 10.0.0.1/24")

    network = None
    try:
        network = ipaddress.ip_network(fields[S_NETWORK], strict=True)
    except ValueError:
        errors.append("Network must be a network address like 10.0.0.0/24")

    if address is not None and network is not None and address.ip not in network:
        errors.append("Server address %s is outside %s" % (address.ip, network))

    port = fields[S_PORT]
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        errors.append("Port must be a number between 1 and 65535")

    if fields[S_PRIVKEY] == "":
        errors.append("Private key must not be empty")

    _check_mode(fields[S_MODE], "Default mode", errors)
    _check_ip_list(fields[S_DNS], "DNS", errors)
    return errors


def validate_client(fields, others, servers):
    """Fehlerliste fuer einen Clienteintrag.

    others  = Felder der uebrigen Clients
    servers = Felder aller Server (fuer die Pruefung der Zuordnung)
    """
    errors = []
    for value in fields:
        _check_no_semicolon(value, "field", errors)
    if len(fields) > CLIENT_NFIELDS:
        errors.append("too many fields (%d, expected %d)" % (len(fields), CLIENT_NFIELDS))

    name = fields[C_NAME]
    if name == "":
        errors.append("Name must not be empty")
    elif "/" in name:
        errors.append("Name is used as a file name, so '/' is not allowed")

    # Ohne Praefix: die Templates haengen selbst /32 an %CLIENTIP% an
    ip = None
    if "/" in fields[C_IP]:
        errors.append("Client IP must not carry a prefix -- use 10.0.0.2, not 10.0.0.2/32 "
                      "(the templates append /32 themselves)")
    else:
        try:
            ip = ipaddress.ip_address(fields[C_IP])
        except ValueError:
            errors.append("Client IP must be a plain address like 10.0.0.2")

    if fields[C_PRIVKEY] == "":
        errors.append("Private key must not be empty")

    _check_net_list(fields[C_NETWORKS], "Extra networks", errors)
    _check_mode(fields[C_MODE], "Tunnel mode", errors)
    _check_ip_list(fields[C_DNS], "DNS", errors)

    assigned = fields[C_SERVER]
    names = [s[S_NAME] for s in servers]
    if assigned != "" and assigned not in names:
        errors.append("Server '%s' does not exist in %s" % (assigned, SERVER_FILE))

    # Kollisionen nur gegen Clients pruefen, die sich einen Server teilen koennen
    for other in others:
        if not scopes_overlap(assigned, other[C_SERVER]):
            continue
        if name and other[C_NAME] == name:
            errors.append("client '%s' already exists for this server" % name)
        if ip is not None:
            try:
                if ipaddress.ip_interface(other[C_IP]).ip == ip:
                    errors.append("IP %s is already used by client '%s'"
                                  % (ip, other[C_NAME]))
            except ValueError:
                pass

    # Passt die IP ins Netz des zugeordneten Servers?
    if ip is not None and assigned:
        for server in servers:
            if server[S_NAME] != assigned:
                continue
            try:
                network = ipaddress.ip_network(server[S_NETWORK], strict=False)
            except ValueError:
                break
            if ip not in network:
                errors.append("IP %s is outside the server network %s" % (ip, network))
    return errors


def suggest_ip(server_fields, clients):
    """Erste freie Adresse im Servernetz, oder None wenn das Netz voll ist.

    Rueckgabe ohne Praefix: templates/client und templates/server-peer haengen selbst
    `/32` an `%CLIENTIP%` an, ein Praefix hier wuerde zu `10.0.0.2/32/32` fuehren.
    """
    try:
        network = ipaddress.ip_network(server_fields[S_NETWORK], strict=False)
    except ValueError:
        return None

    used = set()
    try:
        used.add(ipaddress.ip_interface(server_fields[S_ADDRESS]).ip)
    except ValueError:
        pass
    for row in clients_for(server_fields[S_NAME], clients):
        try:
            used.add(ipaddress.ip_interface(row.get(C_IP)).ip)
        except ValueError:
            continue

    for host in network.hosts():
        if host not in used:
            return str(host)
    return None


# --- wg-Aufrufe ------------------------------------------------------------

class WgError(Exception):
    pass


def _wg(args, stdin=None):
    try:
        proc = subprocess.run(["wg"] + args, input=stdin,
                              capture_output=True, text=True)
    except FileNotFoundError:
        raise WgError("'wg' not found in PATH -- please enter the key manually")
    if proc.returncode != 0:
        raise WgError((proc.stderr or "wg failed").strip().splitlines()[0])
    return proc.stdout.strip()


def wg_genkey():
    return _wg(["genkey"])


def wg_pubkey(privkey):
    return _wg(["pubkey"], stdin=privkey + "\n")


# ---------------------------------------------------------------------------
# Oberflaeche
# ---------------------------------------------------------------------------

# (Label, Feldindex, Art, Hinweis) -- die Reihenfolge hier ist die Formularreihenfolge
SERVER_FORM = [
    ("Name", S_NAME, "text", "used for configs/<name>.server.conf"),
    ("Endpoint", S_ENDPOINT, "text", "public host or IP clients connect to"),
    ("Interface", S_INTERFACE, "text", "e.g. wg0"),
    ("Server address", S_ADDRESS, "text", "server IP inside the VPN, e.g. 10.0.0.1/24"),
    ("Network", S_NETWORK, "text", "VPN network, e.g. 10.0.0.0/24"),
    ("Port", S_PORT, "text", "UDP listen port, e.g. 51820"),
    ("Private key", S_PRIVKEY, "key", "^G generates a new key"),
    ("Default mode", S_MODE, "mode", "LEFT/RIGHT to change; used when a client sets none"),
    ("DNS", S_DNS, "text", "default DNS for clients; empty falls back to 8.8.8.8"),
]

CLIENT_FORM = [
    ("Name", C_NAME, "text", "used for <name>.client.conf"),
    ("Client IP", C_IP, "ip", "plain address, no /32 -- ^T suggests the next free one"),
    ("Private key", C_PRIVKEY, "key", "^G generates a new key"),
    ("Extra networks", C_NETWORKS, "text", "comma separated; server side AllowedIPs only"),
    ("Tunnel mode", C_MODE, "mode", "LEFT/RIGHT to change"),
    ("DNS", C_DNS, "text", "empty inherits the server's DNS"),
    ("Server", C_SERVER, "server", "LEFT/RIGHT to change; (all servers) = every server"),
]


class App:
    def __init__(self, scr):
        self.scr = scr
        self.servers = load(SERVER_FILE, SERVER_NFIELDS)
        self.clients = load(CLIENTS_FILE, CLIENT_NFIELDS)
        self.status = "Loaded %d server(s) and %d client(s)" % (
            len(data_rows(self.servers)), len(data_rows(self.clients)))

    # --- Basis-Ausgabe ----------------------------------------------------

    def put(self, y, x, text, attr=0):
        height, width = self.scr.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        try:
            self.scr.addnstr(y, x, text, width - x - 1, attr)
        except curses.error:
            pass

    def header(self, title, subtitle=""):
        height, width = self.scr.getmaxyx()
        self.put(0, 0, " " + title.ljust(max(0, width - 2)), curses.A_REVERSE)
        if subtitle:
            self.put(1, 1, subtitle, curses.A_BOLD)

    def footer(self, keys):
        height, _ = self.scr.getmaxyx()
        self.put(height - 2, 1, keys, curses.A_BOLD)
        self.put(height - 1, 1, self.status)

    def message(self, text):
        self.status = text

    def confirm(self, question):
        height, _ = self.scr.getmaxyx()
        self.put(height - 1, 1, " " * 2)
        self.put(height - 1, 1, question + " [y/N] ", curses.A_REVERSE)
        self.scr.refresh()
        while True:
            try:
                key = self.scr.get_wch()
            except curses.error:
                return False
            if isinstance(key, str):
                if key.lower() == "y":
                    return True
                if key.lower() == "n" or key in ("\x1b", "\n", "\r"):
                    return False
            else:
                return False

    def show_errors(self, errors):
        """Validierungsfehler in einem Fenster anzeigen; blockiert bis Tastendruck."""
        self.pager("Please fix the following", errors)

    def pager(self, title, lines):
        top = 0
        while True:
            self.scr.erase()
            height, _ = self.scr.getmaxyx()
            self.header(title)
            view = height - 4
            visible = lines[top:top + view]
            for offset, line in enumerate(visible):
                self.put(2 + offset, 1, line)
            more = " (%d-%d of %d)" % (top + 1, top + len(visible), len(lines)) if lines else ""
            self.footer("UP/DOWN scroll   any other key: back" + more)
            self.scr.refresh()
            try:
                key = self.scr.get_wch()
            except curses.error:
                continue
            if key == curses.KEY_DOWN:
                top = min(top + 1, max(0, len(lines) - 1))
            elif key == curses.KEY_UP:
                top = max(0, top - 1)
            elif key == curses.KEY_NPAGE:
                top = min(top + view, max(0, len(lines) - 1))
            elif key == curses.KEY_PPAGE:
                top = max(0, top - view)
            elif key == curses.KEY_RESIZE:
                continue
            else:
                return

    # --- Speichern --------------------------------------------------------

    def persist(self, path, rows, what):
        try:
            save(path, rows)
        except OSError as exc:
            self.message("ERROR: could not write %s: %s" % (path, exc))
            return False
        self.message("%s -- %s saved (backup: %s.bak)" % (what, path, path))
        return True

    # --- Server-Liste -----------------------------------------------------

    def server_screen(self):
        selected = 0
        while True:
            rows = data_rows(self.servers)
            selected = max(0, min(selected, len(rows) - 1)) if rows else 0

            self.scr.erase()
            self.header("wg-manager -- servers (%s)" % SERVER_FILE)
            self.put(2, 1, "%-14s %-26s %-18s %-7s %s"
                     % ("NAME", "ENDPOINT:PORT", "NETWORK", "MODE", "DNS"),
                     curses.A_UNDERLINE)
            if not rows:
                self.put(4, 1, "No servers yet -- press 'a' to add one.")
            for index, row in enumerate(rows):
                endpoint = "%s:%s" % (row.get(S_ENDPOINT), row.get(S_PORT))
                line = "%-14s %-26s %-18s %-7s %s" % (
                    row.get(S_NAME)[:14], endpoint[:26], row.get(S_NETWORK)[:18],
                    MODE_LABELS.get(row.get(S_MODE), row.get(S_MODE))[:7],
                    row.get(S_DNS) or "8.8.8.8 (default)")
                attr = curses.A_REVERSE if index == selected else 0
                self.put(3 + index, 1, line.ljust(self.scr.getmaxyx()[1] - 2), attr)
            self.footer("ENTER clients   a add   e edit   d delete   g run generate.sh   q quit")
            self.scr.refresh()

            try:
                key = self.scr.get_wch()
            except curses.error:
                continue

            if key == curses.KEY_RESIZE:
                continue
            if key == curses.KEY_DOWN:
                selected = min(selected + 1, max(0, len(rows) - 1))
            elif key == curses.KEY_UP:
                selected = max(0, selected - 1)
            elif key in ("\n", "\r", curses.KEY_ENTER):
                if rows:
                    self.client_screen(rows[selected])
            elif key in ("a", "A"):
                self.add_server()
            elif key in ("e", "E"):
                if rows:
                    self.edit_server(rows[selected])
            elif key in ("d", "D"):
                if rows:
                    self.delete_server(rows[selected])
            elif key in ("g", "G"):
                self.run_generate()
            elif key in ("q", "Q", "\x1b"):
                return

    def add_server(self):
        fields = [""] * SERVER_NFIELDS
        fields[S_INTERFACE] = "wg0"
        fields[S_PORT] = "51820"
        fields[S_MODE] = "split"
        try:
            fields[S_PRIVKEY] = wg_genkey()
            note = "new key generated with wg genkey"
        except WgError as exc:
            note = str(exc)
        self.message(note)
        result = self.edit_form("Add server", SERVER_FORM, fields, is_server=True,
                                original=None)
        if result is None:
            self.message("Cancelled -- nothing was written")
            return
        self.servers.append(Row(fields=result))
        self.persist(SERVER_FILE, self.servers, "Server '%s' added" % result[S_NAME])

    def edit_server(self, row):
        result = self.edit_form("Edit server", SERVER_FORM, list(row.fields),
                                is_server=True, original=row)
        if result is None:
            self.message("Cancelled -- nothing was written")
            return
        old_name = row.get(S_NAME)
        row.fields = result
        # Umbenennen: Zuordnungen in clients.txt mitziehen, sonst zeigen sie ins Leere
        if old_name and old_name != result[S_NAME]:
            affected = [r for r in data_rows(self.clients) if r.get(C_SERVER) == old_name]
            for client in affected:
                client.fields[C_SERVER] = result[S_NAME]
            if affected:
                self.persist(CLIENTS_FILE, self.clients,
                             "Updated %d client assignment(s)" % len(affected))
        self.persist(SERVER_FILE, self.servers, "Server '%s' saved" % result[S_NAME])

    def delete_server(self, row):
        name = row.get(S_NAME)
        assigned = [r for r in data_rows(self.clients) if r.get(C_SERVER) == name]
        question = "Delete server '%s'?" % name
        if assigned:
            question = ("Delete server '%s'? %d client(s) are assigned to it and will "
                        "keep a dangling assignment." % (name, len(assigned)))
        if not self.confirm(question):
            self.message("Not deleted")
            return
        self.servers.remove(row)
        self.persist(SERVER_FILE, self.servers, "Server '%s' deleted" % name)

    # --- Client-Liste -----------------------------------------------------

    def client_screen(self, server):
        selected = 0
        while True:
            name = server.get(S_NAME)
            rows = clients_for(name, self.clients)
            selected = max(0, min(selected, len(rows) - 1)) if rows else 0

            self.scr.erase()
            self.header("wg-manager -- clients (%s)" % CLIENTS_FILE,
                        "Server: %s   %s:%s   network %s   default mode %s"
                        % (name, server.get(S_ENDPOINT), server.get(S_PORT),
                           server.get(S_NETWORK),
                           MODE_LABELS.get(server.get(S_MODE), server.get(S_MODE))))
            self.put(3, 1, "  %-16s %-19s %-7s %-16s %s"
                     % ("NAME", "IP", "MODE", "DNS", "EXTRA NETWORKS"),
                     curses.A_UNDERLINE)
            if not rows:
                self.put(5, 1, "No clients for this server yet -- press 'a' to add one.")
            for index, row in enumerate(rows):
                marker = "*" if row.get(C_SERVER) == "" else " "
                line = "%s %-16s %-19s %-7s %-16s %s" % (
                    marker, row.get(C_NAME)[:16], row.get(C_IP)[:19],
                    MODE_LABELS.get(row.get(C_MODE), row.get(C_MODE))[:7],
                    (row.get(C_DNS) or "(inherited)")[:16], row.get(C_NETWORKS))
                attr = curses.A_REVERSE if index == selected else 0
                self.put(4 + index, 1, line.ljust(self.scr.getmaxyx()[1] - 2), attr)
            self.put(self.scr.getmaxyx()[0] - 3, 1,
                     "* = not assigned to a server, applies to every server")
            self.footer("a add   e edit   d delete   g run generate.sh   b back   q quit")
            self.scr.refresh()

            try:
                key = self.scr.get_wch()
            except curses.error:
                continue

            if key == curses.KEY_RESIZE:
                continue
            if key == curses.KEY_DOWN:
                selected = min(selected + 1, max(0, len(rows) - 1))
            elif key == curses.KEY_UP:
                selected = max(0, selected - 1)
            elif key in ("a", "A"):
                self.add_client(server)
            elif key in ("e", "E"):
                if rows:
                    self.edit_client(rows[selected])
            elif key in ("d", "D"):
                if rows:
                    self.delete_client(rows[selected])
            elif key in ("g", "G"):
                self.run_generate()
            elif key in ("b", "B", "\x1b"):
                return
            elif key in ("q", "Q"):
                raise Quit()

    def add_client(self, server):
        fields = [""] * CLIENT_NFIELDS
        fields[C_SERVER] = server.get(S_NAME)
        notes = []
        suggestion = suggest_ip(server.fields, self.clients)
        if suggestion:
            fields[C_IP] = suggestion
            notes.append("IP %s suggested" % suggestion)
        else:
            notes.append("no free IP found in %s" % server.get(S_NETWORK))
        try:
            fields[C_PRIVKEY] = wg_genkey()
            notes.append("key generated")
        except WgError as exc:
            notes.append(str(exc))
        self.message("; ".join(notes))
        result = self.edit_form("Add client", CLIENT_FORM, fields, is_server=False,
                                original=None, server=server)
        if result is None:
            self.message("Cancelled -- nothing was written")
            return
        self.clients.append(Row(fields=result))
        self.persist(CLIENTS_FILE, self.clients, "Client '%s' added" % result[C_NAME])

    def edit_client(self, row):
        result = self.edit_form("Edit client", CLIENT_FORM, list(row.fields),
                                is_server=False, original=row)
        if result is None:
            self.message("Cancelled -- nothing was written")
            return
        row.fields = result
        self.persist(CLIENTS_FILE, self.clients, "Client '%s' saved" % result[C_NAME])

    def delete_client(self, row):
        name = row.get(C_NAME)
        if not self.confirm("Delete client '%s' (%s)?" % (name, row.get(C_IP))):
            self.message("Not deleted")
            return
        self.clients.remove(row)
        self.persist(CLIENTS_FILE, self.clients, "Client '%s' deleted" % name)

    # --- Formular ---------------------------------------------------------

    def edit_form(self, title, spec, values, is_server, original, server=None):
        """Formular anzeigen. Rueckgabe: neue Feldliste oder None bei Abbruch."""
        values = list(values)
        field = 0
        cursor = len(values[spec[0][1]])
        choices_for_server = ["(all servers)"] + server_names(self.servers)
        local_status = ""

        while True:
            kind = spec[field][2]
            self.scr.erase()
            self.header("wg-manager -- %s" % title)
            label_width = max(len(item[0]) for item in spec) + 2
            value_x = 2 + label_width

            for index, (label, idx, kind_i, _hint) in enumerate(spec):
                y = 3 + index * 2
                active = index == field
                self.put(y, 2, (label + ":").ljust(label_width),
                         curses.A_BOLD if active else 0)
                shown = self._display_value(values[idx], kind_i)
                width = self.scr.getmaxyx()[1] - value_x - 2
                self.put(y, value_x, shown.ljust(max(0, min(width, 60))),
                         curses.A_REVERSE if active else curses.A_UNDERLINE)

            # Hinweis zum aktiven Feld plus abgeleiteter Public Key
            hint = spec[field][3]
            self.put(3 + len(spec) * 2, 2, "-> " + hint)
            key_index = S_PRIVKEY if is_server else C_PRIVKEY
            if values[key_index]:
                try:
                    pub = wg_pubkey(values[key_index])
                    self.put(4 + len(spec) * 2, 2, "   public key: " + pub)
                except WgError:
                    pass

            self.status = local_status or self.status
            self.footer("F2/^S save   ESC cancel   TAB/UP/DOWN field"
                        + ("   ^G new key" if kind == "key" else "")
                        + ("   ^T suggest IP" if kind == "ip" else "")
                        + ("   LEFT/RIGHT change" if kind in ("mode", "server") else ""))

            if kind in ("text", "key", "ip"):
                try:
                    curses.curs_set(1)
                    self.scr.move(3 + field * 2,
                                  min(value_x + cursor, self.scr.getmaxyx()[1] - 1))
                except curses.error:
                    pass
            else:
                try:
                    curses.curs_set(0)
                except curses.error:
                    pass
            self.scr.refresh()

            try:
                key = self.scr.get_wch()
            except curses.error:
                continue

            idx = spec[field][1]
            local_status = ""

            def move(delta):
                nonlocal field, cursor
                field = (field + delta) % len(spec)
                cursor = len(values[spec[field][1]])

            if key == curses.KEY_RESIZE:
                continue
            if key == "\x1b":  # ESC
                try:
                    curses.curs_set(0)
                except curses.error:
                    pass
                return None
            if key in (curses.KEY_F2, "\x13"):  # F2 / Ctrl-S
                errors = self._validate(values, is_server, original)
                if errors:
                    self.show_errors(errors)
                    local_status = "%d problem(s) -- nothing written yet" % len(errors)
                    continue
                try:
                    curses.curs_set(0)
                except curses.error:
                    pass
                return values
            if key in ("\t", curses.KEY_DOWN):
                move(1)
                continue
            if key in (curses.KEY_BTAB, curses.KEY_UP):
                move(-1)
                continue
            if key in ("\n", "\r", curses.KEY_ENTER):
                if field == len(spec) - 1:
                    errors = self._validate(values, is_server, original)
                    if errors:
                        self.show_errors(errors)
                        local_status = "%d problem(s) -- nothing written yet" % len(errors)
                        continue
                    try:
                        curses.curs_set(0)
                    except curses.error:
                        pass
                    return values
                move(1)
                continue

            if kind == "mode":
                if key in (curses.KEY_RIGHT, " "):
                    values[idx] = MODES[(MODES.index(self._norm_mode(values[idx])) + 1)
                                        % len(MODES)]
                elif key == curses.KEY_LEFT:
                    values[idx] = MODES[(MODES.index(self._norm_mode(values[idx])) - 1)
                                        % len(MODES)]
                continue

            if kind == "server":
                current = values[idx]
                position = (choices_for_server.index(current)
                            if current in choices_for_server else 0)
                if key in (curses.KEY_RIGHT, " "):
                    position = (position + 1) % len(choices_for_server)
                elif key == curses.KEY_LEFT:
                    position = (position - 1) % len(choices_for_server)
                else:
                    continue
                choice = choices_for_server[position]
                values[idx] = "" if choice == "(all servers)" else choice
                continue

            # Textfelder (inkl. key/ip mit Sonderaktionen)
            if key == "\x07" and kind == "key":  # Ctrl-G
                try:
                    values[idx] = wg_genkey()
                    cursor = len(values[idx])
                    local_status = "new private key generated"
                except WgError as exc:
                    local_status = str(exc)
                continue
            if key == "\x14" and kind == "ip":  # Ctrl-T
                target = server
                if target is None:
                    target = self._server_row(values[C_SERVER])
                if target is None:
                    local_status = "assign a server first, then ^T can suggest an IP"
                    continue
                suggestion = suggest_ip(target.fields, self.clients)
                if suggestion:
                    values[idx] = suggestion
                    cursor = len(values[idx])
                    local_status = "suggested %s" % suggestion
                else:
                    local_status = "no free address left in %s" % target.get(S_NETWORK)
                continue

            if key == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
            elif key == curses.KEY_RIGHT:
                cursor = min(len(values[idx]), cursor + 1)
            elif key == curses.KEY_HOME:
                cursor = 0
            elif key == curses.KEY_END:
                cursor = len(values[idx])
            elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                if cursor > 0:
                    values[idx] = values[idx][:cursor - 1] + values[idx][cursor:]
                    cursor -= 1
            elif key == curses.KEY_DC:
                values[idx] = values[idx][:cursor] + values[idx][cursor + 1:]
            elif isinstance(key, str) and key.isprintable():
                values[idx] = values[idx][:cursor] + key + values[idx][cursor:]
                cursor += len(key)

    @staticmethod
    def _norm_mode(value):
        value = value.lower()
        return value if value in MODES else ""

    @staticmethod
    def _display_value(value, kind):
        if kind == "mode":
            return MODE_LABELS.get(value.lower(), value)
        if kind == "server":
            return value if value else "(all servers)"
        return value

    def _server_row(self, name):
        for row in data_rows(self.servers):
            if row.get(S_NAME) == name:
                return row
        return None

    def _validate(self, values, is_server, original):
        if is_server:
            others = [r.fields for r in data_rows(self.servers) if r is not original]
            return validate_server(values, others)
        others = [r.fields for r in data_rows(self.clients) if r is not original]
        servers = [r.fields for r in data_rows(self.servers)]
        return validate_client(values, others, servers)

    # --- generate.sh ------------------------------------------------------

    def run_generate(self):
        if not os.path.exists(GENERATE_SCRIPT):
            self.message("ERROR: %s not found in the current directory" % GENERATE_SCRIPT)
            return
        if not self.confirm("Run %s? This deletes and recreates configs/." % GENERATE_SCRIPT):
            self.message("generate.sh not started")
            return
        try:
            proc = subprocess.run(["bash", GENERATE_SCRIPT],
                                  capture_output=True, text=True)
        except OSError as exc:
            self.message("ERROR: could not run %s: %s" % (GENERATE_SCRIPT, exc))
            return
        lines = ["$ bash %s" % GENERATE_SCRIPT, ""]
        lines += (proc.stdout or "").splitlines()
        if proc.stderr:
            lines += ["", "--- stderr ---"] + proc.stderr.splitlines()
        lines += ["", "exit code: %d" % proc.returncode]
        self.pager("generate.sh output", lines)
        self.message("generate.sh finished with exit code %d" % proc.returncode)


class Quit(Exception):
    """Wird aus tieferen Screens geworfen, um die Anwendung zu beenden."""


# ---------------------------------------------------------------------------
# Selbsttest der Datenschicht (laeuft ohne Terminal)
# ---------------------------------------------------------------------------

def selftest():
    # parse_line: auffuellen, strippen, Extras behalten
    assert parse_line("a;b", 4) == ["a", "b", "", ""]
    assert parse_line(" a ; b ", 2) == ["a", "b"]
    assert parse_line("a;b;c", 2) == ["a", "b", "c"]

    # is_skippable deckt dieselben Faelle ab wie generate.sh
    assert is_skippable("")
    assert is_skippable("   ")
    assert is_skippable("# comment")
    assert not is_skippable("1.1.1.1;c1;key")

    # Round-trip inklusive Kommentar- und Leerzeilen an Originalposition
    text = "# servers\n\nwg-a;a.example;wg0;10.0.0.1/24;10.0.0.0/24;51820;KEY;split;9.9.9.9\n"
    path = os.path.join(tempfile.mkdtemp(), SERVER_FILE)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    rows = load(path, SERVER_NFIELDS)
    assert [r.is_data for r in rows] == [False, False, True]
    assert serialize(rows) == text

    # Leere Felder am Ende werden abgeschnitten
    assert serialize([Row(fields=["a", "b", "", ""])]) == "a;b\n"
    assert serialize([Row(fields=["a", "", "c"])]) == "a;;c\n"
    assert serialize([Row(fields=[""])]) == "\n"

    # save(): atomar, mit Backup und restriktiven Rechten
    rows[2].fields[S_NAME] = "wg-b"
    save(path, rows)
    assert os.path.exists(path + ".bak")
    assert oct(os.stat(path).st_mode)[-3:] == "600"
    assert load(path, SERVER_NFIELDS)[2].get(S_NAME) == "wg-b"

    # clients_for(): leeres SERVER-Feld gilt fuer alle Server
    def client(ip, name, srv=""):
        return Row(fields=parse_line("%s;%s;KEY;;;;%s" % (ip, name, srv), CLIENT_NFIELDS))

    # Client-IPs stehen ohne Praefix in clients.txt -- die Templates haengen /32 an
    clients = [client("10.0.0.2", "a", "wg-a"),
               client("10.0.0.3", "b", "wg-b"),
               client("10.0.0.4", "glob")]
    assert [r.get(C_NAME) for r in clients_for("wg-a", clients)] == ["a", "glob"]
    assert [r.get(C_NAME) for r in clients_for("wg-b", clients)] == ["b", "glob"]
    assert [r.get(C_NAME) for r in clients_for("nope", clients)] == ["glob"]

    assert scopes_overlap("", "wg-a") and scopes_overlap("wg-a", "wg-a")
    assert not scopes_overlap("wg-a", "wg-b")

    # suggest_ip(): Serveradresse und belegte Client-IPs werden uebersprungen.
    # 10.0.0.3 gehoert zu Client "b" auf wg-b und ist fuer wg-a daher wieder frei --
    # getrennte VPN-Netze duerfen dieselben Adressen verwenden.
    srv_a = parse_line("wg-a;a.example;wg0;10.0.0.1/24;10.0.0.0/24;51820;KEY;split;",
                       SERVER_NFIELDS)
    assert suggest_ip(srv_a, clients) == "10.0.0.3"
    assert suggest_ip(srv_a, []) == "10.0.0.2"
    # ein /31 bietet nach Abzug der Serveradresse keinen Platz mehr
    tiny = parse_line("t;t.example;wg0;10.1.0.0/31;10.1.0.0/31;51820;KEY;split;",
                      SERVER_NFIELDS)
    assert suggest_ip(tiny, [client("10.1.0.1", "x", "t")]) is None
    # kaputtes Netz fuehrt nicht zur Exception
    assert suggest_ip(parse_line("t;t;wg0;x;nonsense;1;K;split;", SERVER_NFIELDS), []) is None

    # validate_server(): guter Eintrag
    assert validate_server(srv_a, []) == []
    # Name doppelt, Port kaputt, Netz ist keine Netzadresse, DNS ungueltig
    bad = parse_line("wg-a;a.example;wg0;10.0.0.1/24;10.0.0.1/24;99999;KEY;nope;notanip",
                     SERVER_NFIELDS)
    errors = validate_server(bad, [srv_a])
    assert any("already exists" in e for e in errors)
    assert any("Port" in e for e in errors)
    assert any("network address" in e for e in errors)
    assert any("split" in e for e in errors)
    assert any("DNS" in e for e in errors)
    # Semikolon im Feld und Adresse ausserhalb des Netzes
    assert any("';'" in e for e in validate_server(
        parse_line("a;b", SERVER_NFIELDS)[:0] + ["we;ird"] + [""] * 8, []))
    outside = parse_line("wg-c;c.example;wg0;10.9.9.1/24;10.0.0.0/24;51820;KEY;split;",
                         SERVER_NFIELDS)
    assert any("outside" in e for e in validate_server(outside, []))

    # validate_client(): guter Eintrag
    ok = parse_line("10.0.0.9;dave;KEY;192.168.0.0/16;full;9.9.9.9;wg-a", CLIENT_NFIELDS)
    assert validate_client(ok, [], [srv_a]) == []
    # Ein Praefix in der Client-IP wuerde zu "10.0.0.9/32/32" in der Config fuehren
    with_prefix = parse_line("10.0.0.9/32;dave;KEY;;;;wg-a", CLIENT_NFIELDS)
    assert any("must not carry a prefix" in e
               for e in validate_client(with_prefix, [], [srv_a]))
    # Name und IP kollidieren mit einem global zugeordneten Client
    dup = parse_line("10.0.0.4;glob;KEY;;;;wg-a", CLIENT_NFIELDS)
    errors = validate_client(dup, [c.fields for c in clients], [srv_a])
    assert any("already exists" in e for e in errors)
    assert any("already used" in e for e in errors)
    # unbekannter Server, IP ausserhalb des Netzes, kaputtes Extra-Netz
    errors = validate_client(
        parse_line("10.7.0.2;x;KEY;notanet;;;wg-zz", CLIENT_NFIELDS), [], [srv_a])
    assert any("does not exist" in e for e in errors)
    assert any("not a valid network" in e for e in errors)
    errors = validate_client(
        parse_line("10.7.0.2;x;KEY;;;;wg-a", CLIENT_NFIELDS), [], [srv_a])
    assert any("outside the server network" in e for e in errors)
    # leerer Name, leerer Key, kaputte IP
    errors = validate_client(parse_line("nope;;;;;;", CLIENT_NFIELDS), [], [srv_a])
    assert any("Name must not be empty" in e for e in errors)
    assert any("Private key" in e for e in errors)
    assert any("Client IP" in e for e in errors)
    # Clients in verschiedenen Servern duerfen Name und IP teilen
    same = parse_line("10.0.0.2;a;KEY;;;;wg-b", CLIENT_NFIELDS)
    assert validate_client(same, [clients[0].fields], [srv_a, parse_line(
        "wg-b;b.example;wg0;10.0.0.1/24;10.0.0.0/24;51821;KEY;split;", SERVER_NFIELDS)]) == []

    print("selftest: all checks passed")
    return 0


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def disable_flow_control():
    """Ctrl-S/Ctrl-Q sind per Default Flusskontrolle (XOFF/XON) des Terminals und
    erreichen die Anwendung nie. Damit "^S = speichern" funktioniert, muss IXON aus.
    Rueckgabe: Zustand fuer restore_flow_control(), oder None.
    """
    try:
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
    except (termios.error, ValueError, OSError):
        return None
    attrs = list(saved)
    attrs[0] &= ~termios.IXON
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except (termios.error, OSError):
        return None
    return (fd, saved)


def restore_flow_control(state):
    if state is None:
        return
    try:
        termios.tcsetattr(state[0], termios.TCSADRAIN, state[1])
    except (termios.error, OSError):
        pass


def ensure_files():
    """Fehlende Eingabedateien anlegen -- leer oder aus der .sample-Vorlage."""
    for path in (SERVER_FILE, CLIENTS_FILE):
        if os.path.exists(path):
            continue
        sample = path + ".sample"
        print("%s does not exist." % path)
        if os.path.exists(sample):
            answer = input("Create it from %s? [y/N] " % sample).strip().lower()
            if answer == "y":
                shutil.copy2(sample, path)
                os.chmod(path, 0o600)
                print("  copied %s -> %s" % (sample, path))
                continue
        if input("Create an empty %s? [y/N] " % path).strip().lower() != "y":
            print("Aborted -- %s is required." % path)
            return False
        open(path, "a", encoding="utf-8").close()
        os.chmod(path, 0o600)
        print("  created empty %s" % path)
    return True


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--selftest":
            return selftest()
        if sys.argv[1] in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        print("unknown argument: %s (try --help)" % sys.argv[1], file=sys.stderr)
        return 2

    size = shutil.get_terminal_size(fallback=(80, 24))
    if size.columns < MIN_COLS or size.lines < MIN_LINES:
        print("Terminal is too small: %dx%d, need at least %dx%d."
              % (size.columns, size.lines, MIN_COLS, MIN_LINES), file=sys.stderr)
        return 1

    if not ensure_files():
        return 2

    def bootstrap(scr):
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(25)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        saved = disable_flow_control()
        app = App(scr)
        try:
            app.server_screen()
        except Quit:
            pass
        finally:
            restore_flow_control(saved)

    curses.wrapper(bootstrap)
    return 0


if __name__ == "__main__":
    sys.exit(main())

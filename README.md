# wgctl

Manage devices on an existing WireGuard server with a few commands.

```console
$ wgctl
INTERFACE   NAME             ID             IP         STATE    HANDSHAKE   RECEIVED   SENT
wg0         phone            mJ8QpN2rT5vX   10.8.0.2   active   34s ago     12.0MiB    84.0MiB
wg0         unknown device   zK3sL9aB6cDe   10.8.0.3   active   2h ago      4.0MiB     19.0MiB
```

Existing peers work without special comments. Unnamed peers appear as `unknown device`; use their IP or ID to manage them. `wgctl` reads both the saved configuration and the running interface.

## Install

Requires Linux, Python 3.8 or newer, and WireGuard tools (`wg`). Adding devices also uses `ip` from iproute2 to detect the server address when no endpoint is configured. Install `qrencode` for terminal QR codes.

From a checkout of this repository:

```sh
sudo install -m 755 wgctl /usr/local/bin/wgctl
wgctl
```

Commands need root privileges; `wgctl` invokes `sudo` when necessary. No Python packages, daemon, or database are required.

Your WireGuard server must already be configured. `wgctl` discovers configuration files in `/etc/wireguard` and active WireGuard interfaces. Listing works even when an active interface has no saved configuration.

## Usage

### Add a device

```sh
wgctl add phone
wgctl add laptop > laptop.conf
```

Import the configuration into your WireGuard client, or scan the QR shown in a terminal when `qrencode` is installed. Redirected output contains only the configuration, and regular output files are restricted to their owner.

`wgctl` chooses a free IPv4 address using the server's actual subnet mask. It reserves addresses used by the server, saved peers (including disabled devices), and active peers, including whole routed prefixes.

By default, the new device routes traffic only to the server's tunnel IPv4 address. For a server already configured to provide internet access:

```sh
wgctl add phone --full
```

Full-tunnel profiles use IPv4 internet access and DNS `1.1.1.1`. They also direct IPv6 into the tunnel so it does not bypass the VPN; IPv6 connectivity is not provisioned. Forwarding must already be enabled, and the server's firewall and return routing or NAT must already support internet access. `wgctl` does not configure them or claim to verify end-to-end connectivity.

**Save the generated configuration.** The device's private key is not retained on the server. If you lose it, remove the device and add it again; the old configuration will no longer work.

### List devices

```sh
wgctl
wgctl show
```

The default output is a Docker-style table: one header and one row per device across all interfaces. IDs are the first 12 characters of each public key. Handshakes show elapsed time and traffic uses readable units. An empty table contains only the header.

Traffic is measured from the server's perspective: received from the device and sent to it. `-` means runtime data is unavailable; `never` means no handshake has been recorded. Counters can reset when peers or interfaces are recreated.

`active` means the peer is loaded into WireGuard, not that the device is online. Other states are `not_loaded`, `not_saved`, `ip_mismatch`, `disabled`, `disabled_but_active`, and `interface_down`. `ip_mismatch` means the saved and active AllowedIPs differ; listing does not compare every peer setting.

For scripts, use `-j` (also `-json` or `--json`). Each line is a separate JSON object, with no header or surrounding array:

```sh
wgctl -j show

# Public keys of loaded devices.
wgctl -j show | jq -r 'select(.state == "active") | .public_key'

# Device names on wg0.
wgctl -j show dev wg0 | jq -r '.name'
```

JSON fields are `interface`, `name`, `ip`, `state`, `handshake`, `received_bytes`, `sent_bytes`, and `public_key`. Public keys are complete. `ip` contains comma-separated AllowedIPs CIDR prefixes. `handshake` is an integer Unix timestamp, or `0` when there has never been a handshake. Traffic counters are integer bytes. Unavailable runtime values are `null`. An empty list produces no JSON lines. Diagnostic messages and sudo prompts do not form part of stdout.

### Name, suspend, or remove a device

Use a name, a single-host IP, or a public key from the list:

```sh
wgctl set 10.8.0.3 name tablet
wgctl set tablet down
wgctl set tablet up
wgctl delete tablet
```

`set DEVICE down` preserves the device for later; `set DEVICE up` restores its access using the same client configuration. These operations act on the peer, not the server interface. `delete` removes its access permanently. Changes apply immediately when the interface is up, or are saved for its next start when it is down. Other peers are not reloaded.

Ambiguous selectors are rejected. Public key prefixes of at least eight characters are also accepted when unique. Devices found only in the running interface are listed, but must be saved in the server configuration before they can be edited persistently.

### Multiple interfaces

Listing shows every discovered interface. For changes, select one when there is more than one:

```sh
wgctl show dev wg1
wgctl add phone dev wg1
wgctl set tablet down dev wg1
```

### Back up

```sh
wgctl backup > wg.tgz
```

The archive contains the selected server configuration and `/etc/wgctl.conf`, if present. It contains secret keys: keep it private. With several interfaces, use `wgctl backup dev wg0 > wg.tgz`. Restore with `sudo tar -xzf wg.tgz -C /`, then apply the restored configuration through your usual WireGuard service management. Restoring files alone does not update a running interface.

### Abbreviations

Like `ip`, commands and keywords accept prefixes. Device names, IP addresses, keys and interface names are always taken literally.

| Full form | Accepted prefixes |
| --- | --- |
| `show` | `s`, `sh`, `sho` |
| `list` (alias for `show`) | `l`, `li`, `lis` |
| `set` | `se` |
| `add` | `a`, `ad` |
| `delete` | `d`, `de`, `del`, `dele`, `delet` |
| `backup` | `b`, `ba`, `bac`, `back`, `backu` |
| `dev` | `d`, `de` |
| `up` | `u` |
| `down` | `d`, `do`, `dow` |
| `name` | `n`, `na`, `nam` |
| `help` | `h`, `he`, `hel` |

`s` selects `show`; use `se` for `set`. Keywords are interpreted in their position, so `d` can mean `delete`, `down`, or `dev`:

```sh
wgctl sh d wg0
wgctl se phone d d wg0
wgctl se phone n tablet d wg0
wgctl del tablet d wg0
```

## Configuration

Most single-interface servers need no settings. If the server is behind NAT, its public hostname cannot be inferred reliably. Set it once in `/etc/wgctl.conf`:

```sh
ENDPOINT=vpn.example.com
```

An explicit port is supported, including `[IPv6-address]:port`. Without an explicit port, the server configuration must provide a fixed `ListenPort`.

These optional defaults are also supported:

```sh
IFACE=wg0
CLIENT_ALLOWED=10.8.0.0/24
CLIENT_DNS=10.8.0.1
```

`CLIENT_ALLOWED` replaces the default server-only routes for new devices; `--full` overrides it. `CLIENT_DNS` sets DNS for new profiles. These settings do not change existing profiles.

Settings are simple `KEY=value` assignments, with shell-style quoting and comments. They are read as data, never executed. The file must belong to root and must not be writable by other users.

## Scope and safeguards

`wgctl` manages devices on an existing Linux WireGuard server. It does not install a VPN server, configure firewalls, start interfaces, or manage client applications.

- Listing supports IPv4 and IPv6 peers. Creating devices currently requires an IPv4 subnet in the server's `Address` setting.
- Names are optional metadata. Existing `# BEGIN_PEER` blocks remain supported; `# Name = ...` inside a peer section is also recognized. Arbitrary comments are not guessed to be device names.
- Changes preserve other sections, create an owner-only `.conf.bak` of the previous configuration, and replace the configuration file atomically. A failed live update triggers file and peer rollback; failures to restore live state are reported.
- Edits require `SaveConfig = false` (or its omission), so `wg-quick` cannot overwrite saved names or disabled devices. Symlinked configuration files can be listed but are not edited.
- Concurrent `wgctl` commands are locked. Other configuration managers must not write to the same interface or file during a change.
- Disabling a peer uses `#~` comments so it can be restored. Peers commented out using unrelated conventions are not interpreted as disabled devices.

The interface intentionally stays small: `show`, `add`, `set`, `delete`, and `backup`. Diagnostics appear where they matter; there is no separate `doctor` command.

## Development

Run the regression tests without root or a live WireGuard interface:

```sh
python3 -m unittest discover -s tests -v
```

The tests use temporary files and simulated WireGuard responses. They do not change the host's network configuration.

# wgctl

Manage devices on an existing Linux WireGuard server. No Python packages, daemon, or database required.

## Install

Requires Python 3.8+ and WireGuard tools (`wg`). Address detection uses `ip` from iproute2; install `qrencode` for terminal QR codes.

From this repository:

```sh
sudo install -m 755 wgctl /usr/local/bin/wgctl
wgctl
```

`wgctl` invokes `sudo` when needed and discovers configurations in `/etc/wireguard` and active interfaces. Your server must already be configured; `wgctl` does not set up routing, firewalls, or start interfaces.

## Common commands

```sh
wgctl                              # List devices across all interfaces
wgctl add phone                    # Print a client configuration and QR, if available
wgctl add laptop > laptop.conf     # Save a client configuration for import
wgctl set 10.8.0.3 name tablet      # Name an existing device
wgctl set tablet down              # Suspend access, keeping its configuration
wgctl set tablet up                # Restore access with the same client configuration
wgctl delete tablet                # Remove access permanently
wgctl backup > wg.tgz              # Back up the server configuration and wgctl settings
```

Select devices by name, IP, or ID from the listing. IDs are the first 12 characters of the public key; unique prefixes of at least eight characters and full keys also work. Existing unnamed peers appear as `unknown device`.

**Save generated client configurations:** their private keys are not retained on the server. If lost, delete and recreate the device.

Changes apply immediately when the interface is up, or on its next start otherwise. With multiple interfaces, select one for changes using `dev`:

```sh
wgctl show dev wg1
wgctl add phone dev wg1
wgctl set phone down dev wg1
wgctl backup dev wg1 > wg1.tgz
```

Commands accept prefixes such as `sh`, `se`, `a`, and `del`. Use `wgctl --help` or `wgctl add --help` for syntax.

## Client routing and settings

New devices get a free IPv4 address and, by default, route only to the server's tunnel address. To route internet traffic through an existing VPN gateway:

```sh
wgctl add phone --full
```

This requires working IPv4 forwarding and firewall/NAT or return routing on the server. It defaults to DNS `1.1.1.1` and captures IPv6 traffic to prevent bypassing the VPN, but does not provide IPv6 connectivity.

Most single-interface servers need no settings. Behind NAT, set the public hostname in `/etc/wgctl.conf`:

```ini
ENDPOINT=vpn.example.com
```

An explicit port is supported (`vpn.example.com:51820` or `[IPv6-address]:51820`); otherwise the server needs a fixed `ListenPort`. Optional defaults:

```ini
IFACE=wg0
CLIENT_ALLOWED=10.8.0.0/24
CLIENT_DNS=10.8.0.1
```

`CLIENT_ALLOWED` replaces the default routes; `--full` overrides it. `CLIENT_DNS` sets client DNS. Routing and DNS settings affect only new profiles. The settings file must belong to root and must not be writable by other users.

## Reading device status

`active` means loaded in WireGuard, not necessarily online. The listing combines saved and running peers; `disabled`, `interface_down`, `not_loaded`, `not_saved`, `ip_mismatch`, and `disabled_but_active` highlight their status or differences. Handshakes show elapsed time; traffic is received/sent from the server's perspective. `-` means runtime data is unavailable, and `never` means no recorded handshake.

For scripts, `-j` emits one JSON object per device, without a header or surrounding array:

```sh
wgctl -j show dev wg0
wgctl -j show | jq -r 'select(.state == "active") | .public_key'
```

Fields: `interface`, `name`, `ip` (AllowedIPs CIDRs), `state`, `handshake` (Unix timestamp; `0` for never), `received_bytes`, `sent_bytes`, and `public_key` (full key). Unavailable runtime values are `null`; an empty list produces no output.

## Operational notes

- Creating devices requires an IPv4 subnet in the server's `Address` setting. Listing supports IPv4 and IPv6.
- Editing requires `SaveConfig = false` or its omission. Peers found only in the running interface must be saved before editing; symlinked configurations cannot be edited.
- Changes preserve other peers, save the previous configuration as an owner-only `.conf.bak`, and attempt rollback if the live update fails. Avoid concurrent edits by other configuration managers.
- Client configurations and backups contain secret keys. Redirected regular files are restricted to their owner; keep them private.
- Restore a backup with `sudo tar -xzf wg.tgz -C /`, then apply it through your usual WireGuard service management. Restoring files alone does not update a running interface.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary files and simulated WireGuard responses; no root or live interface is needed.

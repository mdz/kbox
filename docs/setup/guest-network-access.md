# Letting Guests Reach kbox from a Guest Network

If your home or venue network puts guests on an isolated guest Wi-Fi (common
with UniFi, and similar on other routers), their phones can't reach a kbox
running on your main network. This page covers opening up access to just kbox.
The examples use UniFi; other routers have equivalent settings.

kbox listens on port **8000** on all interfaces, so nothing needs to change in
kbox itself.

## 1. Find kbox's IP address

On the machine running kbox (for example the Raspberry Pi):

```bash
hostname -I
```

Give it a fixed address (a DHCP reservation on your router is easiest) so the
firewall rule below and the QR code don't break when it changes.

## 2. Check kbox works locally

```bash
curl http://localhost:8000
```

This should return the kbox page's HTML.

## 3. Allow the guest network to reach kbox

### Option A: firewall rule (recommended)

In the UniFi Network controller, under **Settings → Security → Firewall**,
create a rule:

- **Type:** LAN In
- **Action:** Accept
- **Protocol:** TCP
- **Source:** your guest network
- **Destination:** the kbox IP address, port 8000

This lets guests reach kbox and nothing else on your network. Depending on your
controller version the menu names differ, and the rule must sit above the
default guest-isolation rule.

### Option B: relax guest isolation

Under **Settings → Networks**, edit the guest network and turn off the setting
that isolates guests from the private network. This is simpler but lets guests
reach **every** device on your network, not just kbox. Prefer Option A.

## 4. Test from a guest device

Connected to the guest network, open `http://<kbox-ip>:8000` in a browser. From
a laptop you can also check with `curl http://<kbox-ip>:8000`.

## 5. Optional: a friendly hostname

mDNS lets guests use a name instead of an IP, if the guest network passes
multicast traffic (many guest networks don't, so don't rely on it). On the Pi:

```bash
sudo apt-get install avahi-daemon
sudo systemctl enable --now avahi-daemon
```

Then try `http://<hostname>.local:8000`.

## Troubleshooting

- **Can't ping, or the page won't load.** Check the firewall rule and its
  order, and the isolation settings. Some networks block ICMP while still
  allowing HTTP, so test with a browser, not just ping.
- **Can ping but not load port 8000.** Confirm kbox is running and listening
  (`sudo ss -tlnp | grep 8000`), and that no host firewall on the kbox machine
  (`sudo ufw status`) is blocking it.
- **Loads, then drops quickly.** Look for guest-network timeouts, rate limiting
  or deep-packet-inspection settings on the router.

## Security

Anyone on the guest network can then open kbox. Operator controls are
PIN-protected and locked by default, but consider a dedicated VLAN for kbox if
you want stricter separation.

# Getting the most automatic map

<p><a href="../README.md#netorbit"><img src="nav/hub.svg" width="250" alt="Back to the NetOrbit map"></a></p>

Don't see a map yet? Setting up the integration tracks your devices, and the map is a dashboard card you add once: [Add the map to a dashboard](../README.md#add-the-map-to-a-dashboard).

NetOrbit draws a map the moment you add it. How much of it is right on its own depends on what your network can tell it. This page covers the three things that do most of the work for you, and how to finish the rest by hand in a minute or two.

| You have | What to do | What you get |
| --- | --- | --- |
| UniFi | Add Home Assistant's UniFi Network integration | Every wireless client placed under its access point, with names |
| Managed switches | Turn on SNMP (below) | Every device placed under the switch, and the access point, it really connects through |
| Netgear or Orbi | Add the Netgear integration and turn on its "connected AP" sensors | Clients placed under the right satellite |
| eero, Google/Nest Wifi, Deco, Velop, an ISP router | Nothing to turn on, these don't share it | Use the quick manual steps at the bottom |

You can mix these. Anything you place by hand always wins over automatic placement.


## 1. Let your router or controller integration do it

If Home Assistant already has an integration for your router or Wi-Fi system, NetOrbit reads what it knows:

- **UniFi Network:** which access point each wireless client is on, plus the client names you see in UniFi. NetOrbit adds your UniFi access points as hubs automatically.
- **Netgear / Orbi:** the Netgear integration creates a "connected AP" sensor per device, turned off by default. Turn them on (Settings > Devices & services > Netgear > entities, show disabled, enable the ones you want) and NetOrbit uses them.
- **Fritz!Box, ASUS, OpenWrt, MikroTik, Keenetic and other router integrations:** these give NetOrbit device names, even when they don't say which access point a device is on.

## 2. Turn on SNMP on managed switches

SNMP lets a switch share its address table, the list of which device sits behind which port. With that, NetOrbit works out the whole wired picture by itself, including which access point every wireless device is on (because the access point sits on a switch port too).

NetOrbit only ever reads. It never changes a setting on your switch.

### Does my switch support it?

- **Yes:** most switches sold as "managed", "smart managed" or "business": UniFi (most models), TP-Link Omada/JetStream (for example TL-SG2008), Netgear Smart Pro and Insight, Cisco Business, MikroTik.
- **No:** unmanaged switches, and some budget "smart" lines with no SNMP at all, for example **TP-Link Easy Smart** (TL-SG105E, TL-SG108E) and **Netgear Plus** (GS105E, GS108E, GS308E). NetOrbit still works with these; place devices behind them by hand.
- **Not supported by UniFi:** USW Flex and USW Ultra switches.

### UniFi

1. In UniFi Network, open **Settings > CyberSecure > Traffic Logging**.
2. Scroll to **SNMP**, turn it on, choose **v1/v2c**, and set a community (see the tip below).
3. Click **Apply Changes**.

### TP-Link Omada (controller)

1. On the site, open **Network Config > SNMP**.
2. Turn on **SNMPv1/v2c** and set the community.
3. Click **Apply**. The controller pushes it to your Omada switches.

### Grandstream GWN access points

Grandstream GWN access points list their own Wi-Fi clients over SNMP, so NetOrbit places each client under the access point it is connected to, without needing a switch table. In the access point's web page (or GWN Manager), turn on **SNMP v1/v2c** with a read-only community, then make sure the access point is a hub in NetOrbit (it is added automatically when it is recognised, or use **Configure > Add a hub**). NetOrbit reads the client list from the access point's `GRANDSTREAM-GWN-MIB` client table. This was added in 0.3.0 and has not yet been tried on every GWN model.

### Any other managed switch

Open the switch's web page and look for **SNMP** (often under Management, System or Monitoring). Turn on **SNMP v2c**, and set or keep a **read-only** community. You don't need traps, write access or SNMPv3.

### Then tell NetOrbit

Settings > Devices & services > NetOrbit > **Configure > Settings**, and enter the community under **SNMP community**. If you left it as the factory "public", there's nothing to enter.

NetOrbit asks devices once an hour whether they speak SNMP, and reads switch tables every few minutes. It's a tiny amount of traffic.

Alongside the address table, NetOrbit reads each switch's port list: the port names and descriptions you set on the switch, their link speed and whether each link is up (the standard `IF-MIB` and `BRIDGE-MIB` tables, read only). That is what the card's port view shows when you tap a switch, and what colors the lines by link speed. A switch that shares its address table but not its port list still gets a port view, built from the ports devices were seen on, without speeds.

**Who gets your community.** SNMP v2c sends the community unencrypted, so NetOrbit only sends yours to hubs, suggested hubs, and gear from makers that only build network equipment (UniFi, eero, Aruba, MikroTik and so on). Everything else is only asked with the factory "public". If your switch is from a maker that also sells plugs and cameras (TP-Link, Netgear) and NetOrbit doesn't spot it, add it as a hub under **Configure > Add a hub** with its IP, and NetOrbit will start reading it.

**Tips**

- Use a community made of **letters and numbers only**. Some switches accept symbols in their web page but quietly ignore a community containing `#` or `@`.
- Changing it from "public" is a good idea; treat it like a password. NetOrbit stores it with your Home Assistant config and leaves it out of diagnostics downloads.
- Read-only is all NetOrbit needs. If your switch has a read-write community you don't use, turn it off.

### Several subnets (VLANs) on one map

If your devices are spread over more than one subnet, such as a main network and an IoT VLAN, go to **Settings > Devices & services > NetOrbit > Configure > Settings** and list the others under **Other subnets to include**, separated by commas (for example `198.51.100.0/24`). Up to four, each up to 1024 addresses. They are swept with the main subnet and drawn on the same map.

NetOrbit tracks devices by MAC address. If Home Assistant has an address on that subnet (for example a VLAN interface under **Settings > System > Network**), it reads the MACs itself. If it only reaches the subnet through your router, it takes them from the UniFi Network integration, so set that up first; without it, devices on a routed subnet can't be tracked and stay off the map.


### FortiSwitches behind a FortiGate

FortiSwitches managed by a FortiGate over FortiLink don't answer SNMP, and the FortiGate doesn't pass their MAC tables on over SNMP either. NetOrbit reads them from the FortiGate's REST API instead, with a token that can only read.

1. On the FortiGate, go to **System > Admin Profiles > Create New**. Name it something like `netorbit-read`, set **WiFi & Switch** to **Read** and everything else to **None**, and save.
2. Go to **System > Administrators > Create New > REST API Admin**. Pick the profile you just made, turn off **PKI Group**, and under **Trusted Hosts** enter only your Home Assistant's address (for example `192.0.2.50/32`). Save, and copy the token it shows. It is shown only once.
3. In Home Assistant, go to **Settings > Devices & services > NetOrbit > Configure > Settings**. Enter the FortiGate's address under **FortiGate address** (add `:port` if its admin page isn't on 443) and paste the token. Leave **Check the FortiGate's certificate** off unless it has a certificate Home Assistant trusts.

To also see which FortiGate port each switch is on, and the FortiGate's own ports when you tap the router, add **Network** set to **Read** to the same admin profile. NetOrbit then reads the FortiGate's interfaces and its LLDP neighbours. A FortiSwitch is heard over FortiLink straight away; for other switches (UniFi and the like), turn on LLDP reception on the FortiGate ports they plug into:

```
config system interface
    edit internal1
        set lldp-reception enable
    next
end
```

With Network read NetOrbit also uses the FortiGate's ARP table, so a subnet on its own FortiGate port (a consumer Wi-Fi router or mesh system, for example) works without Home Assistant being on it: add the subnet under **Other subnets to include**, let Home Assistant reach it through a firewall policy (pings), and add the Wi-Fi router as a hub with its address on that subnet. Everything the FortiGate sees on that port is then drawn under it. A Wi-Fi router in router (NAT) mode hides its clients behind its own address; put it in bridge or access point mode to see them.

On the next scan each FortiSwitch shows up as a hub under the FortiGate, or under the FortiSwitch it hangs off, with its devices on the ports the FortiGate saw them on. Tap one to see its ports. NetOrbit only sends GET requests to monitor endpoints (`switch-controller/managed-switch/status`, `switch-controller/detected-device`, and with Network read `system/interface`, `network/lldp/neighbors` and `system/arp`), never logs the token, and leaves both the address and the token out of diagnostics.

## 3. Finish the rest by hand (about a minute)

Whatever NetOrbit couldn't work out, you can place yourself. Three ways, use whichever is quickest:

**Drag and drop.** Tap **Edit layout**, drag a device onto the hub it connects through, tap **Done**.

![Dragging devices onto a hub](howto-drag.gif)

**Confirm a possible hub.** Devices with a spinning pink ring look like network gear. Tap one and choose **It's a hub** (or **Not a hub**). Or, in **Edit layout**, drag a device straight onto the pink ring: NetOrbit asks to make it a hub and moves the device under it.

![Confirming a suggested hub](howto-confirm-hub.gif)

**On a phone,** tap a device in edit mode and pick its hub from the menu.

![Moving a device on a phone](howto-phone-move.gif)

**Paste a client list.** Most access point web pages and mesh apps list the devices connected to each unit. Copy that list, then in Home Assistant go to NetOrbit > **Configure > Paste a client list**, paste it, and pick the hub. Every device in it is placed at once.

**Ask an AI assistant.** If you use Home Assistant's MCP Server or Assist, an assistant can walk you through each piece of gear and do the placing. See [ASSISTANT.md](ASSISTANT.md).

## Better names

NetOrbit names devices from, in order: Home Assistant's own device list, router and controller integrations, Ping sensors you've set up, your router's local DNS, and the name a device announces over Bonjour. When none of those know a device, it shows the maker (for example "Apple 28:54").

**Prefer DNS and Bonjour names** (Configure > Settings, off by default) puts the name your network gives a device first, ahead of names from other integrations, and replaces hub names NetOrbit made up (such as *Router*) with the real ones. Names you typed yourself are kept. The network's name is the reverse DNS entry when your router or DNS server has one for the address, else the name the device announces over Bonjour. NetOrbit looks names up again every 12 hours.

To rename one, open its sensor in Home Assistant and change the name, or use **Configure > Track / edit a device**. Names stick to the device's MAC address, so they survive IP changes.

<p align="right"><sub><a href="../README.md#netorbit">back to the map</a></sub></p>

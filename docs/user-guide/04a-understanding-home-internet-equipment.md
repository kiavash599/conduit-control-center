---
title: Chapter 4 — Networking Fundamentals (Part 2)
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 4 — Networking Fundamentals (Part 2)

**Purpose of this part**

In the first part we learned:

- What IP is
- What Public IP is
- What Private IP is
- What NAT is

Now we want to learn:

- How devices receive an IP
- Why device IPs may change
- Why the Raspberry Pi must always have a fixed IP
- How Port Forwarding works

## 4.5 What is DHCP?

**Purpose**

Understanding how an IP is assigned to devices.

**DHCP in simple terms**

DHCP stands for Dynamic Host Configuration Protocol. This service usually runs on your home Router.

**What problem does it solve?**

Suppose the following devices connect to the network:

- A new mobile phone
- A new laptop
- A new Raspberry Pi

Without DHCP, you would have to set an IP manually for each device. DHCP does this automatically.

**Real-world example**

When the Raspberry Pi powers on:

Raspberry Pi

│

▼

Request IP Address

│

▼

Router DHCP Server

│

▼

192.168.1.50

the Router provides an IP to the Raspberry Pi.

**Advantages**

- Easier network management
- No need to set IP manually
- Fewer configuration errors

![The router's DHCP server assigns an IP address on request](../diagrams/svg/network-dhcp.svg)

*The router's DHCP server automatically assigns an IP address to a device when it joins the network.*

## 4.6 Why do device IPs change?

**Purpose**

Understanding the normal behavior of DHCP.

**Important note**

DHCP usually does not assign IPs permanently; instead, it leases them for a defined period called the Lease Time.

**Example**

Today:

Raspberry Pi

192.168.1.50

A few days later:

Raspberry Pi

192.168.1.107

Both situations can be completely normal.

**Why does this happen?**

Because DHCP is responsible for managing the available IPs, the Router may assign a new IP when the network changes or the Lease expires.

**Is this a problem for normal use?**

Usually not. For a:

- Laptop
- Mobile
- Tablet

a changing IP often does not matter. But for the Raspberry Pi, which is meant to run a permanent service, it does.

## 4.7 What is DHCP Reservation?

**Purpose**

Keeping the Raspberry Pi's IP fixed.

**The problem**

In the previous section we saw that DHCP may change a device's IP, but we want to always know which IP the Raspberry Pi is on.

**The solution**

DHCP Reservation

or:

Static Lease

**How does DHCP Reservation work?**

Every network card has a unique identifier:

MAC Address

Example:

B8:27:EB:12:34:56

The Router can have a rule similar to the following:

If the MAC Address equals:

B8:27:EB:12:34:56

always assign the following IP:

192.168.1.50

**Result**

Each time the Raspberry Pi powers on, it will receive 192.168.1.50.

![A DHCP reservation ties a device's MAC address to a fixed IP](../diagrams/svg/network-dhcp-reservation.svg)

*A DHCP reservation ties a device's MAC address to a fixed IP so it always receives the same address.*

MAC Address

│

▼

Router DHCP

│

▼

Always Assign

192.168.1.50

## 4.8 Why must the Raspberry Pi always have a fixed IP?

**Purpose**

Understanding the most important reason for using DHCP Reservation.

**Example**

Suppose you have configured Port Forwarding like this:

Port 443

│

▼

192.168.1.50

Everything works correctly. But a few days later DHCP changes this IP:

Raspberry Pi

192.168.1.107

while Port Forwarding still points to:

192.168.1.50

**Result**

Incoming connections no longer reach the Raspberry Pi.

You might think:

- Conduit is broken
- Cloudflare is broken
- CCC has a problem

when the problem was only the IP change.

**Important rule**

⚠️

Before performing Port Forwarding, DHCP Reservation must already be done.

## 4.9 What is Port Forwarding?

**Purpose**

Allowing the Router to send incoming traffic to the Raspberry Pi.

**The problem**

In the first part we learned about NAT. NAT makes the devices inside the home invisible from the internet, which is very useful for security.

**But it creates a problem**

If someone on the internet wants to connect to the Raspberry Pi, the Router does not know which device to send the request to.

**Port Forwarding is the solution to this problem**

Port Forwarding tells the Router:

If traffic is received on this port

send it to the Raspberry Pi

**Example**

Internet

│

▼

Router

Port 443

│

▼

192.168.1.50

**Simple analogy**

Suppose you have a large building. All mail first arrives at the building's front desk, and the desk must know which unit to send each letter to. Port Forwarding plays a similar role.

![Port forwarding sends inbound traffic on a port to the Raspberry Pi](../diagrams/svg/network-port-forwarding.svg)

*Port forwarding tells the router to send inbound traffic on a given port to the Raspberry Pi.*

## 4.10 Connection validation

**Purpose**

Ensuring the network settings have been done correctly.

**Checking the Raspberry Pi's current IP**

hostname -I

**Checking the MAC Address**

ip link

**Checking SSH access**

From the computer:

ssh ubuntu@192.168.1.50

**Items that must be confirmed**

✓ The Raspberry Pi always receives the same IP.

✓ SSH works.

✓ The Router has applied the DHCP Reservation.

✓ The Raspberry Pi is visible in the DHCP Clients list.

## 4.11 Troubleshooting

**The Raspberry Pi gets a different IP each time**

Check that:

- The DHCP Reservation is saved.
- The correct MAC Address is selected.

**Port Forwarding does not work**

Check that:

- DHCP Reservation is done.
- The correct Raspberry Pi IP is selected.
- The Rule is saved on the Router.

**The Raspberry Pi is not visible in DHCP Clients**

Check that:

- The network cable is connected.
- Wi-Fi is enabled.
- The device is powered on.

**Conclusion of this chapter**

You now know the core networking concepts needed for setting up CCC:

✓ Public IP

✓ Private IP

✓ NAT

✓ DHCP

✓ DHCP Reservation

✓ Port Forwarding

These concepts will be used many times in the following chapters.

**Next chapter**

In the next chapter we will get familiar with:

- Domain
- DNS
- Subdomain
- Cloudflare
- API Token

and learn how to prepare our domain name for use with CCC.

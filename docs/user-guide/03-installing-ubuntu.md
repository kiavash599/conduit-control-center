---
title: Chapter 3 — Installing Ubuntu on Raspberry Pi
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 3 — Installing Ubuntu on Raspberry Pi

## Purpose of this chapter

In this chapter we install the Ubuntu operating system on the Raspberry Pi and prepare it for the rest of the guide.

By the end of this chapter:

✓ Ubuntu is installed on the Raspberry Pi.

✓ The Raspberry Pi is connected to the network.

✓ SSH is enabled and usable.

✓ You can connect to the Raspberry Pi from your computer.

✓ The operating system is updated to the latest version.

## 3.1 Why Ubuntu?

**Purpose**

To understand why Ubuntu is the recommended operating system.

**Why?**

One of the first questions many users ask is: why Ubuntu, and not Raspberry Pi OS? In reality, CCC and Conduit can run on a variety of Linux distributions. Ubuntu was chosen for this project because it is well known and widely used, has extensive documentation, offers long-term support (LTS), is common in server environments, and receives regular security updates. Throughout this guide, all steps are based on Ubuntu 22.04 LTS.

**Note**

If CCC is validated on other operating systems in the future, documentation for them will be provided separately.

## 3.2 What do we need in this chapter?

**Required equipment**

- Raspberry Pi
- MicroSD memory card
- MicroSD card reader
- A Windows, Linux, or macOS computer
- An internet connection

## 3.3 What is Raspberry Pi Imager?

**Purpose**

To get familiar with the official memory-card preparation tool.

**Why?**

To install the operating system on the Raspberry Pi, the operating system image must first be written to the memory card. The simplest way to do this is with the official Raspberry Pi Imager software.

**Getting the software**

Official website:

https://www.raspberrypi.com/software/

**Validation**

After installation, you should be able to run Raspberry Pi Imager.

**Screenshot needed**

Screenshot:

raspberry-pi-imager-main-window.png

## 3.4 Choosing the operating system

After running Raspberry Pi Imager:

**Step 1**

Click **Choose Device** and select your Raspberry Pi model.

**Step 2**

Click **Choose OS**, then select:

Other General Purpose OS

↓

Ubuntu

↓

Ubuntu Server 22.04 LTS (64-bit)

**Why the Server version?**

This project does not need a graphical environment. The Server version is lighter, consumes fewer resources, and is better suited to running services.

**Screenshot needed**

Screenshot:

ubuntu-selection.png

## 3.5 Choosing the memory card

Click **Choose Storage** and select the correct memory card.

**Warning**

All data on the memory card will be erased. Back up any important data before continuing.

## 3.6 Advanced settings in Raspberry Pi Imager

**Purpose**

To configure the Raspberry Pi before its first boot.

**Why?**

Configuring these settings now means you will not need a monitor or keyboard, and SSH will be enabled from the very start.

**Enabling advanced settings**

After choosing the operating system and the memory card, click **Next**, then select **Edit Settings**.

**Hostname**

Suggestion:

conduitpi

or:

my-conduit-node

**Username**

Example:

ubuntu

or:

conduit

**Password**

Choose a strong password — for example, at least 12 characters with uppercase and lowercase letters, numbers, and symbols.

**SSH**

Be sure to enable:

Enable SSH

Login method:

Use password authentication

**Timezone**

Choose your time zone.

Example:

Europe/Stockholm

**Locale**

Example:

en_US.UTF-8

**Wi-Fi**

If you use Wi-Fi, enter the network name (SSID) and password. If you use Ethernet, this section is not necessary.

**Screenshot needed**

Screenshot:

advanced-settings.png

## 3.7 Writing the operating system to the memory card

After confirming the settings, click **Write**. The writing process may take a few minutes. When it finishes, you will see:

Write Successful

**Screenshot needed**

Screenshot:

write-complete.png

## 3.8 First boot

Insert the memory card into the Raspberry Pi, then:

- Connect the network cable.
- Or have Wi-Fi configured in advance.
- Connect the power.

The Raspberry Pi will now begin to boot.

**Note**

The first boot may take a few minutes. This is normal.

## 3.9 Finding the Raspberry Pi on the network

**Purpose**

To find the device's IP Address.

**Method 1**

Log in to the router's panel and check the DHCP Clients list.

**Method 2**

Use the hostname:

conduitpi.local

**Method 3**

Network scanning tools.

## 3.10 Connecting via SSH

**Windows**

Open PowerShell.

Example:

ssh ubuntu@192.168.1.50

or:

ssh conduit@192.168.1.50

**First connection**

You will see a message similar to the following:

Are you sure you want to continue connecting?

Answer:

yes

**Validation**

After logging in, you should see the Linux prompt.

Example:

ubuntu@conduitpi:~$

## 3.11 Updating Ubuntu

**Purpose**

To ensure the latest security fixes are installed.

**Run**

sudo apt update

Then:

sudo apt upgrade -y

**Note**

Depending on your internet speed, this may take a few minutes.

## 3.12 Validation

The command:

hostnamectl

should display system information.

The command:

lsb_release -a

should display the Ubuntu version.

The command:

ip addr

should display the network IP Address.

## 3.13 Troubleshooting

**SSH does not connect**

Check that:

- The Raspberry Pi is powered on.
- It is connected to the network.
- You used the correct IP.

**The Raspberry Pi is not visible on the network**

Check that:

- The network cable is connected.
- Wi-Fi is configured correctly.
- The router's DHCP is enabled.

**The boot process is taking a long time**

A few minutes of waiting on the first boot is normal. If it takes too long:

- Check the memory card.
- Write the operating system again.

**Next chapter**

In the next chapter we get familiar with home network equipment and examine:

- What is an ISP?
- What is a Modem?
- What is a Router?
- What is the difference between a Modem and a Router?
- Why is Port Forwarding done on the Router?

---
title: Chapter 2 — Hardware Requirements
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 2 — Hardware Requirements

## Purpose of this chapter

Before installing any software, we need to make sure we have suitable hardware.

This chapter examines:

- Which Raspberry Pi model is suitable?
- What type of memory card should we use?
- What power supply is appropriate?
- Is Wi-Fi enough, or is a wired connection better?
- What are the minimum and recommended hardware specifications?

By the end of this chapter you will be able to confidently decide whether your current equipment is suitable for running CCC.

## 2.1 What is Raspberry Pi?

**Purpose**

To get familiar with the device on which CCC and Conduit will run.

**Why?**

Conduit and CCC can run on many types of Linux systems, but the Raspberry Pi is one of the most popular choices because it draws very little power, is relatively inexpensive, can run around the clock, has a large user community, and is extensively documented. You can think of the Raspberry Pi as a small but complete computer, roughly the size of a bank card.

## 2.2 Which Raspberry Pi model is suitable?

**Validated configuration**

Raspberry Pi 4 (2 GB RAM)

CCC has been successfully run and validated on this configuration.

**Recommended configuration**

Raspberry Pi 4 (4 GB RAM)

**Future validation (planned)**

Raspberry Pi 3 B (1 GB RAM)

**Other options**

Raspberry Pi 4 with 8GB RAM

If you have it, it is an excellent choice, but it is not required for running CCC.

**Raspberry Pi 5**

At the time of writing, the Raspberry Pi 5 is also a good option and performs faster than the Raspberry Pi 4.

## 2.3 Memory card (MicroSD)

**Purpose**

To choose the storage on which the operating system is installed.

**Why does it matter?**

The entire operating system, along with its settings and required files, is stored on the memory card. A low-quality card can lead to system slowness, file corruption, data loss, or operating system failure.

**Minimum recommended**

32GB

**Main recommendation**

64GB

**Recommended brands**

- Samsung
- SanDisk
- Kingston

Preferably, use well-known, genuine models.

## 2.4 Power supply

**Purpose**

To ensure a stable power supply for the Raspberry Pi.

**Why does it matter?**

Many problems that users mistakenly blame on software are in fact caused by an inadequate power supply. A weak supply can lead to sudden reboots, system instability, memory card corruption, or disrupted network performance.

**Recommendation**

Use the official Raspberry Pi adapter, or a quality power supply with sufficient output.

## 2.5 Network connection

**Purpose**

To choose how the Raspberry Pi connects to the home network.

**Option 1: Wi-Fi**

Advantages:

- No cable needed.
- Easier to set up.

Disadvantages:

- Lower stability
- Sensitivity to distance and obstacles
- Higher latency

**Option 2: Ethernet (network cable)**

Advantages:

- Greater stability
- Better speed
- Lower latency
- Suitable for 24-hour operation

**Recommendation**

Use a wired connection if possible. For a continuously running node, Ethernet is the better choice.

## 2.6 Required accessories

For the initial setup, you may need the following:

- Network cable
- MicroSD memory card
- MicroSD card reader
- A suitable power supply
- A personal computer to prepare the memory card

## 2.7 Optional equipment

This equipment is not required, but it can be useful.

**Protective case (Case)**

Better physical protection.

**Heatsink**

Helps cool the system.

**Fan**

For hot environments or extended use.

**UPS**

Prevents a sudden shutdown during a power outage.

## 2.8 Recommended configuration

If you want a stable, low-maintenance node, the following combination is recommended:

- Raspberry Pi 4 (4GB RAM)
- MicroSD 64GB
- Ethernet
- A quality power supply
- A protective case

This combination is more than sufficient for most users.

**Chapter validation**

Before continuing, you should be able to answer the following:

1. Which Raspberry Pi model will you use?
2. What capacity have you chosen for the memory card?
3. Are you using Wi-Fi or Ethernet?
4. Is your power supply suitable?
5. Have you obtained all the necessary equipment?

If the answers are clear, you are ready to move on to the next chapter.

**Next chapter**

In the next chapter we will install the Ubuntu operating system on the Raspberry Pi and perform the device's first startup.

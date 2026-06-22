---
title: Chapter 1 — Introduction
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 1 — Introduction

## Purpose of this chapter

Before we prepare the Raspberry Pi, configure DNS, or install CCC, it helps to understand the problem this project was built to solve.

This chapter introduces the basic concepts:

- What is internet censorship?
- What is Conduit?
- What is Conduit Control Center (CCC)?
- Why do people around the world run Conduit nodes?
- What will this guide teach you?

## 1.1 What is internet censorship?

**Purpose**

To understand the problem that Conduit was designed to help solve.

**Why does this matter?**

In many parts of the world, free and unrestricted access to the internet is not always possible. In some countries, for example:

- News websites are blocked.
- Social networks are filtered.
- Messaging services are restricted.
- Access to educational or scientific resources becomes difficult.

Under these conditions, users may be unable to reach information that is readily available elsewhere in the world. The Conduit project was designed to increase access to the free and open internet.

## 1.2 Why does this project exist?

**Purpose**

To understand the role of volunteers in the Conduit network.

**Why?**

Conduit is built on a simple idea: volunteers around the world each contribute a portion of their internet resources to the network. Any single node's contribution may be small on its own, but when many people across different countries take part, they create significant combined capacity. This is why contributors play an important role in the network's growth and stability.

## 1.3 What is Conduit?

**Purpose**

To get familiar with the network's core software.

**Conduit in simple terms**

Conduit is software that runs on a server or computer and lets you dedicate part of your internet capacity to the Conduit network. You can think of it as the system's main engine: it carries out the network's core tasks and manages communication with the other parts of the Conduit ecosystem.

**What you need to know**

To use this guide, you do not need to understand Conduit's internal details. It is enough to know that:

- Conduit is the network's core software.
- It runs on the Raspberry Pi.
- It must be running for you to participate in the network.

Later chapters cover how to install, set up, and manage it.

## 1.4 What is Conduit Control Center (CCC)?

**Purpose**

To get familiar with the software we will install in this guide.

**Why was CCC built?**

Running Conduit directly is possible for many users, but managing it day to day is not always simple. For example, you may want to view the system status, see your contribution level, change Conduit settings, back up and restore your data, or check the system's health. CCC was created to make these tasks easier.

**CCC in simple terms**

Conduit Control Center is a web-based management panel that runs on the Raspberry Pi. Once it is installed, you can connect to it from a web browser and perform many management tasks without running complex commands.

## 1.5 How does CCC help non-expert users?

**Purpose**

To understand the design philosophy behind CCC.

**Why?**

Not everyone who wants to take part in the Conduit network is an expert in Linux, networking, or programming. CCC's design therefore follows a few important principles:

**Simplicity**

Common tasks should be achievable through the web interface wherever possible.

**Transparency**

The user should be able to see the system status at a glance.

**Security**

Default settings should be as safe as possible.

**Privacy**

CCC displays only aggregate information and is not designed to analyze end users.

## 1.6 What does this guide teach?

By the end of this guide you will be able to:

- Prepare the Raspberry Pi.
- Install Ubuntu.
- Understand basic home networking concepts.
- Configure DHCP Reservation.
- Set up Port Forwarding.
- Configure a Domain and DNS.
- Set up Cloudflare.
- Install CCC.
- Use the Dashboard.
- Back up your data.
- Restore your data if needed.
- Use the Ryve Claim capability.
- Troubleshoot common problems.

**Chapter validation**

After reading this chapter, you should be able to answer the following:

1. What is the main goal of Conduit?
2. What is the role of a Conduit node in the network?
3. What is the difference between Conduit and CCC?
4. Why is CCC useful for non-expert users?
5. What topics does this guide cover?

If you can answer these, you are ready to move on to the next chapter.

**Next chapter**

The next chapter introduces the required hardware and examines what equipment you need to set up a Conduit node.

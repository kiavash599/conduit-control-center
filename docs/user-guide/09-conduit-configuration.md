---
title: Chapter 9 — Conduit Configuration
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 9 — Conduit Configuration

## Purpose of this chapter

In the previous chapter we got familiar with the Contribution Advisor. The Advisor only provides suggestions; to apply those suggestions we must use the:

Conduit Configuration

section.

By the end of this chapter you will know:

✓ What the difference between Configured and Effective is

✓ How Conduit capacity is set

✓ How the bandwidth limit is set

✓ What Reduced Mode is

✓ How changes are applied

✓ How automatic Rollback protects the system

## 9.1 What is Conduit Configuration?

**Purpose**

Getting familiar with the Conduit settings page.

This section is used to manage Conduit's main parameters.

In version v0.3.0 the following settings are displayed on this page:

Max Common Clients

Bandwidth Limit

Reduced Mode

Max Personal Clients

**Important note**

Not all of these items are editable.

## 9.2 Configured and Effective

**Purpose**

Understanding the most important concept of this chapter.

On the Configuration page you will see two columns:

Configured

Effective

**What is Configured?**

It is the value that CCC has saved.

This value is read from the configuration files.

**What is Effective?**

It is the value that Conduit is currently using.

This value is obtained from Conduit's live Metrics.

**Example**

Suppose:

Configured

=

250

but:

Effective

=

200

**Is the system broken?**

No. It may simply be that a Restart has not been done yet, so Conduit is still running with the previous settings. This situation is normal.

## 9.3 Maximum Common Clients

**Purpose**

Setting the maximum number of common clients.

This parameter specifies how many public users can use the station simultaneously.

**Example**

50

means:

Up To 50 Concurrent Public Clients

**Note**

The Advisor usually provides suggestions on this same value.

## 9.4 Choosing a suitable value

**Purpose**

Choosing a reasonable capacity.

**Too low a value**

Example:

10

may limit the station's capacity.

**Too high a value**

Example:

1000

may cause excessive load on the hardware.

**Recommendation**

In most cases, the Advisor's suggestions are a good starting point.

## 9.5 Bandwidth Limit

**Purpose**

Limiting Conduit's bandwidth.

This value specifies the maximum bandwidth Conduit may use.

**Example**

40 Mbps

means:

Maximum 40 Megabits Per Second

**Use**

When you do not want Conduit to consume all of the internet bandwidth.

## 9.6 Unlimited bandwidth

**Purpose**

Understanding the Unlimited state.

In CCC:

-1

means:

Unlimited

**Warning**

Being unlimited does not mean forced consumption.

Conduit uses bandwidth only when there is demand.

## 9.7 What is Reduced Mode?

**Purpose**

A conservative capacity reduction during low-traffic hours.

Reduced Mode is a capability in Conduit.

In this mode:

- The number of clients is reduced
- More conservative limits are applied

**Purpose**

Reducing resource consumption during low-traffic hours.

## 9.8 Reduced Mode hours

**Purpose**

Understanding how the time window is defined.

The Reduced Mode window is defined with:

Start Time

End Time

**Very important note**

All times are stored as:

UTC

**Warning**

UTC is different from your local time. In Sweden, the time difference changes depending on the season, so when setting the hours you must pay attention to UTC.

## 9.9 Max Personal Clients

**Purpose**

Understanding this parameter.

This value is displayed on the Configuration page.

But:

Editable Here

=

No

**Why?**

Because the owner of this setting is:

Personal Mode

To change it you must go to the Personal Mode section.

## 9.10 Applying changes

**Purpose**

Understanding the Apply process.

When you click:

Apply

CCC does not only change the configuration file.

Instead the following process runs:

Validate

↓

Save

↓

Restart Conduit

↓

Health Check

↓

Success

## 9.11 Restart Conduit

**Purpose**

Understanding the reason for Restart.

Conduit reads many settings only at startup.

So:

Apply

↓

Restart

is necessary.

**Note**

If there is no change:

a Restart is not done.

## 9.12 Health Verification

**Purpose**

Ensuring the system is healthy after Apply.

After the Restart:

CCC waits for Conduit to become healthy again.

**Success criterion**

Success does not only mean that the Helper Script ran.

Real success is:

Conduit Healthy

## 9.13 Automatic Rollback

**Purpose**

Protecting the system against invalid settings.

If Conduit does not return healthy after the changes:

CCC automatically performs:

Rollback

**Process**

Apply New Settings

↓

Restart

↓

Health Check

↓

Failure

↓

Restore Previous Settings

↓

Restart

**Benefit**

Reducing the chance of losing access to the station.

## 9.14 Validation errors

**Purpose**

Knowing common errors.

**Invalid value**

Example:

Negative Client Count

is rejected.

**Invalid time**

Example:

25:99

is rejected.

**Invalid format**

Any value outside the allowed range is not accepted.

## 9.15 Troubleshooting

**Configured and Effective are different**

Check:

Restart Completed?

**Changes are not applied**

Check:

Conduit Healthy?

**Reduced Mode does not behave as expected**

Check that you have entered:

UTC Times

correctly.

**A Rollback occurred**

Check:

System Logs

to find the cause of the failure.

## 9.16 Conclusion of this chapter

Now you know:

✓ What Configured is

✓ What Effective is

✓ How Capacity is set

✓ How Bandwidth is set

✓ How Reduced Mode works

✓ How the Apply Workflow runs

✓ How Rollback protects the system

**Next chapter**

In the next chapter we will examine:

Personal Mode

and learn how to create a private space for personal use.

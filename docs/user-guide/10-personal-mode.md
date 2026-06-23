---
title: Chapter 10 — Personal Mode
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 10 — Personal Mode

## Purpose of this chapter

So far we have become familiar with:

- Dashboard
- Contribution Advisor
- Conduit Configuration

Now we reach one of CCC's dedicated capabilities:

Personal Mode

Personal Mode allows you to create a private space for personal use or limited sharing with trusted people.

## By the end of this chapter

✓ You will understand the concept of Personal Mode.

✓ You will know the concept of Identity.

✓ You will know QR Pairing.

✓ You will know Max Personal Clients.

✓ You will learn enabling and disabling.

✓ You will know Regenerate and Restore.

✓ You will know the security considerations.

✓ You will know the Backup limitations.

## 10.1 What is Personal Mode?

**Purpose**

Understanding the philosophy of Personal Mode.

In normal mode:

Conduit provides its capacity to public users.

Personal Mode allows you to dedicate part of the capacity to personal or limited-group use.

**Important note**

Personal Mode is a feature independent of public users.

## 10.2 The two main parts of Personal Mode

**Purpose**

Understanding the actual architecture of the capability.

Personal Mode consists of two independent parts:

Identity (Compartment)

and

Max Personal Clients

**Important note**

These two parts are independent.

## 10.3 What is Identity?

**Purpose**

Knowing the Personal Mode identity.

Identity is a unique identifier created by Conduit.

This identifier is called the Compartment.

**Storage location**

Conduit stores it in the system.

**Properties**

✓ Unique

✓ Persistent

✓ No expiration date

✓ Reproducible

## 10.4 Creating an Identity

**Purpose**

Creating the first Identity.

In Settings → Personal Mode, select the option:

Create Identity

Then enter a Display Name.

**Example**

Kiavash Personal Access

**Note**

The Display Name is not confidential; it is used only for easier identification.

## 10.5 Creating an Identity does not mean it is active

**Important warning**

⚠️

Many users assume:

Identity Created

=

Personal Mode Active

This assumption is wrong.

The fact:

Identity Created

+

Max Personal Clients > 0

=

Personal Mode Active

## 10.6 Visible states

**Purpose**

Understanding the user interface states.

**Not Set Up**

No Identity exists.

**Created – Inactive**

An Identity has been created.

But Max Personal Clients = 0.

**Active**

An Identity exists.

And Max Personal Clients > 0.

![Personal Mode states: an Identity alone is Inactive; it becomes Active only when Max Personal Clients > 0 is applied](../diagrams/svg/personal-mode-states.svg)

*Personal Mode becomes Active only when an Identity exists and Max Personal Clients is set above 0 and applied; setting it back to 0 returns to the Inactive state.*

## 10.7 What is QR Pairing?

**Purpose**

Getting familiar with the access-sharing method.

After creating an Identity, CCC can generate a QR Code. This QR is used for Pairing.

**Important note**

The QR is built from the Identity.

## 10.8 What information does the QR Code contain?

**Purpose**

Understanding the security importance of the QR.

The QR contains the Identity information.

Including:

Compartment Identity

Display Name

**Very important warning**

⚠️

The QR Code is considered a Credential.

Treat it like a password.

Share it only with trusted people.

## 10.9 Does the QR expire?

**Short answer**

No.

**Important note**

The QR does not have an expiration time.

As long as the Identity does not change:

the QR remains valid.

## 10.10 Displaying the QR

**Purpose**

Understanding how it is displayed.

CCC generates the QR in the browser, where it is displayed only temporarily. After you close the window, the QR display is removed.

**Note**

CCC does not store the QR in the database.

## 10.11 Max Personal Clients

**Purpose**

Setting the Personal Mode capacity.

This value determines how many personal clients can be used simultaneously.

**Allowed range**

0

to

1000

## 10.12 What does a value of zero mean?

**Purpose**

Understanding how to disable.

If:

Max Personal Clients = 0

then:

Personal Mode is disabled.

**Important note**

The Identity is not deleted; only its use is stopped.

## 10.13 Enabling Personal Mode

**Purpose**

Enabling the capability.

Steps: create an Identity, set Max Personal Clients to a value greater than 0, then Apply.

After Apply, Conduit is restarted.

## 10.14 Restart Behavior

**Purpose**

Understanding when Restarts happen.

**Requires Restart**

✓ Changing Max Personal Clients

✓ Regenerate Identity (when active)

✓ Restore Identity (when active)

**Without Restart**

✓ Creating an Identity

✓ Viewing the QR

✓ Viewing the status

## 10.15 What is Regenerate?

**Purpose**

Creating a new Identity.

Regenerate means:

Create New Identity

**Result**

All previous QRs become invalid.

A new QR will be generated.

**Warning**

⚠️

Before Regenerate, make sure the people who need it will receive the new QR.

## 10.16 Restore Previous Identity

**Purpose**

Restoring the previous Identity.

CCC keeps an internal backup of the previous Identity, so if needed you can restore it.

**Use**

Example:

Regenerate Performed By Mistake

## 10.17 Backup and Personal Mode

**Purpose**

Understanding an important Backup limitation.

**Very important warning**

⚠️

Backup does not save everything.

**Items saved**

✓ Display Name

✓ Max Personal Clients

**Items not saved**

✗ Identity

✗ Compartment File

**Result**

After Restore:

you may need to create a new Identity.

## 10.18 What happens after Restore?

**Example**

Before Backup:

Display Name

=

Kiavash Personal

Max Personal Clients

=

5

After Restore:

these same values return.

But the actual Identity is not restored.

So:

the old QRs will not be valid.

## 10.19 Personal Mode security

**Purpose**

Getting familiar with best practices.

**Recommendation 1**

Share the QR only with trusted people.

**Recommendation 2**

Avoid storing the QR in public places.

**Recommendation 3**

If there is any chance the QR has been exposed, perform Regenerate.

**Recommendation 4**

Do not treat the Display Name as confidential information.

**Recommendation 5**

Treat the Identity as an important Secret.

## 10.20 Troubleshooting

**Personal Mode does not become active**

Check that Max Personal Clients > 0.

**The QR is not displayed**

Check that:

an Identity has been created.

**The old QR does not work**

It may be that:

Regenerate

was performed.

**There is a problem after Restore**

You may need to create a new Identity.

## 10.21 Conclusion of this chapter

Now you know:

✓ What Personal Mode is.

✓ What Identity is.

✓ What QR Pairing is.

✓ How it is enabled or disabled.

✓ What Regenerate does.

✓ What Restore does.

✓ What is saved in a Backup.

✓ What is not saved in a Backup.

✓ How to keep Personal Mode secure.

**Next chapter**

In the next chapter we will examine:

Ryve Integration

and learn how to create a Claim QR and use it.

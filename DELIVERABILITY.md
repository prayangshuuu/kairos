# Kairos Email Deliverability Guide

Proper email deliverability setup is critical for Kairos. Booking confirmations that land in spam are essentially failed product interactions.

We use **Resend** as our transactional email provider because of its developer-friendly API, fast SMTP interface, modern deliverability practices (automatic dedicated IP pooling, strong sender reputation), and a generous free tier (3,000 emails/month).

To ensure high deliverability, you must configure DNS records for the sending domain (`mail.joinkairos.me`). We use a dedicated subdomain (`mail.`) so that our transactional reputation is isolated from any corporate or marketing email sent from the root domain.

## Required DNS Records for joinkairos.me

Add the following records to your DNS provider (e.g., Cloudflare, Route53, Namecheap) for `joinkairos.me`.

### 1. Domain Verification & SPF
SPF (Sender Policy Framework) prevents domain spoofing by authorizing Resend's IPs to send emails on your behalf.
- **Type:** `TXT`
- **Name/Host:** `bounces.mail`
- **Value:** `v=spf1 include:amazonses.com ~all`
*(Note: Resend operates on top of AWS SES for delivery under the hood, hence the `amazonses.com` include.)*

### 2. DKIM (DomainKeys Identified Mail)
DKIM cryptographically signs your emails so receiving servers know they haven't been tampered with. Missing DKIM is the single largest cause of emails landing in spam.
You need to retrieve the exact DKIM key from the Resend dashboard after adding your domain. It will look like this:
- **Type:** `TXT`
- **Name/Host:** `resend._domainkey.mail`
- **Value:** `p=YOUR_PUBLIC_KEY_FROM_RESEND...`

### 3. Return-Path (MX)
This handles hard bounces and out-of-office replies.
- **Type:** `MX`
- **Name/Host:** `bounces.mail`
- **Value:** `feedback-smtp.us-east-1.amazonses.com`
- **Priority:** `10`

### 4. DMARC (Domain-based Message Authentication, Reporting, and Conformance)
DMARC ties SPF and DKIM together. It tells receiving servers what to do if an email fails authentication. We start with `p=none` for monitoring and should tighten to `quarantine` or `reject` later.
- **Type:** `TXT`
- **Name/Host:** `_dmarc.mail`
- **Value:** `v=DMARC1; p=none; rua=mailto:postmaster@joinkairos.me;`

## Webhook Configuration

After verifying the domain in Resend, add a webhook in the Resend dashboard:
- **Endpoint URL:** `https://api.joinkairos.me/webhooks/resend/` (or your production URL)
- **Events to listen for:** `email.bounced`, `email.complained`

This webhook allows Kairos to automatically track hard bounces and stop sending to invalid addresses, preserving your sender reputation.

# Kairos Email Deliverability Setup

To ensure transactional emails (booking confirmations, etc.) land in users' inboxes and not their spam folders, you must configure DNS records for your domain (`joinkairos.me`). We use [Resend](https://resend.com) as our transactional email provider.

We use a dedicated subdomain for sending emails (`mail.joinkairos.me`) to isolate transactional email reputation from the root domain.

## Required DNS Records

Please add the following records to your domain's DNS settings (e.g., Cloudflare, Route53):

### 1. SPF (Sender Policy Framework)
**Why it matters:** SPF tells receiving mail servers which services are authorized to send email on behalf of your domain, preventing spoofing.

* **Type:** `TXT` (or `MX` depending on Resend setup)
* **Name:** `mail` (or `mail.joinkairos.me` depending on your DNS provider)
* **Value:** `v=spf1 include:amazonses.com ~all` *(Note: Follow Resend's exact instructions in the dashboard)*

### 2. DKIM (DomainKeys Identified Mail)
**Why it matters:** DKIM adds a cryptographic signature to your emails, proving they were actually sent by your domain and haven't been tampered with.

* **Type:** `TXT`
* **Name:** `resend._domainkey.mail`
* **Value:** *(Retrieve this exact key from your Resend Dashboard -> Domains -> Add Domain)*

### 3. DMARC (Domain-based Message Authentication, Reporting, and Conformance)
**Why it matters:** DMARC tells receiving servers what to do if an email fails SPF or DKIM checks.

* **Type:** `TXT`
* **Name:** `_dmarc.mail`
* **Value:** `v=DMARC1; p=none; rua=mailto:postmaster@joinkairos.me;`

*(Note: We start at `p=none` for monitoring. After a few weeks of monitoring reports, tighten to `p=quarantine` or `p=reject` to protect your domain.)*

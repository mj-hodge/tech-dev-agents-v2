## Adversarial Security Review -- EPIC-008 SP-API Notifications Archive PR #267

*Adversarial analysis -- 2026-04-27*

Seed document analysis for EPIC-008: SP-API -> SQS -> Lambda/ACA Job -> S3 archive.
A seed review surfaces design-level risk only. Several decisions here will be hard to reverse later.

---

### 1. Data Exposure Risks

**H-1 (HIGH) -- PII and sensitive commercial data in indefinite raw archive; GDPR/ToS risk**

The archives contain:
- ORDER_CHANGE: buyer name, shipping address, email (PII -- GDPR/CCPA), order items, pricing.
- TRANSACTION_UPDATE: seller financial transactions, settlement amounts, refund reversals.
- ANY_OFFER_CHANGED / PRICING_HEALTH: GC real-time bid/offer prices and competitor pricing.
- DETAIL_PAGE_TRAFFIC_EVENT: hourly per-ASIN traffic metrics, likely restricted by Amazon data-use agreement.

The seed acknowledges none of this. No data classification, no Amazon SP-API data-use agreement compliance
analysis for bulk notification retention, no PII redaction, no buyer-data retention limits.
The lifecycle policy is indefinite/never-delete -- legally risky for ORDER_CHANGE (buyer PII)
and potentially violates Amazon SP-API ToS for bulk notification data storage.

Required for Phase 6b: Data classification per notification type. ORDER_CHANGE requires a retention cap
and SSE-KMS (not SSE-S3). Consult legal on GDPR right-to-erasure vs. write-once-never-delete.

**M-1 (MEDIUM) -- SSE-S3 insufficient for PII/financial tiers**

SSE-S3 (AWS-managed keys) does not prevent access by IAM-authorized actors or AWS support.
CMK-based SSE-KMS is required for key-rotation auditability and access revocation.
Fix: Mandate SSE-KMS with a CMK for Tier 1 data from the start -- do not defer to Phase 6b.

---

### 2. IAM / KMS Controls

**H-2 (HIGH) -- KMS key policy grants Amazon principal kms:GenerateDataKey+Decrypt with Resource:* (all account keys)**

The seed copies verbatim from Amazon docs this KMS policy:
  Principal: arn:aws:iam::437568002678:root
  Action: [kms:GenerateDataKey, kms:Decrypt]
  Resource: *

Resource:* in a KMS key policy means ALL keys in the account. Amazon SP-API service principal
can decrypt using any CMK in the GC AWS account -- not just the SQS queue key.
Other SSE-KMS-protected resources (RDS, other S3 buckets) become decryptable by Amazon.

Fix: Scope Resource to only the specific KMS key ARN(s) used for notification SQS queues.

**M-2 (MEDIUM) -- Bootstrap IAM policy overly broad; no deactivation plan**

Bootstrap principal is granted s3:CreateBucket, s3:PutObject (all buckets),
sqs:SetQueueAttributes (all queues), events:CreatePartnerEventSource with no ARN scoping
and no plan to disable after bootstrap. An attacker with these permissions could add
malicious SQS subscribers or write to any S3 bucket.

Fix: Scope all permissions to specific resource ARNs. Define a deactivation plan for the bootstrap role.

**M-3 (MEDIUM) -- Consumer IAM role scope not defined**

The seed says the consumer gets least-privilege scoped to specific ARNs but provides no policy JSON.
For a Large/New-system epic, Phase 6 must include the literal IAM policy.

---

### 3. Cross-Account Risks

**H-3 (HIGH) -- New AWS account with no stated organizational guardrails**

The seed notes Mark is provisioning a new AWS account (OQ-3) with no mention of:
AWS Organizations OU enrollment with SCPs, preventive controls blocking public S3 bucket creation,
CloudTrail (all regions), AWS Config recording, or GuardDuty.
A new AWS account with no org-level guardrails is a security island.

Fix: Define AWS Organizations enrollment and baseline (CloudTrail + Config + GuardDuty)
as a hard precondition for Phase 8 infrastructure work.

**M-4 (MEDIUM) -- Cross-account Azure->AWS IAM federation deferred to Phase 6b; should be a Phase 6 deliverable**

The seed labels OIDC web-identity federation (Azure Managed Identity -> AWS IAM role) as a
Phase 6b call. But the cross-account trust policy must exist before any consumer can write to S3.
A Phase 6b redesign verdict would block Phase 8.

Fix: Resolve OQ-11 (consumer runtime) before Phase 6 starts, not during it.

---

### 4. SQS / EventBridge Subscription Security

**H-4 (HIGH) -- SQS sqs:SendMessage granted to entire Amazon SP-API account; no SourceArn condition**

The verbatim SQS policy grants sqs:SendMessage to arn:aws:iam::437568002678:root -- all Amazon SP-API
infrastructure. If the queue ARN leaks (IaC commit, CloudFormation stack listing), a malicious SP-API
application could inject arbitrary messages into GC SQS queues.

Fix: Investigate whether SP-API supports aws:SourceArn-conditioned SQS policies scoped to GC
specific SP-API application ARN. If not, document as accepted risk with compensating controls
(envelope validation, per-message origin check).

**M-5 (MEDIUM) -- EventBridge partner source acceptance has no integrity check**

The one-time manual acceptance step has no proposed verification that the accepted source name
exactly matches the GC SP-API application. Mis-clicking on a different partner source silently
routes foreign notifications into the archive.

Fix: Phase 10 runbook must verify accepted partner event source name exactly matches
aws.partner/sellingpartnerapi.amazon.com/{GC-AWS-Account-Id}/{GC-SP-API-App-Id}.

---

### 5. External API Write Safety

**H-5 (HIGH) -- Write-safety adapter behavior unspecified; env cross-contamination risk; boto3 writes not guarded**

Three sub-issues:

(a) _ensure_mcp_write_adapter is mandated for createSubscription/createDestination but the seed
does not specify what it does when TESTING=1. A silent no-op causes SC-6 to pass even if the
live code path is also no-op in production due to mis-initialization.

(b) Subscription manager calls both createDestination (grantless) AND createSubscription (requires
seller grant). Accidentally calling createSubscription in UAT against a prod LWA token creates
live subscriptions pointing at UAT SQS queues -- production SP-API pushing to UAT infrastructure.

(c) sqs.delete_message and s3.PutObject are writes to AWS but are NOT covered by the write-adapter
safety mechanism. A test that fires real delete_message calls silently ACKs SQS messages without
writing to S3, causing permanent data loss.

Fix: (a) _ensure_mcp_write_adapter must RAISE (not no-op) in test mode.
(b) Subscription manager must validate LWA credentials match target environment before createSubscription.
(c) Mock boto3 SQS and S3 clients explicitly in test mode, separate from the SP-API HTTP mock.

---

### 6. Notification Type Sensitivity

**M-6 (MEDIUM) -- TRANSACTION_UPDATE and pricing stream sensitivity underweighted; no tier separation**

Proposed sensitivity tiers:
- Tier 1 (highest): ORDER_CHANGE (buyer PII), TRANSACTION_UPDATE (financial),
  ANY_OFFER_CHANGED/B2B_ANY_OFFER_CHANGED/PRICING_HEALTH (competitive pricing)
- Tier 2 (moderate): FBA_INVENTORY_AVAILABILITY_CHANGES, LISTINGS_ITEM_STATUS_CHANGE, ACCOUNT_STATUS_CHANGED
- Tier 3 (lower): DETAIL_PAGE_TRAFFIC_EVENT, BRANDED_ITEM_CONTENT_CHANGE, PRODUCT_TYPE_DEFINITIONS_CHANGE

Tier 1 warrants SSE-KMS, restricted bucket policy, and potentially a separate bucket.

**M-7 (MEDIUM) -- ACCOUNT_STATUS_CHANGED is a high-value attack target**

This fires when GC seller account moves between NORMAL/AT_RISK/DEACTIVATED. An attacker monitoring
this stream in real-time knows precisely when GC is at risk.

---

### 7. Injection / Deserialization Risks

**M-8 (MEDIUM) -- No payload size guard before json.loads(); memory exhaustion risk**

The consumer pseudocode does not validate payload size before deserialization.
An injected message at the SQS 256 KB limit with deeply nested JSON could trigger
memory exhaustion in Lambda (128 MB default).

Fix: Add MAX_PAYLOAD_BYTES guard (e.g., 512 KB) before json.loads().
Reject oversized messages to DLQ with a size-exceeded reason code.

**M-9 (MEDIUM) -- notificationId not sanitized before use in S3 key**

The S3 path uses <timestamp>-<notificationId>.json.gz where notificationId comes from the SP-API
payload as an arbitrary string. A notificationId containing ../ sequences could produce malformed
S3 keys that break lifecycle policy prefix matching.

Fix: Validate notificationId against [a-zA-Z0-9-_]{8,128} before S3 key construction.
Reject to DLQ if invalid.

**M-10 (MEDIUM) -- notificationType not normalized before use in S3 path**

The consumer uses raw notificationType string in S3 key path even for unknown types routed to _unknown/.
A malformed notificationType with path-separator characters produces a malformed prefix.

Fix: Normalize notificationType to uppercase alphanumeric + underscore before any S3 key construction.

---

### Summary

| ID | Finding | Severity | Gate |
|----|---------|----------|------|
| H-1 | PII/financial in indefinite archive; GDPR/ToS risk | High | Before Phase 6 |
| H-2 | KMS Resource:* grants Amazon principal all-account key access | High | Phase 6 design |
| H-3 | New AWS account: no org guardrails defined | High | Phase 8 precondition |
| H-4 | SQS SendMessage to entire Amazon SP-API account; no SourceArn condition | High | Phase 6 design |
| H-5 | Write-adapter unspecified; env cross-contamination; boto3 not guarded | High | Phase 7 gate |
| M-1 | SSE-S3 insufficient for PII/financial tiers | Medium | Phase 6 |
| M-2 | Bootstrap IAM overly broad; no deactivation plan | Medium | Phase 6 |
| M-3 | Consumer IAM role scope not defined | Medium | Phase 6 |
| M-4 | Azure->AWS federation is Phase 6 deliverable, not Phase 6b | Medium | Resolve OQ-11 first |
| M-5 | EventBridge source acceptance has no integrity check | Medium | Phase 10 runbook |
| M-6 | TRANSACTION_UPDATE + pricing data sensitivity underweighted | Medium | Phase 6b |
| M-7 | ACCOUNT_STATUS_CHANGED is high-value attack target | Medium | Phase 6b |
| M-8 | No payload size guard before json.loads() | Medium | Phase 7 |
| M-9 | notificationId not sanitized before S3 key | Medium | Phase 7 |
| M-10 | notificationType not normalized before S3 path | Medium | Phase 7 |

### Verdict: REQUEST_CHANGES

Mandatory before Phase 6 design begins:
1. H-1: Add data classification; resolve GDPR/Amazon ToS for indefinite retention of ORDER_CHANGE and TRANSACTION_UPDATE
2. H-2: Fix KMS policy Resource scope to specific key ARNs, not *
3. H-3: Define AWS Organizations enrollment and org-level SCPs as Phase 8 hard precondition
4. H-4: Investigate aws:SourceArn SQS condition; document verdict in Phase 6 design
5. H-5: Specify write-adapter raises in test mode; add per-env LWA guard; extend mock coverage to boto3

M-1 through M-10 are Phase 6/6b inputs.

*-- Morris adversarial pass (2026-04-27)*

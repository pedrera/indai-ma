# C1.6 — Operations follow-up after formal closure

Prepared 2026-10-31. Procedures only: no rotation, deletion, restart, new Business
request or next increment is authorized/executed by this document. The approved
state is [PILOTO LOCAL VALIDADO CON LIMITACIONES](C1_6_FINAL_REVIEW.md).

Detailed read-only inventory, staged renewal/rollback and natural-expiry observation
criteria: [TLS and expiry maintenance plan](C1_6_TLS_EXPIRY_MAINTENANCE_PLAN.md).

## TLS A2 complete — active chain and next renewal

A2 **PASSED** on 2026-11-07. The new chain is **ACTIVE**. Chrome Default
retains the old CA, and the A1 and A2 backups are preserved. Both approvals
remain false. The live verification is recorded in the [A2 review](C1_6_TLS_ROTATION_A2_REVIEW.md).

| Active material | Exact expiry UTC | Proposed renewal target |
| --- | --- | --- |
| localhost server certificate | 2027-02-05 14:25:35Z | By 2027-01-29 |
| Local CA | 2027-11-07 14:25:35Z | By 2027-10-08 |

These proposed dates allow seven days before leaf expiry and 30 days before
CA expiry. They are planning dates. A future renewal requires separate approval.

## Historical pre-A2 inventory and renewal plan

| Material | Exact expiry UTC | Europe/Madrid | Proposed maintenance target |
| --- | --- | --- | --- |
| localhost server certificate | 2026-11-08 15:30:15Z | 08/11/2026 16:30:15 | By 06/11/2026 |
| Local CA | 2026-11-24 15:30:15Z | 24/11/2026 16:30:15 | By 20/11/2026 |

Dates are operational deadlines/proposals, not scheduled automations. Both
certificates were verified valid at final review. No certificate is regenerated
automatically and initial prepare_local.py must not overwrite existing secrets.

1. Obtain explicit approval for the selected operation (leaf renewal versus CA
   rotation), downtime, secure backup and rollback. Confirm both pilot approvals
   remain false and no jobs are active. Preserve issuer, audience, users, grants,
   exact OIDC clients/redirects and C1.3 policy.
2. Recheck current public certificate fingerprints, validity and actual physical
   mount paths. Protect backups and key material with private ACLs; keep all keys
   outside Git. Never print private keys or secrets. Do not export private CA
   material to browser or container.
3. For a leaf-only renewal while the existing CA is valid: issue a new server key
   and certificate signed by that CA with SAN localhost and 127.0.0.1, proper server
   usage and a validity window within CA validity. Review the public certificate,
   key match, chain, SAN and dates offline before replacing anything. If the
   requested useful lifetime crosses the CA expiry, plan CA rotation explicitly.
4. For a CA rotation: create new protected CA/key and leaf in a separate private
   staging directory. Review fingerprints and limits. Plan transition of explicit
   Python trust and the isolated Chrome profile using only the new public CA.
   Do not modify global Windows/system stores or certifi automatically. Existing
   trusted roots must not be removed indiscriminately.
5. During an approved maintenance window, stop only the necessary local clients
   and Keycloak after verifying their identities and idle state. Replace only
   reviewed TLS material or approved mount configuration; preserve read-only
   mounts, non-root operation and 127.0.0.1:8443 binding. Do not regenerate/import
   the realm, reset passwords or delete the data volume.
6. Start Keycloak and check healthy, TLS with hostname/CA verification, exact
   discovery issuer, JWKS and certificate chain. Restart Python clients for the
   approved trust configuration. Validate API/Streamlit loopback and unauthenticated
   session 401, then native manual login + /session 200 with expected principal.
   Do not use a Business POST as the smoke test. Keep both approval flags false.
7. On failure, stop and diagnose. Restore the securely backed-up still-valid
   certificate/config only if appropriate; never use expired material or TLS
   bypass as a fallback. Record public fingerprints/dates and the validated
   outcome. Retire obsolete keys/trust only under the reviewed rollback plan.

The preceding inventory and targets describe the pre-A2 chain. The procedure
remains a reference for future maintenance. The active state and A2 outcome
are recorded above.

## Private audit retention and deletion

Location: C:\Users\pedre\indai-ma-private-audit. Access is restricted to the local
operator, SYSTEM and Administrators; directory inheritance is disabled. Do not
include this directory, secret configuration, tokens, raw logs or keys in a Git
baseline. No public UI/API serves its files.

| File | Contents retained at closure | Earliest UTC event | Proposed latest deletion/review deadline (7 days) |
| --- | --- | --- | --- |
| component-check.jsonl | 1 explicitly non-HTTP component write probe | 2026-10-30 19:39:02Z | 06/11/2026 20:39 Europe/Madrid |
| denials.jsonl | 17 safe events, 4 fully correlated denials | 2026-10-30 20:03:19Z | 06/11/2026 21:03 Europe/Madrid |

The final review preserves the required nonsensitive outcome, stage, exception,
HTTP result and four correlation IDs. The original request without retained
evidence remains explicitly inconclusive as to exception/execution. No token,
cookie, raw claims, payload or private principal mapping is needed for closure.

The owner should delete the private records after review, no later than the
above proposed retention deadlines unless an explicit, justified retention
extension is approved. This document does not execute deletion or schedule it.

Safe deletion procedure for an approved maintenance step:

1. Confirm the documentation contains the evidence needed for closure. Stop the
   owning API writer safely; verify identity and port closure. Never delete a file
   while the writer is active or assume a cached PID is still the same process.
2. Disable the audit settings for the next API startup (omit/clear
   INDAI_LOCAL_SECURITY_AUDIT and INDAI_SECURITY_AUDIT_FILE in that process).
3. Resolve each explicitly named target inside the above private directory;
   verify it is a regular file with no links/reparse redirection. Delete only
   these reviewed files via Remove-Item -LiteralPath. No recursive directory
   deletion, globbing or computed-directory removal.
4. If later audit testing is authorized, provision a new empty private regular
   file, review ACLs and explicit process settings, and restart the writer.
   A missing/deleted file does not stop authorization; failed audit writes are
   not permission to resume experiments without verified observability.

Deletion is logical file deletion, not a guarantee of forensic erasure on SSDs,
backups or snapshots. Retention/backup handling remains an operator responsibility.

## Follow-up register

- Before 06/11: review audit disposal and leaf renewal authorization.
- Before 08/11 16:30:15 local: leaf renewed and verified, or stop relying on it.
- By 20/11 target, before 24/11 16:30:15 local: approved CA/trust rotation.
- Next no-POST validation: observe natural access-token expiry and renewal UX.
- Separately authorized later trial: real asynchronous/pending-job isolation.
- Before widening multi-tenant scope: RAG resource isolation and protection of
  all exposed direct views. Persistence, production audit/revocation and backend
  cancellation/idempotency remain separate design work. No C2/C3 is initiated.

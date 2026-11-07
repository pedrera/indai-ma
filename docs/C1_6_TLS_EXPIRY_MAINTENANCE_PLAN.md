# C1.6 — TLS inventory and natural access-token expiry plan

Prepared 2026-10-31 after formal local-pilot closure. Read-only inventory plus
procedures only. No key generation, replacement, trust modification, service
restart, audit deletion or Business POST is performed/authorized by this plan.
Both private approvals were checked false. C1.3 and Keycloak remain unchanged.

Subsequent approved A1 joint rotation preparation is recorded in the
[A1 staging review](C1_6_TLS_ROTATION_A1_REVIEW.md). It generated private backup/new
material only.

The [A1.1 review and A2 runbook](C1_6_TLS_ROTATION_A1_1_REVIEW.md) supersede the
original staging dates and rotation sequence for the joint CA/server change.

## 1. Active TLS inventory after A2

A2 **PASSED** on 2026-11-07. The new chain is **ACTIVE** at the existing
TLS paths. Chrome Default trusts the new public CA and retains the old CA.
The A1 and A2 backups are preserved. Both approvals remain false.
[A2 execution and verification](C1_6_TLS_ROTATION_A2_REVIEW.md)

| Active certificate | SHA-256 | Expiry UTC | Proposed renewal target |
| --- | --- | --- | --- |
| Leaf | `0e834970d0230704297a308039a6211061651eac6e6a62acf42289e6417528e0` | 2027-02-05 14:25:35Z | 2027-01-29 |
| CA | `b3933c69d2358a0b06cfdfb63dfc88cbed6d8d546c8359476ce1d166a5ba26db` | 2027-11-07 14:25:35Z | 2027-10-08 |

The proposed targets allow seven days before leaf expiry and 30 days before CA
expiry. Future maintenance requires separate approval.

### Historical pre-A2 inventory

The following observations and the original renewal dates in section 2 apply
to the previous chain. A2 retained the physical paths and rotated the material.

Private root on this machine:
`C:\Users\pedre\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\indai-ma-keycloak-dev`.
This physical path avoids the previously diagnosed AppData/Docker virtualization
mismatch. Relative file names below are relative to that root.

| Item | Effective location / evidence |
| --- | --- |
| Keycloak server chain | tls/localhost-cert.pem; two PEM certificates (leaf + local CA) |
| Server private key | tls/localhost-key.pem; existence/type checked, contents not read |
| Local CA public certificate | ca/ca-cert.pem; public DER copy ca/ca-cert.cer |
| Local CA private key | ca/ca-key.pem; existence/type checked, contents not read; never mounted into Keycloak |
| Container certificate/key | /opt/keycloak/conf/dev-tls/localhost-cert.pem and localhost-key.pem |
| Docker mount | Physical private tls directory -> /opt/keycloak/conf/dev-tls, read-only; Docker reports the equivalent /run/desktop/mnt/host/c/... source |
| Keycloak | 26.8.1 pinned image, running/healthy; KC_HTTP_ENABLED=false; KC_HOSTNAME=https://localhost:8443; only 127.0.0.1:8443 published |
| FastAPI trust | INDAI_OIDC_CA_FILE points to external ca/ca-cert.pem; composition creates SSL context with certifi roots plus that CA, supplied to C1.3 transport |
| Native Streamlit/Authlib trust | Existing startup supplies SSL_CERT_FILE=external ca/ca-cert.pem; NO_PROXY localhost/127.0.0.1; native client settings in external streamlit.toml |
| Python default public roots | venv/Lib/site-packages/certifi/cacert.pem; local CA is NOT installed in that bundle |
| Isolated Chrome trust | Historically validated during C1.6; public local CA trusted in the approved profile. This review did not inspect its certificate store or prove the currently visible Chrome profile is that exact profile |

Observed Chrome process metadata exposes a User Data root and no insecure TLS
flags; this alone does not identify the selected profile or establish its trust.
Before maintenance, manually inspect chrome://version Profile Path in the intended
isolated profile, then its certificate manager. Match the public CA fingerprint
below. Do not copy full browser command lines, cookies or authorization URLs.
Do not infer that an isolated profile implies an isolated Windows trust store.

| Public certificate | SHA-256 fingerprint | Expiry UTC |
| --- | --- | --- |
| Leaf | 85b8db4841eb48b9d01212f141e189a67cadae829d9d5e3fb7dfe363e2e909dd | 2026-11-08 15:30:15Z |
| CA | f33ae7b5c99a565b1957eec4a0fa31c0dd0d50b75d573ef0b5b3aa8ed9043a7b | 2026-11-24 15:30:15Z |

Leaf issuer/signature verified against the public CA. Live endpoint matches the
leaf file, with TLS 1.3, verified chain and hostname. SAN: DNS localhost and IP
127.0.0.1. Certificate files and key paths checked are not symlinks. Public-root
bundle parsing emitted a cryptography warning about a legacy non-positive serial
in the bundle; this is not evidence of an invalid local leaf/CA. No bundle change
or dependency upgrade was made as a workaround.

## 2. Invariants and maintenance stages

Preserve exact issuer https://localhost:8443/realms/indai-ma-dev, hostname/8443,
discovery and JWKS URLs, audience indai-ma-api, confidential client
indai-ma-streamlit, redirect http://localhost:8501/oauth2callback, clients/mappers,
RS256/RFC9068/typ policy, memberships and explicit tenant selection. Server TLS
certificates and OIDC JWT signing keys are separate; do not rotate realm JWT keys
as a side effect of renewing the HTTPS certificate.

### A. Approval, backup and generation (original pre-A2 plan)

1. Obtain approval separately for leaf renewal and later CA rotation, proposed
   validity dates, downtime, private backup and browser trust actions. Verify both
   flags false, idle jobs and actual running process/container identities.
2. Inventory fingerprints and current private paths again. Back up the current
   leaf/key pair, CA public certificate, relevant path/config references and, only
   when needed for CA continuity, protected CA key. Use a new private directory
   outside Git with current-user/SYSTEM/admin-only ACLs and a secure backup policy.
   Do not dump files, export the realm, or include browser profiles/tokens. Verify
   restore feasibility without exposing key bytes.
3. Stage output in a separate private directory. Do not rerun prepare_local.py:
   it generates a whole new fixture including secrets, not just TLS. Use a reviewed
   TLS-only issuer/generator with existing cryptography tooling, no new dependency.
4. Leaf-only: generate a fresh RSA key (at least existing 2048-bit strength), random
   positive certificate serial, BasicConstraints CA=false, appropriate KeyUsage,
   EKU SERVER_AUTH, SAN localhost/127.0.0.1, AKI/SKI and SHA-256 signature with the
   existing CA. Sign only for the approved local names. Keep the CA key outside
   the container; output reviewed leaf+CA chain and private key separately.
5. Approve dates explicitly. Target leaf renewal before 06/11 and CA rotation before
   20/11. Blindly repeating the 14-day generation default could create an earlier
   leaf expiry than the CA maintenance window. A proposed bridging leaf can end
   23/11/2026 15:30:15Z (24 hours before CA expiry); alternatively move CA rotation
   earlier than the new leaf expiry. Neither choice is approved by this document.
   Never issue a leaf extending beyond its CA validity.
6. CA rotation: generate a fresh protected CA/key, random positive serial,
   BasicConstraints CA=true/path_length=0 and certificate-signing usage, then a new
   local leaf/key. Review the proposed new CA/leaf lifetimes before issuance.
   Existing development strengths are CA RSA3072 and leaf RSA2048. No production
   PKI suitability is implied. Do not reuse an expired CA as fallback.

### B. Offline validation and controlled replacement (original pre-A2 plan)

1. Verify the private key's derived public key matches the new leaf without printing
   private bytes. Verify signature/issuer, leaf+CA order, positive serials, SAN,
   key usage, validity and public fingerprints. Confirm chain validates with the
   intended trust only and that an unrelated CA fails. Protect ACLs before copying.
2. For a CA change, plan trust in the intended Chrome profile/certificate manager
   and per-process Python configuration using only the public CA. Inspect actual
   scope first; if Chrome requires system/user-store modification, stop and obtain
   separate explicit approval. Do not use trust_python.py to modify certifi or
   global stores automatically. Preserve unrelated public trust roots.
3. During the approved window stop affected Python clients and Keycloak gracefully,
   confirming no jobs and revalidating identities. Replace leaf and key as a matched
   pair while stopped, or switch a previously reviewed private staging mount.
   Use the known physical directory. Keep read-only mount, unprivileged container,
   H2 volume/realm data and loopback publication unchanged. No broad chmod/ACL grant.
4. Confirm non-root Keycloak can read the pair without displaying it. Start only
   Keycloak, verify health/TLS first, then restart FastAPI/Streamlit with the
   approved per-process trust configuration. Do not start a second callback on
   the Streamlit port. Log only safe health/status/public-fingerprint evidence.

## 3. Rollback

- Stop on any chain, hostname, health, discovery, JWKS or login mismatch; keep
  Business disabled and do not retry an analysis to diagnose TLS.
- Gracefully stop the affected services; restore the backed-up matching leaf/key
  and previous approved mount/trust references. Restore only known files, never
  overwrite realm/clients/users or remove the data volume.
- Rollback is viable only while the old certificate/CA remain valid and trusted.
  After their expiry, leave the affected service unavailable and repair the new
  configuration; do not accept expired TLS or turn verification off.
- During CA transition retain the approved old public trust until new-trust
  validation and the rollback decision are complete. Revert only newly added
  profile/process trust if needed; never delete unrelated roots. Remove obsolete
  trust and protected keys only in a reviewed later cleanup step.
- Repeat the post-maintenance checks below. Preserve the old private backup only
  for an explicitly approved rollback period; record and enforce its deletion.

## 4. Post-maintenance validation checklist (procedure)

| Check | Required evidence |
| --- | --- |
| Public material | New leaf/key match, chain, SAN, issuer, dates, public fingerprints |
| Keycloak | Running/healthy; only localhost:8443; read-only TLS mount; HTTP disabled |
| TLS | Hostname and chain verification succeed in Python and intended Chrome; unrelated trust fails |
| Discovery/JWKS | HTTPS 200, exact unchanged issuer/endpoints, RSA JWKS; no JWT-key change required |
| API/UI | Loopback listeners, health/ready 200; existing unauthenticated session contract preserved |
| Native OIDC | Manual login; actual API-validated principal, /session 200, expected backend grants; ID token never used as access token |
| Safety | Both approvals false; no Business POST, no TLS bypass, no secret logging |
| Audit | Existing private policy respected; no fabricated request/execution events |

For the executed checks and results on 2026-11-07, see the [A2 review](C1_6_TLS_ROTATION_A2_REVIEW.md).

## 5. Manual natural access-token expiry — no token handling or POST

This is a prepared procedure, not a claim of observed expiry or HTTP 401.

1. Use the intended isolated profile, close extra pilot tabs and start a fresh native
   manual analyst-a login. Confirm actual principal via existing backend comparison,
   /session 200, expected tenant/permissions, both approvals false and no jobs/results.
   Optionally select tenant-alpha to observe its later invalidation; do not click
   analysis or negative probes. Record only safe UI status and observation time.
2. Establish the effective access-token lifetime from an approved read-only provider
   setting or safe lifetime metadata, not by extracting/decoding browser tokens.
   The original fixture declares 300 seconds, but do not assume persisted realm
   settings still match it without evidence. With a fresh login and that confirmed
   lifetime, leave the tab idle beyond it plus the configured validation leeway.
   Do not refresh/relogin, change the clock, alter realm lifetime or automate inputs.
3. Reload once and inspect UI cleanup: old tenant cleared, no jobs/results, Analyze
   disabled, authentication-renewal instruction where applicable. Native st.user
   can still be logged in while its API access token has expired; this is not proof
   that API access is allowed. Do not perform a POST to test expiration.
4. Obtain the actual GET /session HTTP status only from existing safe status-level
   evidence if available. The current persistent audit covers POST /analysis only;
   the browser sees a Streamlit connection, not necessarily the server-side API GET.
   Do not enable raw HTTP/Authlib debug, HAR/token capture or copy cookie values.
5. If status-level evidence is unavailable, classify the UI observation as such and
   the real HTTP 401 as NOT VERIFIED. A generic credential error may also arise
   before HTTP due to native expiry. If a conclusive status trace is required,
   propose separately reviewed minimal GET status instrumentation (status and
   classified failure only, no token/body/claims), with tests; do not add it here.
6. Only after recording the observation, logout and start a new manual login. Confirm
   /session 200 and expected identity with empty/new tenant selection and no old
   result. No analysis is needed for renewal validation.

| Observation | Permitted conclusion |
| --- | --- |
| Actual GET /session 401 + cleanup, no intervening login | Real API credential rejection; call it expiry only with sufficient time/error evidence |
| Generic UI credential warning, no status trace | UI blocked; HTTP 401 and precise cause not established |
| Native local expiry prevents a GET | Local expiry handling observed; no API 401 claim |
| 200 after waiting, no new login recorded | Not proof of acceptance of an expired token; lifetime/leeway/automatic renewal must be investigated |
| New native login succeeds via existing SSO | Reauthentication using provider session, not proof of refresh of the previous access token |
| Changed safe expiry metadata without manual login | Possible automatic renewal; investigate safe framework behavior before classifying |

Do not store tokens to compare them. Native ID-token lifetime, access-token lifetime,
automatic refresh and provider SSO reuse are distinct. Existing offline C1.3 expired
JWT/401 cleanup tests remain the authoritative controlled expiry evidence until a
real observation is recorded. If a controlled natural-expiry observation is not
possible without further instrumentation, keep it pending.

## 6. Audit retention — preparation only

Private C:\Users\pedre\indai-ma-private-audit retains denials.jsonl (17 events/four
requests) and component-check.jsonl (one explicitly non-HTTP probe). Current ACL
allows the operator, SYSTEM and Administrators only. No record was deleted.
The approved closure summary preserves outcomes and correlation IDs without keys,
tokens, payloads or a clear principal mapping.

Seven-day proposed maximum from oldest event: review/delete component probe before
06/11/2026 20:39 Europe/Madrid and denial log before 06/11/2026 21:03. See
[operations follow-up](C1_6_OPERATIONS_FOLLOWUP.md) for exact timestamps and policy.
Deletion procedure: obtain approval, confirm evidence summary, stop/identify writer,
disable audit for its next start, resolve exactly the two private regular files,
check no redirection and delete only via Remove-Item -LiteralPath. Never recursively
delete a calculated path. Do not delete while writing or recreate a sink without
private ACL review. File removal is not guaranteed forensic SSD/backups erasure.

## 7. Dependencies and stop boundary

- Separate approval for key generation, backups, downtime, trust replacement and
  record deletion; none is implied by preparation.
- Operator participation for isolated-profile identification, public-CA review,
  logins and visible expiry behavior.
- Retain confirmed physical Windows/Docker paths and UID readability; previous
  virtualization/mount issues are not solved by broadening permissions.
- Bridging leaf validity must cover the planned CA window, and rollback has a hard
  old-certificate validity limit. Preserve TLS and JWT separation throughout.
- No new Business run, C1.3 relaxation, Keycloak/client/membership change, artifacts
  access, Git write, C2 or C3 is performed.

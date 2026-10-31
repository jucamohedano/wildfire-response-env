# Redacted public facade for legacy source hosts

`wildfire_source_public_facade_v1.PublicSourceFacadeV1` wraps an exact supported
legacy source host and replaces every delivered `destination_threatened` report
with a generic closure message. It removes the destination object, location,
correlation and source extensions while preserving the report ID, observation
and receipt times, order and cursor behavior. Other report kinds are unchanged.
The original EventBus, private checkpoint, transcript and replay state are not
modified.

This facade is required before exposing reports from the legacy restricted host,
restricted scorer, source-walk/hold wrappers, or source-domain v1/v2 hosts to an
actor: those versions can place a complete private destination record in a
closure report. Do not expose their raw Python objects, checkpoints or
transcripts. This is an accidental-access guard, not an in-process Python
sandbox; a reviewed transport must enforce the actor allowlist. It only handles
this closure-report contract and does not certify the privacy of arbitrary
incoming reports or unrelated future report kinds.

Only trusted in-process policies may receive this Python object: forwarded
methods can reveal their underlying host through Python introspection. For an
untrusted actor, pass only serialized allowlisted observations and commands
across a separate transport or process boundary.

```python
from wildfire_source_public_facade_v1 import PublicSourceFacadeV1

actor = PublicSourceFacadeV1(trusted_legacy_host)
observation = actor.situation()
reports = actor.reports(after_cursor=0, limit=20)
```

`eval.qualify_source_restricted_redacted_v1.qualify` provides the same
restricted-orchestration interface for trusted policies. It sanitizes the
policy's report reads while leaving the private host, independent verifier and
optional authenticated state adapter intact. It prefixes `run_id` with
`wildfire-source-restricted-redacted-runner-v1:` so an older unredacted runner
checkpoint cannot be silently resumed as a redacted run. Create a new private
checkpoint and preserve the old one for reproduction. The existing runner is
unchanged and must not be used to expose raw legacy reports to an actor.

For a new domain episode, the separate
[domain v3 host](source-domain-profile-incident-v3.md) emits the generic closure
message at the source. It has a new identity; earlier domain v1/v2 hosts,
profiles, checkpoints and results retain their original behavior and pins.

# Milestone dependency chain

`milestone-0` is the foundation: platform core authentication and AAA remain
separate modules. `milestone-1` adds CRM and consumes M0 authentication.
Every later milestone is replayed sequentially onto its predecessor. Verify
with `git merge-base --is-ancestor milestone-N milestone-(N+1)` for N=0..9.

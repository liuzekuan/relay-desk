# Privacy and Publishing

Relay Desk is a local application. Keep both HTTP servers bound to loopback.
Do not expose them through a reverse proxy, public tunnel or shared host.

`codex-providers.toml` and `claude-providers.toml` contain plaintext credentials
and relay identities. `batch-results`, logs, exports and the official
installation's `meow_runs` can identify providers even without API keys.
Keep these private, including in backups, screenshots and issue attachments.
The detector sends API requests and credentials to the endpoint you configure.
Local deployment does not protect against an untrusted endpoint or local user.

## Before Committing or Pushing

The repository uses an explicit publication allowlist in
`meow-local-tools/audit_publication.py`, in addition to `.gitignore`.
New public files must be deliberately added to the allowlist after inspection.

```sh
git config core.hooksPath .githooks
python -B meow-local-tools/audit_publication.py
python -B meow-local-tools/audit_publication.py --history
```

The pre-commit hook checks staged blobs. The pre-push hook also checks all
reachable local Git history. When local provider files exist, the audit reads
their names, URLs, hosts and keys in memory and checks for matches without
printing matched values. Historical relay names are included.
Short alphabetic names are checked as quoted values or HTML labels to avoid
mistaking ordinary source identifiers for relay identities.
Use `--require-private` when releasing from a working directory with real data;
this fails if no local provider configuration is available for comparison.
Public CI can check the allowlist and common credential patterns, but cannot
compare against private data that was never uploaded. Hooks are local to a
checkout and must be enabled again after cloning.

These checks supplement inspection; they cannot recognize every possible
secret, transformed value or screenshot. Never force-add runtime data or use
a real provider in examples, tests, screenshots or issue reports. Tests use
reserved example domains and fake credentials.

If a credential was published, revoke it at its provider immediately. Removing
it in a later commit does not remove it from Git history or existing clones.
Report vulnerabilities privately through GitHub's private vulnerability
reporting when enabled; do not include credentials in public issues.

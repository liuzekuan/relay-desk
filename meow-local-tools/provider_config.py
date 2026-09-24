"""Load provider connections from a user-maintained TOML file."""
import hashlib
import tomllib
from urllib.parse import urlsplit

from detector_client import ImportIssue


MODES = {"gpt": {"label": "OpenAI", "model": "gpt-6-astra", "prefix": "codexconfig-"},
         "claude": {"label": "Claude", "model": "claude-fable-5-1", "prefix": "claudeconfig-"}}


def load_connections(path, allow_empty=False, mode="gpt"):
    if mode not in MODES:
        raise ImportIssue("Unsupported detection mode.")
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except OSError:
        raise ImportIssue("Cannot read provider configuration.") from None
    except (ValueError, UnicodeError):
        # TOML parse errors can quote the line containing a key.
        raise ImportIssue("Invalid TOML. Check quotes and [[providers]] blocks; no connections imported.") from None
    if set(config) - {"model", "providers"}:
        raise ImportIssue("Unknown top-level setting. Use model and [[providers]] only.")
    model = config.get("model", MODES[mode]["model"])
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ImportIssue("model must be a non-empty string of at most 256 characters.")
    providers = config.get("providers")
    if not isinstance(providers, list) or (not providers and not allow_empty):
        raise ImportIssue("Add at least one [[providers]] block.")
    result, names = [], set()
    for index, provider in enumerate(providers, 1):
        prefix = f"Provider #{index}: "
        if not isinstance(provider, dict) or set(provider) != {"name", "api", "key"}:
            raise ImportIssue(prefix + "requires exactly name, api and key.")
        if any(not isinstance(provider[field], str) or not provider[field].strip()
               for field in ("name", "api", "key")):
            raise ImportIssue(prefix + "fill in name, api and key before opening the detector.")
        name, base, key = (provider[field].strip() for field in ("name", "api", "key"))
        if len(name) > 256 or any(not char.isprintable() for char in name):
            raise ImportIssue(prefix + "name must be printable and at most 256 characters.")
        if name.casefold() in names:
            raise ImportIssue(prefix + "duplicate name; use a unique name for each relay.")
        names.add(name.casefold())
        try:
            url = urlsplit(base)
            port = url.port
        except ValueError:
            raise ImportIssue(prefix + "invalid API address.") from None
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment
                or "\\" in base or any(ord(char) < 33 for char in base)
                or port is not None and not 1 <= port <= 65535):
            raise ImportIssue(prefix + "api must be HTTPS without a username, password, query or fragment.")
        if any(ord(char) < 33 or ord(char) == 127 for char in key):
            raise ImportIssue(prefix + "key contains whitespace or control characters.")
        if any(key in value for value in (name, base, model)):
            raise ImportIssue(prefix + "key must appear only in the key field.")
        identity = MODES[mode]["prefix"] + hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:32]
        result.append({"preset": {"id": identity, "name": name, "mode": mode,
                                  "base_url": base, "model": model.strip(), "allow_insecure": False},
                       "key": key})
    return result

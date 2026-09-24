"""Keep the Hindsight daemon's port in the env the provider expects.

hindsight-embed treats ``~/.hindsight/profiles/<profile>.env`` as the source of
truth for the daemon port and writes ``HINDSIGHT_API_PORT`` into it whenever it
registers the profile. The bundled Hermes provider builds its expected env
without that key, and on every provider start it compares the two
(``_start_embedded_daemon``). Any difference means "config changed": it rewrites
the file (dropping the port) and stops the daemon so a fresh one picks the
config up. hindsight-embed then writes the port back, so the next session start
sees the same difference again. The result is one daemon restart per new
session. Some restarts fail outright, because the new daemon runs its database
migration before embedded Postgres is listening and then exits.

The fix carries the port already on disk into the expected env. A real config
change (model, provider, base URL, key, idle timeout) still differs and still
restarts the daemon. Nothing here edits Hermes or hindsight files. It wraps one
function in both namespaces the provider reads it from.
"""

import logging

logger = logging.getLogger(__name__)

PORT_KEY = "HINDSIGHT_API_PORT"


def _wrap(build, load_env, env_path):
    if getattr(build, "_lobs_keeps_port", False):
        return build

    def build_keeping_port(config, *args, **kwargs):
        values = build(config, *args, **kwargs)
        if PORT_KEY not in values:
            try:
                port = load_env(env_path(config)).get(PORT_KEY)
            except Exception:
                port = None
            if port:
                values[PORT_KEY] = port
        return values

    build_keeping_port._lobs_keeps_port = True
    return build_keeping_port


def register(ctx):
    try:
        import plugins.memory.hindsight as provider_pkg
        import plugins.memory.hindsight.embedded as embedded
    except ImportError:
        logger.info("hindsight-stable-env: bundled Hindsight provider not present; nothing to patch")
        return
    wrapped = _wrap(embedded._build_embedded_profile_env, embedded._load_simple_env,
                    embedded._embedded_profile_env_path)
    # _materialize and _may_rewrite read the name from embedded; the restart check in
    # _start_embedded_daemon reads the copy imported into the package namespace.
    embedded._build_embedded_profile_env = wrapped
    if hasattr(provider_pkg, "_build_embedded_profile_env"):
        provider_pkg._build_embedded_profile_env = wrapped
    logger.info("hindsight-stable-env: profile env comparison now keeps %s", PORT_KEY)

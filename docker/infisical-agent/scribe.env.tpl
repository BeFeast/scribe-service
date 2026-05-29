{{/*
  Infisical Agent template for scribe (#261).

  Renders the SCRIBE_* secrets stored at services/prod/scribe-service as a
  POSIX-sourceable env-file. The scribe container entrypoint sources the
  rendered output at /secrets/scribe.env before launching uvicorn, so
  pydantic-settings reads these as real env vars.

  Each secret is rendered as KEY=VALUE on its own line. Empty values are
  emitted as KEY= (which the shell exports as an empty string); the
  fail-loud guard in docker/entrypoint.sh blocks boot if
  SCRIBE_TRUSTED_CIDRS or SCRIBE_MACHINE_BEARER_TOKEN are empty.

  Template syntax follows Infisical Agent docs: `secret <env> <path> <key>`
  returns a struct with .SecretValue. The Agent re-renders this file on
  the polling-interval configured in agent.yaml; on transient Infisical
  outages it keeps the previous render in place (last-known-good cache).
*/}}
SCRIBE_DATABASE_URL={{ (secret "prod" "/scribe-service" "SCRIBE_DATABASE_URL").SecretValue }}
SCRIBE_TRUSTED_CIDRS={{ (secret "prod" "/scribe-service" "SCRIBE_TRUSTED_CIDRS").SecretValue }}
SCRIBE_MACHINE_BEARER_TOKEN={{ (secret "prod" "/scribe-service" "SCRIBE_MACHINE_BEARER_TOKEN").SecretValue }}
SCRIBE_VAST_API_KEY={{ (secret "prod" "/scribe-service" "SCRIBE_VAST_API_KEY").SecretValue }}
SCRIBE_SHORTLINK_API_URL={{ (secret "prod" "/scribe-service" "SCRIBE_SHORTLINK_API_URL").SecretValue }}
SCRIBE_SHORTLINK_API_KEY={{ (secret "prod" "/scribe-service" "SCRIBE_SHORTLINK_API_KEY").SecretValue }}
SCRIBE_PUBLIC_BASE_URL={{ (secret "prod" "/scribe-service" "SCRIBE_PUBLIC_BASE_URL").SecretValue }}
SCRIBE_AUTH_CLERK_ISSUER={{ (secret "prod" "/scribe-service" "SCRIBE_AUTH_CLERK_ISSUER").SecretValue }}
SCRIBE_AUTH_CLERK_JWKS_URL={{ (secret "prod" "/scribe-service" "SCRIBE_AUTH_CLERK_JWKS_URL").SecretValue }}
SCRIBE_CLERK_SECRET_KEY={{ (secret "prod" "/scribe-service" "SCRIBE_CLERK_SECRET_KEY").SecretValue }}
SCRIBE_CLERK_PUBLISHABLE_KEY={{ (secret "prod" "/scribe-service" "SCRIBE_CLERK_PUBLISHABLE_KEY").SecretValue }}
SCRIBE_BOOTSTRAP_ADMIN_EMAIL={{ (secret "prod" "/scribe-service" "SCRIBE_BOOTSTRAP_ADMIN_EMAIL").SecretValue }}
SCRIBE_FREELLMAPI_API_KEY={{ (secret "prod" "/scribe-service" "FREELLMAPI_API_KEY").SecretValue }}
SCRIBE_ADMIN_TELEGRAM_BOT_TOKEN={{ (secret "prod" "/scribe-service" "SCRIBE_ADMIN_TELEGRAM_BOT_TOKEN").SecretValue }}
SCRIBE_ADMIN_TELEGRAM_CHAT_ID={{ (secret "prod" "/scribe-service" "SCRIBE_ADMIN_TELEGRAM_CHAT_ID").SecretValue }}

{{/*
  Infisical Agent template for scribe (#261).

  Renders the simple-valued SCRIBE_* secrets that must survive an Infisical
  outage at boot: the entrypoint fail-loud guard needs SCRIBE_TRUSTED_CIDRS +
  SCRIBE_MACHINE_BEARER_TOKEN, and summaries need the freellmapi keys (without
  them the chain falls through to a rate-limited codex and fails). All five
  values are space/newline-free so the env-file is safely POSIX-sourceable.

  Clerk/JWKS secrets (which may contain spaces/newlines and would break a
  naive `source`) keep loading via the in-process Infisical fetch; their
  absence degrades the public Clerk path but never locks out the trusted LAN.

  Function form for this infisical/cli version:
    secret <projectId> <env-slug> <path> -> []SingleEnvironmentVariable{.Key,.Value}
*/}}
{{- range secret "5b5038c7-46d5-496f-bfa6-6184cb41e143" "prod" "/scribe-service" }}
{{- if or (eq .Key "SCRIBE_TRUSTED_CIDRS") (eq .Key "SCRIBE_MACHINE_BEARER_TOKEN") (eq .Key "SCRIBE_FREELLMAPI_API_KEY") (eq .Key "SCRIBE_FREELLMAPI_MODEL") (eq .Key "SCRIBE_SUMMARY_PROVIDERS") }}
{{ .Key }}={{ .Value }}
{{- end }}
{{- end }}

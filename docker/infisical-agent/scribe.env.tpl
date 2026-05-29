{{/*
  Infisical Agent template for scribe (#261).
  Renders ONLY the two scalar-str boot-critical secrets. Do NOT add list/complex
  Settings fields here (e.g. SCRIBE_SUMMARY_PROVIDERS): pydantic-settings parses
  list-type env vars as JSON, so a bare `freellmapi,codex` raises SettingsError
  and crash-loops scribe at import. All other SCRIBE_* secrets (including the
  summary provider chain) load via the in-process Infisical fetch.
  Function: secret <projectId> <env-slug> <path> -> []SingleEnvironmentVariable{.Key,.Value}
*/}}
{{- range secret "5b5038c7-46d5-496f-bfa6-6184cb41e143" "prod" "/scribe-service" }}
{{- if or (eq .Key "SCRIBE_TRUSTED_CIDRS") (eq .Key "SCRIBE_MACHINE_BEARER_TOKEN") }}
{{ .Key }}={{ .Value }}
{{- end }}
{{- end }}

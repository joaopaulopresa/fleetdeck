{{/* Labels common to every object of the chart. */}}
{{- define "fleetdeck.labels" -}}
app.kubernetes.io/part-of: fleetdeck
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* A generated secret value that stays the same across upgrades. */}}
{{- define "fleetdeck.stableSecret" -}}
{{- $existing := lookup "v1" "Secret" .root.Release.Namespace "fleet-secrets" -}}
{{- if and $existing (hasKey $existing.data .key) -}}
{{ index $existing.data .key }}
{{- else -}}
{{ randAlphaNum 40 | b64enc }}
{{- end -}}
{{- end }}

{{/* Environment shared by the Python services. */}}
{{- define "fleetdeck.commonEnv" -}}
- name: FLEET_SYSTEM_NAMESPACE
  value: {{ .Release.Namespace }}
- name: FLEET_KEYS_NAMESPACE
  value: {{ .Values.keysNamespace }}
- name: FLEET_CONTROL_PLANE_URL
  value: http://control-plane.{{ .Release.Namespace }}.svc.cluster.local:8000
- name: FLEET_INTERNAL_TOKEN
  valueFrom:
    secretKeyRef: {name: fleet-secrets, key: internal-token}
{{- end }}

{{/* Hardened container security context used by every platform container. */}}
{{- define "fleetdeck.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "fleetdeck.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

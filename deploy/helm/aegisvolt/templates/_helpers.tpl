{{- define "aegis.labels" -}}
app.kubernetes.io/part-of: aegisvolt
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
aegisvolt.io/environment: {{ .root.Values.global.environment }}
app.kubernetes.io/name: {{ .name }}
{{- end -}}

{{- define "aegis.selector" -}}
app.kubernetes.io/name: {{ .name }}
{{- end -}}

{{- define "aegis.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
capabilities: { drop: ["ALL"] }
seccompProfile: { type: RuntimeDefault }
{{- end -}}

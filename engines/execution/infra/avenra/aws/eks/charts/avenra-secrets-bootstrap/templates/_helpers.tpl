{{- define "avenra-secrets-bootstrap.namespace" -}}
{{- default .Release.Namespace .Values.namespaceOverride -}}
{{- end -}}

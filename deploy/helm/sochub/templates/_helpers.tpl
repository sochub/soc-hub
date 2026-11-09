{{- define "sochub.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "sochub.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "sochub.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "sochub.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* component selector labels: call with (dict "ctx" . "component" "api") */}}
{{- define "sochub.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sochub.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "sochub.secretName" -}}
{{- default (printf "%s-secrets" (include "sochub.fullname" .)) .Values.existingSecret }}
{{- end }}

{{- define "sochub.configMapName" -}}
{{- printf "%s-config" (include "sochub.fullname" .) }}
{{- end }}

{{- define "sochub.pvcName" -}}
{{- default (printf "%s-attachments" (include "sochub.fullname" .)) .Values.persistence.attachments.existingClaim }}
{{- end }}

{{- define "sochub.backendImage" -}}
{{- printf "%s:%s" .Values.images.backend.repository .Values.images.backend.tag }}
{{- end }}

{{- define "sochub.databaseUrl" -}}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgresql:5432/%s" .Values.postgresql.auth.username .Values.postgresql.auth.password .Release.Name .Values.postgresql.auth.database }}
{{- end }}

{{- define "sochub.redisUrl" -}}
{{- printf "redis://:%s@%s-redis-master:6379/0" .Values.redis.auth.password .Release.Name }}
{{- end }}

{{/* Secret-backed env vars. With in-chart subcharts + inline secrets everything is in the Secret. */}}
{{- define "sochub.secretEnv" -}}
{{- $name := include "sochub.secretName" . }}
{{- range $k := list "SECRET_KEY" "ENCRYPTION_KEY" "DATABASE_URL" "REDIS_URL" }}
- name: {{ $k }}
  valueFrom:
    secretKeyRef:
      name: {{ $name }}
      key: {{ $k }}
{{- end }}
{{- end }}

{{/* Backend-ish container env: call with (dict "ctx" . "only" (list ...)) not needed; full set */}}
{{- define "sochub.backendEnv" -}}
{{ include "sochub.secretEnv" . }}
{{- if .Values.smtp.password }}
- name: SMTP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "sochub.secretName" . }}
      key: SMTP_PASSWORD
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{- define "sochub.backendEnvFrom" -}}
- configMapRef:
    name: {{ include "sochub.configMapName" . }}
{{- end }}

{{- define "sochub.attachmentsVolume" -}}
- name: attachments
  {{- if .Values.persistence.attachments.enabled }}
  persistentVolumeClaim:
    claimName: {{ include "sochub.pvcName" . }}
  {{- else }}
  emptyDir: {}
  {{- end }}
{{- end }}

{{- define "sochub.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
{{- end }}

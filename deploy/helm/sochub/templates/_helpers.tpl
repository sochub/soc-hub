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

{{/* repository:tag[@digest]; tag defaults to the chart appVersion. Call with (dict "img" .Values.images.x "ctx" .) */}}
{{- define "sochub.image" -}}
{{- $tag := .img.tag | default .ctx.Chart.AppVersion }}
{{- if .img.digest }}
{{- printf "%s:%s@%s" .img.repository $tag .img.digest }}
{{- else }}
{{- printf "%s:%s" .img.repository $tag }}
{{- end }}
{{- end }}

{{- define "sochub.backendImage" -}}
{{- include "sochub.image" (dict "img" .Values.images.backend "ctx" .) }}
{{- end }}

{{/* Bundled datastore URLs; call with (dict "ctx" . "password" $pw). */}}
{{- define "sochub.databaseUrl" -}}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgresql:5432/%s" .ctx.Values.postgresql.auth.username .password .ctx.Release.Name .ctx.Values.postgresql.auth.database }}
{{- end }}

{{- define "sochub.redisUrl" -}}
{{- printf "redis://:%s@%s-redis-master:6379/0" .password .ctx.Release.Name }}
{{- end }}

{{- define "sochub.datastoreSecretName" -}}
{{- printf "%s-datastores" .Release.Name }}
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

{{/* Pod securityContext; call with (dict "uid" 10001). */}}
{{- define "sochub.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
fsGroup: {{ .uid }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "sochub.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Writable scratch for the read-only backend image: /tmp (beat schedule, spooled uploads) and HOME (fontconfig cache). */}}
{{- define "sochub.scratchMounts" -}}
- name: tmp
  mountPath: /tmp
- name: home
  mountPath: /home/app
{{- end }}

{{- define "sochub.scratchVolumes" -}}
- name: tmp
  emptyDir: {}
- name: home
  emptyDir: {}
{{- end }}

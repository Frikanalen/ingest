{{/*
Expand the name of the chart.
*/}}
{{- define "ingest.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "ingest.fullname" -}}
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

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "ingest.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "ingest.labels" -}}
helm.sh/chart: {{ include "ingest.chart" . }}
{{ include "ingest.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "ingest.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ingest.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: ingest
{{- end }}

{{/*
Where the archive SSH credentials are mounted.
*/}}
{{- define "ingest.sshMountPath" -}}
/etc/ingest/ssh
{{- end }}

{{/*
tusd's arguments.

tusd runs beside ingest in the same pod, so it reaches the hook endpoint over
the pod's own loopback and shares the upload volume rather than handing files
across a network.
*/}}
{{- define "ingest.tusdArgs" -}}
- -port={{ .Values.tusd.port }}
- -upload-dir={{ .Values.uploads.mountPath }}
- -base-path={{ .Values.tusd.basePath }}
- -hooks-http=http://localhost:{{ .Values.service.port }}/tusdHooks/
# Only the two events ingest implements: pre-create assigns the storage path,
# post-finish starts the job.
- -hooks-enabled-events=pre-create,post-finish
{{- if .Values.tusd.behindProxy }}
- -behind-proxy=true
{{- end }}
{{- if .Values.tusd.corsAllowCredentials }}
- -cors-allow-credentials=true
{{- end }}
{{- with .Values.tusd.corsAllowHeaders }}
- -cors-allow-headers={{ . }}
{{- end }}
{{- with .Values.tusd.corsAllowOrigin }}
- -cors-allow-origin={{ . }}
{{- end }}
{{- with .Values.tusd.extraArgs }}
{{- toYaml . }}
{{- end }}
{{- end }}

{{/*
Environment for the ingest container.

Only FK_ARCHIVE_HOST switches ingest from a local archive to SSH; without it
the remaining archive settings are inert.
*/}}
{{- define "ingest.env" -}}
- name: FK_PORT
  value: {{ .Values.service.port | quote }}
- name: FK_API_URL
  value: {{ .Values.api.url | quote }}
# A token, not a login: with a username and password ingest would exchange
# them for one at startup, and could not boot unless the API were already up.
- name: FK_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .Values.api.secretName }}
      key: {{ .Values.api.tokenKey }}
- name: FK_TUSD_DIR
  value: {{ .Values.uploads.mountPath | quote }}
# Both containers mount the upload volume at the same path, so the paths tusd
# reports are the paths ingest reads.
- name: FK_TUSD_UPLOAD_DIR
  value: {{ .Values.uploads.mountPath | quote }}
- name: FK_WORK_DIR
  value: {{ .Values.work.mountPath | quote }}
{{- if .Values.archive.ssh.enabled }}
- name: FK_ARCHIVE_HOST
  value: {{ .Values.archive.ssh.host | quote }}
- name: FK_ARCHIVE_PORT
  value: {{ .Values.archive.ssh.port | quote }}
- name: FK_ARCHIVE_USERNAME
  value: {{ .Values.archive.ssh.username | quote }}
- name: FK_ARCHIVE_PRIVATE_KEY_FILE
  value: {{ printf "%s/%s" (include "ingest.sshMountPath" .) .Values.archive.ssh.privateKeyKey | quote }}
- name: FK_ARCHIVE_KNOWN_HOSTS_FILE
  value: {{ printf "%s/%s" (include "ingest.sshMountPath" .) .Values.archive.ssh.knownHostsKey | quote }}
- name: FK_ARCHIVE_REQUIRED
  value: {{ .Values.archive.ssh.required | quote }}
{{- end }}
- name: FK_ARCHIVE_DIR
  value: {{ .Values.archive.dir | quote }}
{{- range $name, $value := .Values.extraEnv }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- end }}

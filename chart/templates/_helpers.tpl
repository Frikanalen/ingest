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
The archive, mounted read-only, and the volume behind it.

Defined once and used by both Deployments: every pod reads the archive, and a
pod that has it at a different path than FK_ARCHIVE_DIR reads an empty one.

Read-only is not tidiness. The engine publishes by asking `fk-archive` on the
storage host to do it, under an account of its own, precisely so that no ingest
process holds write access to the archive -- see archive-utils/. A read-write
mount hands that access straight back, and ingest logs a warning at startup if
it finds one.

Both are used inside the blocks that already ask whether the deployed archive
is in play, so neither repeats the condition.
*/}}
{{- define "ingest.archiveMount" -}}
- name: archive
  mountPath: {{ .Values.archive.dir }}
  readOnly: true
{{- end }}

{{- define "ingest.archiveVolume" -}}
- name: archive
  {{- with .Values.archive.mount.existingClaim }}
  persistentVolumeClaim:
    claimName: {{ . }}
    readOnly: true
  {{- else }}
  nfs:
    server: {{ .Values.archive.mount.nfs.server | quote }}
    path: {{ .Values.archive.mount.nfs.path | quote }}
    readOnly: true
  {{- end }}
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
The upload half's resource name.

Everything belonging to the pod that owns tusd and the upload volume is named
from this, so the Deployment, Service, Ingress and claim move together and none
of them can be left behind describing the old shape.
*/}}
{{- define "ingest.uploadFullname" -}}
{{ include "ingest.fullname" . }}-upload
{{- end }}

{{/*
Selector labels for the worker pool.

Deliberately not ingest.selectorLabels. A Service selects on a subset, so a
worker carrying the same three labels would be routed tusd hook traffic it has
no server to answer with. Differing on `app` is what keeps them apart, and it
has to be a difference rather than an addition for that reason.
*/}}
{{- define "ingest.workerSelectorLabels" -}}
app.kubernetes.io/name: {{ include "ingest.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: worker
app: {{ include "ingest.name" . }}-worker
{{- end }}

{{/*
How ingest authenticates to django-api. Shared by everything that talks to it.
*/}}
{{- define "ingest.apiEnv" -}}
- name: FK_API_URL
  value: {{ .Values.api.url | quote }}
# A token, not a login: with a username and password ingest would exchange
# them for one at startup, and could not boot unless the API were already up.
- name: FK_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .Values.api.secretName }}
      key: {{ .Values.api.tokenKey }}
{{- end }}

{{/*
Where finished files go, and anything else the deployment wants to set.

Only FK_ARCHIVE_HOST switches ingest from a local archive to the one on the
storage host; without it the remaining archive settings are inert.

FK_ARCHIVE_DIR is where the archive is mounted in the container, not a path on
the archive host. Writes never name a root at all -- `fk-archive` looks it up
by profile -- so this and the profile's `root` no longer have to spell the same
string; the mount has to be of that directory, which is ingest.archiveVolume's
business.
*/}}
{{- define "ingest.archiveEnv" -}}
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

{{/*
The ingest image, named once.

Both Deployments run it and the upload pod reports it over
/ingest-api/formats, so an operator can tell which image answered a question
about format revisions. That is only worth anything if the string reported is
the string running, hence one definition rather than three.
*/}}
{{- define "ingest.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.Version }}
{{- end }}

{{/*
Environment for the ingest container.

The order is the order it has always been in, so that splitting these helpers
out for the worker pool does not roll the upload pod for no reason.
*/}}
{{- define "ingest.env" -}}
- name: FK_PORT
  value: {{ .Values.service.port | quote }}
# Observability only. Reported by /ingest-api/formats so that a sweep run
# mid-rollout can be told apart from one run against a settled deployment.
- name: FK_IMAGE
  value: {{ include "ingest.image" . | quote }}
{{ include "ingest.apiEnv" . }}
- name: FK_TUSD_DIR
  value: {{ .Values.uploads.mountPath | quote }}
# Both containers mount the upload volume at the same path, so the paths tusd
# reports are the paths ingest reads.
- name: FK_TUSD_UPLOAD_DIR
  value: {{ .Values.uploads.mountPath | quote }}
- name: FK_WORK_DIR
  value: {{ .Values.work.mountPath | quote }}
{{ include "ingest.archiveEnv" . }}
{{- end }}

{{- define "ingest.workerEnv" -}}
- name: FK_WORK_DIR
  value: {{ .Values.workers.work.mountPath | quote }}
# The pod name, so an operator reading claimed_by on a stuck job knows which
# pod to go and look at.
- name: FK_WORKER_NAME
  valueFrom:
    fieldRef:
      fieldPath: metadata.name
{{- with .Values.workers.kind }}
# What this pool can reach, not what it prefers. An upload's source is in the
# upload volume, which no worker mounts.
- name: FK_WORKER_KIND
  value: {{ . | quote }}
{{- end }}
- name: FK_WORKER_POLL_INTERVAL_S
  value: {{ .Values.workers.pollIntervalSeconds | quote }}
{{ include "ingest.apiEnv" . }}
{{ include "ingest.archiveEnv" . }}
{{- end }}

{{/*
Expand the name of the chart.
*/}}
{{- define "payments-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "payments-platform.fullname" -}}
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
{{- define "payments-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "payments-platform.labels" -}}
helm.sh/chart: {{ include "payments-platform.chart" . }}
{{ include "payments-platform.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "payments-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "payments-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "payments-platform.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "payments-platform.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Database URL Secret name. The chart consumes an existing Secret and never renders DB credentials.
*/}}
{{- define "payments-platform.databaseSecretName" -}}
{{- required "database.existingSecret.name is required" .Values.database.existingSecret.name -}}
{{- end }}

{{/*
Migration Job name.
*/}}
{{- define "payments-platform.migrationJobName" -}}
{{- printf "%s-migrate" (include "payments-platform.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}

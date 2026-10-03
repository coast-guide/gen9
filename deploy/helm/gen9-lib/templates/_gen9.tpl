{{/*
What every Gen9 stack's chart shares (docs/plans/deploy.md, U3). Each stack is its own release in
its own namespace, gen9-<stack>, as each is its own Compose project; these keep the same names and
the same isolation as Compose's networks.
*/}}

{{/* Labels for one component of a stack: (dict "root" $ "component" "postgres") */}}
{{- define "gen9.labels" -}}
{{ include "gen9.selector" . }}
app.kubernetes.io/part-of: gen9
app.kubernetes.io/version: {{ .root.Chart.AppVersion | default .root.Chart.Version | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- end }}

{{/* What a Service or a workload selects a component by */}}
{{- define "gen9.selector" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

{{/*
One of Gen9's own images, by digest, from the lock make passes as global.images (scripts/images.sh):
(dict "root" $ "name" "postgres"). There is no default: what runs is what the lock says.
*/}}
{{- define "gen9.image" -}}
{{- $ref := index (.root.Values.global.images | default dict) .name -}}
{{- if not $ref -}}
{{- fail (printf "global.images.%s is not set: deploy from a lock (make k8s-up IMAGES=<lock>)" .name) -}}
{{- end -}}
{{- if not (regexMatch "@sha256:[0-9a-f]{64}$" $ref) -}}
{{- fail (printf "global.images.%s must be by digest, not %s" .name $ref) -}}
{{- end -}}
{{- $ref -}}
{{- end }}

{{/*
Who may connect to this stack's pods: its own namespace, and the stacks that use it (usedBy), only
to the ports of the services aliased on its gen9-<stack> network: as a Compose stack's own network
plus that one.
*/}}
{{- define "gen9.networkPolicy" -}}
{{- $compose := include "gen9.compose" . | fromJson -}}
{{- $own := printf "gen9-%s" .Values.stack -}}
{{- $ports := list -}}
{{- range $name, $svc := $compose.services }}
{{- with index (include "gen9.networks" $svc | fromJson) $own }}
{{- with (index (include "gen9.services" $ | fromJson) $name).ports }}
{{- $named := . }}{{ range $p := keys $named | sortAlpha }}{{ $ports = append $ports (index $named $p) }}{{ end }}
{{- end }}
{{- end }}
{{- end }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $own }}
  labels:
    app.kubernetes.io/part-of: gen9
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector: {}
    {{- if and .Values.usedBy $ports }}
    - from:
      {{- range $stack := .Values.usedBy }}
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: gen9-{{ $stack }}
      {{- end }}
      ports:
      {{- range $port := $ports | uniq }}
        - port: {{ $port }}
      {{- end }}
    {{- end }}
{{- end }}

{{/*
The stack's compose.yaml (linked into each chart), parsed: what drifts least when read from one
place, the images and each container's environment and command, comes from here.
*/}}
{{- define "gen9.compose" -}}
{{- $base := .Files.Get "compose.yaml" | fromYaml -}}
{{- with .Files.Get "compose.override.yaml" -}}
{{- /* As Compose merges an override file: maps key by key, volumes by target (the override's
       wins), env_file appended, anything else replaced */ -}}
{{- $over := fromYaml . -}}
{{- range $name, $o := $over.services | default dict -}}
{{- $b := index $base.services $name | default dict -}}
{{- $volumes := dict -}}{{- $order := list -}}
{{- range $v := concat ($b.volumes | default list) ($o.volumes | default list) -}}
{{- $t := (include "gen9.mount" $v | fromJson).target -}}
{{- if not (hasKey $volumes $t) }}{{ $order = append $order $t }}{{ end -}}
{{- $_ := set $volumes $t $v -}}
{{- end -}}
{{- $files := concat ($b.env_file | default list) ($o.env_file | default list) -}}
{{- $merged := mergeOverwrite (deepCopy $b) $o -}}
{{- if $order }}{{ $vs := list }}{{ range $t := $order }}{{ $vs = append $vs (index $volumes $t) }}{{ end }}{{ $_ := set $merged "volumes" $vs }}{{ end -}}
{{- if $files }}{{ $_ := set $merged "env_file" $files }}{{ end -}}
{{- $_ := set $base.services $name $merged -}}
{{- end -}}
{{- $_ := set $base "networks" (mergeOverwrite ($base.networks | default dict) ($over.networks | default dict)) -}}
{{- $_ := set $base "volumes" (mergeOverwrite ($base.volumes | default dict) ($over.volumes | default dict)) -}}
{{- end -}}
{{- toJson $base -}}
{{- end }}

{{/*
A Compose service's image: (dict "root" $ "service" "postgres"). Gen9's own (${GEN9_…_IMAGE:-…})
by digest from the lock; any other as compose.yaml pins it.
*/}}
{{- define "gen9.composeImage" -}}
{{- $compose := include "gen9.compose" .root | fromJson -}}
{{- $image := toString (index $compose.services .service "image") -}}
{{- if regexMatch "^\\$\\{GEN9_[A-Z_]+_IMAGE:-" $image -}}
{{- $name := regexReplaceAll "^\\$\\{GEN9_([A-Z_]+)_IMAGE:-.*$" $image "${1}" | lower | camelcase | untitle -}}
{{- include "gen9.image" (dict "root" .root "name" $name) -}}
{{- else -}}
{{- $image -}}
{{- end -}}
{{- end }}

{{/*
A Compose service's command (its `command`, Kubernetes' args) and entrypoint (Kubernetes' command),
as YAML lists, empty when compose.yaml sets none: (dict "root" $ "service" "postgres" "key" "command").
*/}}
{{- define "gen9.composeList" -}}
{{- $compose := include "gen9.compose" .root | fromJson -}}
{{- $root := .root -}}
{{- with index $compose.services .service .key -}}
{{- $items := list -}}
{{- $words := . -}}
{{- if kindIs "string" . -}}
{{- /* As Compose splits a string command: into words, shell-style, each quoted part without its
       own quotes (a double quote inside single quotes stays); not run by a shell */ -}}
{{- $words = list -}}
{{- range $word := regexFindAll "(?:'[^']*'|\"[^\"]*\"|[^\\s'\"]+)+" . -1 -}}
{{- $w := "" -}}
{{- range $part := regexFindAll "'[^']*'|\"[^\"]*\"|[^'\"]+" $word -1 -}}
{{- if or (hasPrefix "'" $part) (hasPrefix "\"" $part) }}{{ $w = print $w (substr 1 (int (sub (len $part) 1)) $part) }}{{ else }}{{ $w = print $w $part }}{{ end -}}
{{- end -}}
{{- $words = append $words $w -}}
{{- end -}}
{{- end -}}
{{- range $item := $words -}}
{{- $items = append $items (include "gen9.interpolate" (dict "root" $root "value" (toString $item))) -}}
{{- end -}}
{{- toJson $items -}}
{{- end -}}
{{- end }}

{{/*
A value from compose.yaml as Kubernetes takes it, as Compose would resolve it: (dict "root" $
"value" "…"). ${X:-default} and ${X-default} take the setting X (settings.X, <stack>.settings.X)
when given; else, when the stack's .env sets X (fromEnv, the names make k8s-up passes, never the
values), its value from the Secret env, as $(X), which Kubernetes expands; else the default.
${X:?…} (a value make setup must write) is always $(X). Nested ones resolve innermost first, as in
${DATABASE_URL:-postgresql://postgres:${POSTGRES_PASSWORD:-postgres}@postgres:5432/postgres}.
*/}}
{{- define "gen9.interpolate" -}}
{{- $settings := include "gen9.settings" .root | fromJson -}}
{{- /* Gen9's own images by digest from the lock, as make up reads GEN9_<IMAGE>_IMAGE from images.env */ -}}
{{- range $key, $ref := .root.Values.global.images | default dict -}}
{{- $_ := set $settings (printf "GEN9_%s_IMAGE" (snakecase $key | upper)) $ref -}}
{{- end -}}
{{- $fromEnv := .root.Values.fromEnv | default list -}}
{{- /* $$ is a literal $ to Compose and to Kubernetes alike: kept as it is, never read as a variable */ -}}
{{- $s := .value | replace "$$" "\u0000" -}}
{{- range $_ := until 5 -}}
{{- /* A default may hold braces of its own ({id} in MCP_APPS_SANDBOX_URL), never a ${ */ -}}
{{- range $m := regexFindAll "\\$\\{[A-Z0-9_]+:?[-?](?:[^{}]|\\{[^{}$]*\\})*\\}" $s -1 -}}
{{- $var := regexReplaceAll "^\\$\\{([A-Z0-9_]+).*$" $m "${1}" -}}
{{- $required := regexMatch "^\\$\\{[A-Z0-9_]+:?\\?" $m -}}
{{- $default := regexReplaceAll "^\\$\\{[A-Z0-9_]+:?[-?](.*)\\}$" $m "${1}" -}}
{{- $value := "" -}}
{{- if hasKey $settings $var }}{{ $value = toString (index $settings $var) }}
{{- else if or $required (has $var $fromEnv) }}{{ $value = printf "$(%s)" $var }}
{{- else }}{{ $value = $default }}{{ end -}}
{{- $s = replace $m $value $s -}}
{{- end -}}
{{- end -}}
{{- /* ${X:+value} and ${X+value}: the value when X is set, else empty */ -}}
{{- range $m := regexFindAll "\\$\\{[A-Z0-9_]+:?\\+[^{}]*\\}" $s -1 -}}
{{- $var := regexReplaceAll "^\\$\\{([A-Z0-9_]+).*$" $m "${1}" -}}
{{- $alt := regexReplaceAll "^\\$\\{[A-Z0-9_]+:?\\+(.*)\\}$" $m "${1}" -}}
{{- $s = replace $m (ternary $alt "" (or (hasKey $settings $var) (has $var $fromEnv))) $s -}}
{{- end -}}
{{- /* ${X} and $X: X's value, or empty when nothing sets it */ -}}
{{- range $m := regexFindAll "\\$\\{[A-Z_][A-Z0-9_]*\\}|\\$[A-Z_][A-Z0-9_]*" $s -1 -}}
{{- $var := trimSuffix "}" (trimPrefix "{" (trimPrefix "$" $m)) -}}
{{- $value := "" -}}
{{- if hasKey $settings $var }}{{ $value = toString (index $settings $var) }}
{{- else if has $var $fromEnv }}{{ $value = printf "$(%s)" $var }}{{ end -}}
{{- $s = replace $m $value $s -}}
{{- end -}}
{{- $s | replace "\u0000" "$$" -}}
{{- end }}

{{/*
A Compose service's environment as a container's env: (dict "root" $ "service" "keycloak"), each
value resolved as Compose would (gen9.interpolate). Every $(X) a value or the command refers to is
defined first, from the Secret env (the stack's .env, as make k8s-up makes it), since Kubernetes
expands only variables defined earlier; a variable that is only its own secret is that definition.
*/}}
{{- define "gen9.composeEnv" -}}
{{- $compose := include "gen9.compose" .root | fromJson -}}
{{- $svc := index $compose.services .service -}}
{{- $env := $svc.environment | default dict -}}
{{- $values := dict -}}
{{- range $k, $v := $env -}}
{{- $_ := set $values $k (include "gen9.interpolate" (dict "root" $.root "value" (toString $v))) -}}
{{- end -}}
{{- $scan := concat (values $values) (include "gen9.composeList" (dict "root" .root "service" .service "key" "command") | default "[]" | fromJsonArray) (include "gen9.composeList" (dict "root" .root "service" .service "key" "entrypoint") | default "[]" | fromJsonArray) -}}
{{- $secrets := list -}}
{{- range $v := $scan -}}
{{- range $m := regexFindAll "\\$\\([A-Z0-9_]+\\)" (toString $v) -1 -}}
{{- $secrets = append $secrets (trimSuffix ")" (trimPrefix "$(" $m)) -}}
{{- end -}}
{{- end -}}
{{- range $name := $secrets | uniq | sortAlpha }}
- name: {{ $name }}
  valueFrom: {secretKeyRef: {name: env, key: {{ $name }}}}
{{- end -}}
{{- range $k := keys $values | sortAlpha -}}
{{- $s := index $values $k -}}
{{- if not (and (has $k $secrets) (eq $s (printf "$(%s)" $k))) }}
- name: {{ $k }}
  value: {{ $s | quote }}
{{- end -}}
{{- end -}}
{{- end }}

{{/* A Compose service's networks as a map (name: options), whether compose.yaml lists them or maps them */}}
{{- define "gen9.networks" -}}
{{- $nets := .networks | default dict -}}
{{- if kindIs "slice" $nets -}}
{{- $m := dict -}}{{- range $n := $nets }}{{ $_ := set $m $n dict }}{{ end -}}
{{- $nets = $m -}}
{{- end -}}
{{- toJson $nets -}}
{{- end }}

{{/*
The settings (Compose's ${X:-default} variables) for this stack: settings.X, shared by every stack,
then <stack>.settings.X, its own, as deploy/values.yaml gives them.
*/}}
{{- define "gen9.settings" -}}
{{- $own := (index .Values .Values.stack | default dict).settings | default dict -}}
{{- toJson (mergeOverwrite (deepCopy (.Values.settings | default dict)) $own) -}}
{{- end }}

{{/*
This stack's services: the chart's own values, then <stack>.services from deploy/values.yaml
(e.g. keycloak: {services: {postgres: {storage: {postgres_data: 50Gi}}}}), so one settings file
serves every release without one stack's change reaching another's.
*/}}
{{- define "gen9.services" -}}
{{- $own := (index .Values .Values.stack | default dict).services | default dict -}}
{{- toJson (mergeOverwrite (deepCopy .Values.services) $own) -}}
{{- end }}

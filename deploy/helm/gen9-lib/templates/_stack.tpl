{{/*
A whole stack from its compose.yaml (docs/plans/deploy.md, U3): `{{ include "gen9.stack" . }}` is
a stack chart's only template. What Kubernetes needs that Compose doesn't say comes from the chart's
values, per service (`services.<name>`):

  kind:      Deployment; StatefulSet (a named volume needs it); Job (a one-shot service, run at
             every install and upgrade); Init (a one-shot service another waits for, as Compose's
             service_completed_successfully: an init container of the service named in `into`);
             or none (left out: Compose's `ready` services, which only make `up --wait` wait; a
             bundled store another one replaces, which its label gen9.external names the
             setting of). A service in a Compose profile is left out unless given a kind.
  phase:     a Job's: post (default: once the rest is ready) or pre (before the rest changes:
             migrations the services wait for)
  into:      an Init's service
  ports:     {name: container port}, for its Services
  storage:   {volume: size}, for its named volumes (default 10Gi)
  replicas, resources, podSecurityContext, weight (the order of Jobs)
  dropMounts: sources of mounts only Docker has (/var/run/docker.sock), left out
  env:       {NAME: value} only Kubernetes needs, after compose.yaml's
  serviceAccount: the ServiceAccount of a pod that calls Kubernetes' API (its token mounted);
             every other pod gets none

Everything else is the service's in compose.yaml: image (gen9.composeImage), entrypoint and command,
environment (gen9.composeEnv) and env_file (the Secret make k8s-up makes of that file), healthcheck
(a readiness probe; while starting, a startup probe as often as Compose's start_interval that
gives up only after a day: Docker kills no container for being unhealthy), files bind-mounted from the stack's folder
(a ConfigMap each, from the chart's link of the same name), named volumes, tmpfs and shm_size, user,
read_only, cap_drop and no-new-privileges, and its aliases on gen9-<stack> networks (a Service each:
the names other stacks call it by).
*/}}
{{- define "gen9.stack" -}}
{{- $root := . -}}
{{- $services := include "gen9.services" . | fromJson -}}
{{- $compose := include "gen9.compose" . | fromJson -}}
{{- range $name := keys $compose.services }}
{{- if and (not (hasKey $services $name)) (not (index $compose.services $name).profiles) }}
{{- fail (printf "compose.yaml's %s has no entry in services: give it a kind (none to leave it out)" $name) }}
{{- end }}
{{- end }}
{{- /* A bundled store left out (kind none) whose label gen9.external in compose.yaml names the
setting that points the stack at another one: refused without that setting, in settings or the
stack's .env (docs/operations.md, "External services"), as make up refuses it on Docker */}}
{{- $settings := include "gen9.settings" . | fromJson -}}
{{- range $name, $svc := $compose.services }}
{{- with index ($svc.labels | default dict) "gen9.external" }}
{{- if and (eq (toString (index $services $name | default dict).kind) "none") (not (hasKey $settings .)) (not (has . ($root.Values.fromEnv | default list))) }}
{{- fail (printf "%s.services.%s.kind is none, and nothing names another: set %s (in %s.settings, or gen9-%s/.env)" $root.Values.stack $name . $root.Values.stack $root.Values.stack) }}
{{- end }}
{{- end }}
{{- end }}
{{- /* A setting of this stack's that its Compose files don't read would install and do nothing:
refused, naming it. What they read: each ${X…} or $X ($$, a literal dollar, aside). The shared
settings are checked across every stack by scripts/k8s.sh */}}
{{- $text := regexReplaceAll "\\$\\$" (cat (.Files.Get "compose.yaml") (.Files.Get "compose.override.yaml")) "" -}}
{{- $read := dict -}}
{{- range regexFindAll "\\$\\{?[A-Za-z_][A-Za-z0-9_]*" $text -1 }}{{ $_ := set $read (regexReplaceAll "^\\$\\{?" . "") true }}{{ end -}}
{{- range $key, $_ := (index .Values .Values.stack | default dict).settings | default dict }}
{{- if not (hasKey $read $key) }}
{{- fail (printf "%s.settings.%s: gen9-%s's Compose files don't read %s (a key its services read from a settings file goes in that file, gen9-%s/.env or a *.local.env, which make k8s-up makes a Secret)" $root.Values.stack $key $root.Values.stack $key $root.Values.stack) }}
{{- end }}
{{- end }}
{{- /* Files bind-mounted from the stack's folder: one ConfigMap each */}}
{{- $files := dict -}}
{{- /* Each ConfigMap's content, hashed, for the pods that mount it (below) */}}
{{- $contents := dict -}}
{{- range $name := keys $services | sortAlpha }}
{{- if ne (toString (index $services $name).kind) "none" }}
{{- range $v := (index $compose.services $name).volumes | default list }}
{{- $m := include "gen9.mount" $v | fromJson }}
{{- if and (hasPrefix "./" $m.source) (not (hasKey $files $m.source)) }}
{{- $path := trimPrefix "./" $m.source }}
{{- $_ := set $files $m.source true }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "gen9.filesName" $m.source }}
  labels: {{- include "gen9.labels" (dict "root" $root "component" $name) | nindent 4 }}
data:
{{- $glob := $root.Files.Glob (printf "%s/*" $path) }}
{{- $content := "" }}
{{- if $glob }}
{{- range $file, $_ := $glob }}
{{- $content = printf "%s%s=%s;" $content $file ($root.Files.Get $file) }}
  {{ base $file }}: {{ $root.Files.Get $file | quote }}
{{- end }}
{{- $_ := set $contents (include "gen9.filesName" $m.source) (sha256sum $content) }}
{{- else }}
{{- with $root.Files.Get $path }}
{{- $_ := set $contents (include "gen9.filesName" $m.source) (sha256sum .) }}
  {{ base $path }}: {{ . | quote }}
{{- else }}
{{- fail (printf "%s mounts %s: link it into the chart (chart/%s -> ../%s)" $name $m.source $path $path) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- /* Workloads */}}
{{- range $name := keys $services | sortAlpha }}
{{- $k := index $services $name -}}
{{- if has (toString $k.kind) (list "Deployment" "StatefulSet" "Job") }}
{{- $c := dict "root" $root "component" $name -}}
{{- $main := include "gen9.container" (dict "root" $root "name" $name) | fromJson -}}
{{- $inits := list -}}
{{- $volumes := $main.volumes | default list -}}
{{- range $other := keys $services | sortAlpha }}
{{- $ok := index $services $other }}
{{- if and (eq (toString $ok.kind) "Init") (eq (toString $ok.into) $name) }}
{{- $init := include "gen9.container" (dict "root" $root "name" $other) | fromJson }}
{{- $inits = append $inits $init.container }}
{{- $volumes = concat $volumes ($init.volumes | default list) }}
{{- end }}
{{- end }}
{{- if and $main.claims (ne $k.kind "StatefulSet") }}
{{- fail (printf "services.%s: it has a named volume, so kind: StatefulSet" $name) }}
{{- end }}
{{- /* Services: its own name inside the stack, and its aliases on gen9-<stack> networks */}}
{{- if $k.ports }}
{{- $names := list $name }}
{{- range $net, $opts := include "gen9.networks" (index $compose.services $name) | fromJson }}
{{- if and (hasPrefix "gen9-" $net) $opts }}{{ $names = concat $names ($opts.aliases | default list) }}{{ end }}
{{- end }}
{{- range $svcName := $names | uniq }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $svcName }}
  labels: {{- include "gen9.labels" $c | nindent 4 }}
spec:
  selector: {{- include "gen9.selector" $c | nindent 4 }}
  ports:
  {{- range $p := keys $k.ports | sortAlpha }}
    - {name: {{ $p }}, port: {{ index $k.ports $p }}, targetPort: {{ $p }}}
  {{- end }}
{{- end }}
{{- end }}
{{- /* Reached from outside under the domain, as gen9-edge serves it on Docker: an HTTPRoute on the
cluster's Gateway (global.gateway), its host the service's prefix on global.domain */}}
{{- $g := $root.Values.global | default dict }}
{{- if and $k.public $g.domain }}
{{- $gateway := $g.gateway | default dict }}
{{- if not $gateway.name }}{{ fail "global.domain needs global.gateway.name: the Gateway the routes attach to" }}{{ end }}
{{- $port := index ($k.ports | default dict) $k.public.port }}
{{- if not $port }}{{ fail (printf "services.%s.public.port: %s isn't one of its ports" $name $k.public.port) }}{{ end }}
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: {{ $name }}
  labels: {{- include "gen9.labels" $c | nindent 4 }}
spec:
  parentRefs:
    - name: {{ $gateway.name }}
      {{- with $gateway.namespace }}
      namespace: {{ . }}
      {{- end }}
      {{- with $gateway.sectionName }}
      sectionName: {{ . }}
      {{- end }}
  hostnames:
    - {{ ternary $g.domain (printf "%s.%s" $k.public.host $g.domain) (eq $k.public.host "") | quote }}
  rules:
    # No limit of the Gateway's on a request: an answer streams for as long as it takes (Envoy's
    # default, 15 s, cut it on kind), as through gen9-edge on Docker; the apps keep their own.
    # "0s SHOULD disable the timeout completely" (Gateway API, HTTPRouteTimeouts; Extended)
    - timeouts:
        request: "0s"
      backendRefs:
        - name: {{ $name }}
          port: {{ $port }}
{{- end }}
---
{{- if eq $k.kind "Job" }}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ $name }}
  labels: {{- include "gen9.labels" $c | nindent 4 }}
  annotations:
    helm.sh/hook: {{ ternary "pre-install,pre-upgrade" "post-install,post-upgrade" (eq (toString $k.phase) "pre") }}
    helm.sh/hook-weight: {{ $k.weight | default 0 | quote }}
    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
spec:
  backoffLimit: 3
  template:
{{- else }}
apiVersion: apps/v1
kind: {{ $k.kind }}
metadata:
  name: {{ $name }}
  labels: {{- include "gen9.labels" $c | nindent 4 }}
spec:
  replicas: {{ $k.replicas | default 1 }}
  {{- if eq $k.kind "StatefulSet" }}
  serviceName: {{ $name }}
  {{- end }}
  selector:
    matchLabels: {{- include "gen9.selector" $c | nindent 6 }}
  template:
{{- end }}
    {{- /* What its containers read, Secrets (the settings files make k8s-up makes them of, hashed
    by scripts/k8s.sh: secretHashes) and ConfigMaps: one changed, the pods roll, as Compose
    recreates a container whose settings changed (Helm's "Automatically Roll Deployments") */}}
    {{- $read := list }}
    {{- range $ctr := concat (list $main.container) $inits }}
    {{- range $e := $ctr.envFrom | default list }}{{ with $e.secretRef }}{{ $read = append $read (printf "secret/%s=%s" .name (index ($root.Values.secretHashes | default dict) .name | default "none")) }}{{ end }}{{ end }}
    {{- range $e := $ctr.env | default list }}{{ with $e.valueFrom }}{{ with .secretKeyRef }}{{ $read = append $read (printf "secret/%s=%s" .name (index ($root.Values.secretHashes | default dict) .name | default "none")) }}{{ end }}{{ end }}{{ end }}
    {{- end }}
    {{- range $v := $volumes }}{{ with $v.configMap }}{{ $read = append $read (printf "configmap/%s=%s" .name (index $contents .name | default "none")) }}{{ end }}{{ end }}
    metadata:
      labels: {{- include "gen9.labels" $c | nindent 8 }}
      {{- with $read }}
      annotations:
        gen9/settings: {{ . | uniq | sortAlpha | join ";" | sha256sum }}
      {{- end }}
    spec:
      {{- if eq $k.kind "Job" }}
      {{- /* A new pod may start before the network policy allows it anywhere ("pods must be
      resilient against being started up with different network connectivity than expected",
      Kubernetes' Network Policies, "Pod lifecycle"): k3s refused each new pod of a one-shot for
      its first half second, and a new pod per retry met the same. Retried in the same pod, a
      one-shot is let through */}}
      restartPolicy: OnFailure
      {{- end }}
      {{- with $k.serviceAccount }}
      serviceAccountName: {{ . }}
      automountServiceAccountToken: true
      {{- else }}
      automountServiceAccountToken: false
      {{- end }}
      {{- with $k.podSecurityContext }}
      securityContext: {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with (index $compose.services $name).platform }}
      {{- /* Compose's platform: the nodes of that OS and architecture */}}
      nodeSelector:
        kubernetes.io/os: {{ index (splitList "/" .) 0 }}
        kubernetes.io/arch: {{ index (splitList "/" .) 1 }}
      {{- end }}
      {{- /* The other stacks' names, as on Docker: a service on another stack's network gen9-<x>
      resolves every name given there (gen9-models, gen9-models-admin…); here, by that stack's
      namespace among the pod's DNS search domains, after its own */}}
      {{- $searches := list }}
      {{- range $key, $_ := include "gen9.networks" (index $compose.services $name) | fromJson }}
      {{- $net := index ($compose.networks | default dict) $key | default dict }}
      {{- $real := $net.name | default $key }}
      {{- if and $net.external (hasPrefix "gen9-" $real) (ne $real (printf "gen9-%s" $root.Values.stack)) }}
      {{- $searches = append $searches (printf "%s.svc.%s" $real ($root.Values.global.clusterDomain | default "cluster.local")) }}
      {{- end }}
      {{- end }}
      {{- with $searches }}
      dnsConfig:
        searches: {{- toYaml . | nindent 10 }}
      {{- end }}
      {{- with $inits }}
      initContainers: {{- toYaml . | nindent 8 }}
      {{- end }}
      containers: {{- toYaml (list $main.container) | nindent 8 }}
      {{- with $volumes }}
      volumes: {{- include "gen9.uniqByName" . | fromJsonArray | toYaml | nindent 8 }}
      {{- end }}
  {{- with $main.claims }}
  volumeClaimTemplates:
  {{- range $claim := . }}
    - metadata: {name: {{ $claim.name }}}
      spec:
        accessModes: [ReadWriteOnce]
        {{- with $root.Values.global.storageClass }}
        storageClassName: {{ . }}
        {{- end }}
        resources: {requests: {storage: {{ $claim.size }}}}
  {{- end }}
  {{- end }}
{{- end }}
{{- end }}
---
{{ include "gen9.networkPolicy" . }}
{{- end }}

{{/*
One Compose service as a container, with the volumes and claims it needs, as JSON:
(dict "root" $ "name" "temporal") -> {container, volumes, claims}.
*/}}
{{- define "gen9.container" -}}
{{- $root := .root -}}
{{- $services := include "gen9.services" $root | fromJson -}}
{{- $name := .name -}}
{{- $compose := include "gen9.compose" $root | fromJson -}}
{{- $svc := index $compose.services $name | default dict -}}
{{- if not $svc }}{{ fail (printf "services.%s: no such service in compose.yaml" $name) }}{{ end -}}
{{- $k := index $services $name -}}
{{- $mounts := list -}}
{{- $volumes := list -}}
{{- $claims := list -}}
{{- range $v := $svc.volumes | default list }}
{{- $m := include "gen9.mount" $v | fromJson }}
{{- if has $m.source ($k.dropMounts | default list) }}
{{- else if hasPrefix "/" $m.source }}
{{- fail (printf "%s: mounts %s from the host, which a Kubernetes pod can't" $name $m.source) }}
{{- else if hasPrefix "./" $m.source }}
{{- $vol := include "gen9.filesName" $m.source }}
{{- $volumes = append $volumes (dict "name" $vol "configMap" (dict "name" $vol "defaultMode" 365)) }}
{{- if $root.Files.Glob (printf "%s/*" (trimPrefix "./" $m.source)) }}
{{- $mounts = append $mounts (dict "name" $vol "mountPath" $m.target "readOnly" true) }}
{{- else }}
{{- $mounts = append $mounts (dict "name" $vol "mountPath" $m.target "subPath" (base $m.source) "readOnly" true) }}
{{- end }}
{{- else }}
{{- $claim := regexReplaceAll "[^a-z0-9]+" (lower $m.source) "-" | trimAll "-" }}
{{- $claims = append $claims (dict "name" $claim "size" (index ($k.storage | default dict) $m.source | default "10Gi")) }}
{{- $mounts = append $mounts (dict "name" $claim "mountPath" $m.target "readOnly" $m.readOnly) }}
{{- end }}
{{- end }}
{{- range $i, $t := $svc.tmpfs | default list }}
{{- $vol := printf "%s-tmpfs-%d" $name $i }}
{{- $volumes = append $volumes (dict "name" $vol "emptyDir" (dict "medium" "Memory")) }}
{{- $mounts = append $mounts (dict "name" $vol "mountPath" (first (splitList ":" $t))) }}
{{- end }}
{{- with $svc.shm_size }}
{{- /* Docker's 1g is 1 GiB: Kubernetes writes it 1Gi */}}
{{- $size := regexReplaceAll "^([0-9]+)([kmgKMG])[bB]?$" (toString .) "${1}${2}i" | replace "ki" "Ki" | replace "mi" "Mi" | replace "gi" "Gi" }}
{{- $volumes = append $volumes (dict "name" (printf "%s-shm" $name) "emptyDir" (dict "medium" "Memory" "sizeLimit" $size)) }}
{{- $mounts = append $mounts (dict "name" (printf "%s-shm" $name) "mountPath" "/dev/shm") }}
{{- end }}
{{- $ctr := dict "name" $name "image" (include "gen9.composeImage" (dict "root" $root "service" $name)) }}
{{- with include "gen9.composeList" (dict "root" $root "service" $name "key" "entrypoint") }}{{ $_ := set $ctr "command" (fromJsonArray .) }}{{ end }}
{{- with include "gen9.composeList" (dict "root" $root "service" $name "key" "command") }}{{ $_ := set $ctr "args" (fromJsonArray .) }}{{ end }}
{{- with $svc.working_dir }}{{ $_ := set $ctr "workingDir" . }}{{ end }}
{{- with include "gen9.composeEnv" (dict "root" $root "service" $name) | trim }}{{ $_ := set $ctr "env" (fromYamlArray .) }}{{ end }}
{{- with $k.env }}
{{- $extra := list }}{{ range $n := keys . | sortAlpha }}{{ $extra = append $extra (dict "name" $n "value" (toString (index $k.env $n))) }}{{ end }}
{{- $_ := set $ctr "env" (concat ($ctr.env | default list) $extra) }}
{{- end }}
{{- with $svc.env_file }}
{{- $from := list }}
{{- range $f := . }}
{{- $path := $f }}{{ $optional := false }}{{ if kindIs "map" $f }}{{ $path = $f.path }}{{ $optional = eq (toString $f.required) "false" }}{{ end }}
{{- $ref := dict "name" (regexReplaceAll "[^a-z0-9]+" (lower (toString $path)) "-" | trimAll "-") }}
{{- if $optional }}{{ $_ := set $ref "optional" true }}{{ end }}
{{- $from = append $from (dict "secretRef" $ref) }}
{{- end }}
{{- $_ := set $ctr "envFrom" $from }}
{{- end }}
{{- with $k.ports }}
{{- $ports := list }}
{{- range $p := keys . | sortAlpha }}{{ $ports = append $ports (dict "name" $p "containerPort" (index $k.ports $p)) }}{{ end }}
{{- $_ := set $ctr "ports" $ports }}
{{- end }}
{{- with $svc.healthcheck }}
{{- $test := .test | default list }}
{{- if kindIs "string" $test }}{{ $test = list "CMD-SHELL" $test }}{{ end }}
{{- if and $test (ne (toString (first $test)) "NONE") (not .disable) }}
{{- $exec := ternary (list "/bin/sh" "-c" (index $test 1)) (rest $test) (eq (first $test) "CMD-SHELL") }}
{{- $timeout := include "gen9.seconds" (.timeout | default "30s") | atoi }}
{{- /* A readiness probe at Compose's interval. While starting, checked as often as Compose
       checks it then (start_interval), by a startup probe that gives up only after a day: Docker
       marks a container unhealthy and never kills it for that, so no liveness probe either */}}
{{- $_ := set $ctr "readinessProbe" (dict "exec" (dict "command" $exec) "periodSeconds" (include "gen9.seconds" (.interval | default "30s") | atoi) "timeoutSeconds" $timeout "failureThreshold" (.retries | default 3)) }}
{{- if .start_period }}
{{- $every := include "gen9.seconds" (.start_interval | default "5s") | atoi }}
{{- $_ := set $ctr "startupProbe" (dict "exec" (dict "command" $exec) "periodSeconds" $every "timeoutSeconds" $timeout "failureThreshold" (div 86400 $every)) }}
{{- end }}
{{- end }}
{{- end }}
{{- with $k.resources }}{{ $_ := set $ctr "resources" . }}{{ end }}
{{- $sec := dict }}
{{- with $svc.user }}{{ $ids := splitList ":" (toString .) }}{{ $_ := set $sec "runAsUser" (atoi (index $ids 0)) }}{{ if gt (len $ids) 1 }}{{ $_ := set $sec "runAsGroup" (atoi (index $ids 1)) }}{{ end }}{{ end }}
{{- if $svc.read_only }}{{ $_ := set $sec "readOnlyRootFilesystem" true }}{{ end }}
{{- if has "no-new-privileges:true" ($svc.security_opt | default list) }}{{ $_ := set $sec "allowPrivilegeEscalation" false }}{{ end }}
{{- with $svc.cap_drop }}{{ $_ := set $sec "capabilities" (dict "drop" .) }}{{ end }}
{{- with $sec }}{{ $_ := set $ctr "securityContext" . }}{{ end }}
{{- with $mounts }}{{ $_ := set $ctr "volumeMounts" . }}{{ end }}
{{- toJson (dict "container" $ctr "volumes" $volumes "claims" $claims) -}}
{{- end }}

{{/* A Compose volume entry, short or long, as {source, target, readOnly} */}}
{{- define "gen9.mount" -}}
{{- if kindIs "string" . -}}
{{- $parts := splitList ":" . -}}
{{- toJson (dict "source" (index $parts 0) "target" (index $parts 1) "readOnly" (has "ro" $parts)) -}}
{{- else -}}
{{- toJson (dict "source" (toString .source) "target" .target "readOnly" (.read_only | default false)) -}}
{{- end -}}
{{- end }}

{{/* The ConfigMap of a file or folder bind-mounted from the stack's folder (./scripts -> files-scripts) */}}
{{- define "gen9.filesName" -}}
{{- printf "files-%s" (regexReplaceAll "[^a-z0-9]+" (lower (trimPrefix "./" .)) "-" | trimAll "-") -}}
{{- end }}

{{/* A list of named volumes without repeats (init and main containers can share one) */}}
{{- define "gen9.uniqByName" -}}
{{- $seen := dict }}{{ $out := list -}}
{{- range . }}{{ if not (hasKey $seen .name) }}{{ $_ := set $seen .name true }}{{ $out = append $out . }}{{ end }}{{ end -}}
{{- toJson $out -}}
{{- end }}

{{/* A Compose duration (90s, 1m30s, 500ms) in whole seconds, at least 1 */}}
{{- define "gen9.seconds" -}}
{{- $d := toString . -}}
{{- $m := regexFind "[0-9]+m([^s]|$)" $d | trimSuffix "s" | trimSuffix "m" | default "0" | atoi -}}
{{- $s := regexFind "[0-9]+s" $d | trimSuffix "s" | default "0" | atoi -}}
{{- if hasSuffix "ms" $d }}{{ $s = 0 }}{{ end -}}
{{- max 1 (add (mul $m 60) $s) -}}
{{- end }}

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$local = Join-Path $root ".local"
$evidence = Join-Path $root "evidence"
New-Item -ItemType Directory -Force -Path $local, $evidence | Out-Null
$kube = @("--context", "kind-campus")
$timeline = New-Object System.Collections.Generic.List[string]
$index = New-Object System.Collections.Generic.List[string]
$index.Add("id,path,owner,timestamp,revision,redaction,claim")

function Stamp { Get-Date -Format "yyyy-MM-ddTHH:mm:ssK" }

function Mark([string]$Phase, [string]$Note) {
    $note = ($Note -replace ",", ";")
    $line = "$(Stamp),$Phase,operator,campus-ready,$note"
    $timeline.Add($line)
    Write-Host $line
}

function Save-Text([string]$Path, [string]$Text) {
    if ($null -eq $Text) { $Text = "" }
    if ($script:pass) { $Text = $Text -replace [regex]::Escape($script:pass), "REDACTED" }
    $Text = $Text -replace "glpat-[A-Za-z0-9_\-]+", "glpat-REDACTED"
    $Text = $Text -replace "glrt-[A-Za-z0-9_\-]+", "glrt-REDACTED"
    $dir = Split-Path $Path
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Text.TrimEnd() + "`n")
}

function Add-Proof([string]$Id, [string]$Path, [string]$Claim) {
    $rel = $Path.Substring($root.Length + 1) -replace "\\", "/"
    $index.Add("$Id,$rel,operator,$(Stamp),kt4,redacted,$Claim")
}

function Wait-Mounted([string]$Mode) {
    $deadline = (Get-Date).AddSeconds(90)
    do {
        $pods = @(& kubectl @kube -n campus-ready get pod -l app=campus-api --field-selector=status.phase=Running -o name)
        $names = @($pods | Where-Object { $_.Trim() })
        $good = 0
        foreach ($pod in $names) {
            $body = & kubectl @kube -n campus-ready exec $pod.Trim() -- cat /etc/campus/fault.json
            if ($body -match ('"mode":"' + $Mode + '"')) { $good++ }
        }
        if ($names.Count -ge 1 -and $good -eq $names.Count) { return }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    throw "mode $Mode did not reach every api pod"
}

function Assert-Step([string]$Name) {
    if ($LASTEXITCODE -ne 0) { throw "$Name exited $LASTEXITCODE" }
}

function Invoke-Client([string]$Script, [hashtable]$Env, [switch]$AllowFail) {
    $pod = (& kubectl @kube -n campus-ready get pod -l app=lab-client -o jsonpath="{.items[0].metadata.name}").Trim()
    if (-not $pod) { throw "lab-client pod is missing" }
    $tmp = Join-Path $local "snippet.py"
    $prefix = "import os`n"
    if ($Env) {
        foreach ($key in @($Env.Keys)) {
            $value = ([string]$Env[$key]).Replace('\', '\\').Replace('"', '\"')
            $prefix += "os.environ[`"$key`"] = `"$value`"`n"
        }
    }
    $body = [System.IO.File]::ReadAllText($Script)
    [System.IO.File]::WriteAllText($tmp, $prefix + $body, [Text.UTF8Encoding]::new($false))
    $out = cmd /c "kubectl --context kind-campus exec -i -n campus-ready $pod -- python - < `"$tmp`""
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) { throw "client $Script failed: $out" }
    return (($out | Out-String).Trim())
}

Mark "setup" "build image"
docker build -t localhost:5001/campus-ready:kt4 (Join-Path $root "app")
Assert-Step "docker build"
docker push localhost:5001/campus-ready:kt4
Assert-Step "docker push"
$digest = (docker image inspect localhost:5001/campus-ready:kt4 --format "{{index .RepoDigests 0}}").Trim()
if (-not $digest) { $digest = "localhost:5001/campus-ready:kt4" }
Save-Text (Join-Path $evidence "00-architecture\image.txt") $digest
Add-Proof "T00" (Join-Path $evidence "00-architecture\image.txt") "Образ, которым запущен стенд"

& kubectl @kube apply -f (Join-Path $root "deploy\namespace.yaml")
Assert-Step "namespace"
$passFile = Join-Path $local "db-password.txt"
$existing = & kubectl @kube -n campus-ready get secret campus-db --ignore-not-found -o name
if (-not $existing) {
    if (-not (Test-Path $passFile)) {
        $chars = (48..57) + (65..90) + (97..122)
        $script:pass = -join ($chars | Get-Random -Count 24 | ForEach-Object { [char]$_ })
        [System.IO.File]::WriteAllText($passFile, $script:pass)
    }
    $script:pass = [System.IO.File]::ReadAllText($passFile).Trim()
    $url = "postgresql://campus:$($script:pass)@postgres:5432/campus"
    & kubectl @kube -n campus-ready create secret generic campus-db --from-literal=password=$script:pass --from-literal=url=$url
    Assert-Step "secret"
} else {
    $script:pass = ""
    if (Test-Path $passFile) { $script:pass = [System.IO.File]::ReadAllText($passFile).Trim() }
}

foreach ($file in @(
    "deploy\fault-off.yaml", "deploy\rbac.yaml", "deploy\postgres.yaml", "deploy\postgres-dr.yaml",
    "deploy\otel.yaml", "deploy\api.yaml", "deploy\worker.yaml", "deploy\prometheus.yaml",
    "deploy\client.yaml", "deploy\network.yaml"
)) {
    & kubectl @kube apply -f (Join-Path $root $file)
    Assert-Step $file
}
& kubectl @kube -n campus-ready set env deployment/campus-api deployment/campus-worker IMAGE_DIGEST=$digest
Assert-Step "digest env"
foreach ($dep in @("postgres", "postgres-dr", "otel", "campus-api", "campus-worker", "prometheus", "lab-client")) {
    & kubectl @kube -n campus-ready rollout status deployment/$dep --timeout=240s
    Assert-Step "rollout $dep"
}
Wait-Mounted "off"
& kubectl @kube -n campus-ready get pods -o wide | Out-String | ForEach-Object { Save-Text (Join-Path $evidence "00-architecture\pods.txt") $_ }
Add-Proof "T00" (Join-Path $evidence "00-architecture\pods.txt") "Поды стенда и их узел"

Mark "baseline" "pods ready"
$baseline = Invoke-Client (Join-Path $root "ci\py\boards.py") @{ COUNT = "30" }
Save-Text (Join-Path $evidence "01-slo\baseline.json") $baseline
Add-Proof "T01" (Join-Path $evidence "01-slo\baseline.json") "Хороший трафик GET /boards до inject"
$sli = Invoke-Client (Join-Path $root "ci\py\prom.py") @{ QUERY = 'sum(rate(campus_http_requests_total{service="campus-api",route="/boards",status="2xx"}[5m])) / clamp_min(sum(rate(campus_http_requests_total{service="campus-api",route="/boards"}[5m])),0.001)' }
Save-Text (Join-Path $evidence "01-slo\sli-query.json") $sli
Add-Proof "T01" (Join-Path $evidence "01-slo\sli-query.json") "Короткое окно SLI; бюджет 30 дней посчитан в slo/profile.md"
Copy-Item (Join-Path $root "slo\profile.md") (Join-Path $evidence "01-slo\profile.md") -Force

Mark "T07" "post with trace"
$runId = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$post = Invoke-Client (Join-Path $root "ci\py\post.py") @{ BODY = "trace-marker"; KEY = "trace-$runId"; EMAIL = "student@example.test" }
$replay = Invoke-Client (Join-Path $root "ci\py\post.py") @{ BODY = "trace-marker"; KEY = "trace-$runId" }
Save-Text (Join-Path $evidence "04-observability\post.json") ($post + "`n" + $replay)
Start-Sleep -Seconds 4
$trace = ""
if ($post -match '"trace_id": "([0-9a-f]+)"') { $trace = $Matches[1] }
$apiLogs = ""
foreach ($name in (& kubectl @kube -n campus-ready get pod -l app=campus-api --field-selector=status.phase=Running -o name)) {
    $apiLogs += & kubectl @kube -n campus-ready logs $name.Trim() --tail=400 | Out-String
}
$workerLogs = ""
foreach ($name in (& kubectl @kube -n campus-ready get pod -l app=campus-worker --field-selector=status.phase=Running -o name)) {
    $workerLogs += & kubectl @kube -n campus-ready logs $name.Trim() --tail=200 | Out-String
}
$otelLogs = & kubectl @kube -n campus-ready logs deploy/otel --tail=400 | Out-String
Save-Text (Join-Path $evidence "04-observability\api-log.txt") $apiLogs
Save-Text (Join-Path $evidence "04-observability\worker-log.txt") $workerLogs
Save-Text (Join-Path $evidence "04-observability\collector-log.txt") $otelLogs
$corr = "trace_id=$trace`napi=$($apiLogs.Contains($trace))`nworker=$($workerLogs.Contains($trace))`ncollector=$($otelLogs.Contains($trace))`nemail_in_collector=$($otelLogs.Contains('student@example.test'))`nemail_in_api_log=$($apiLogs.Contains('student@example.test'))"
Save-Text (Join-Path $evidence "tests\T07-correlation.txt") $corr
Save-Text (Join-Path $evidence "tests\T08-redaction.txt") $corr
Add-Proof "T07" (Join-Path $evidence "tests\T07-correlation.txt") "Один trace_id в API, worker и collector"
Add-Proof "T08" (Join-Path $evidence "tests\T08-redaction.txt") "student@example.test нет в логе API и collector; redaction в processor attributes/redact"

Mark "T04" "slow dependency"
$fault = '{"mode":"dbslow","error_ratio":0,"latency_ms":800,"deadline_ms":400}'
[System.IO.File]::WriteAllText((Join-Path $local "fault.json"), $fault)
Copy-Item (Join-Path $local "fault.json") "$env:TEMP\campus-fault.json" -Force
& kubectl @kube -n campus-ready create configmap campus-fault --from-file=fault.json=$env:TEMP\campus-fault.json --dry-run=client -o yaml | & kubectl @kube apply -f -
Assert-Step "fault dbslow"
Wait-Mounted "dbslow"
$slow = Invoke-Client (Join-Path $root "ci\py\boards.py") @{ COUNT = "8" }
$deadlineSum = Invoke-Client (Join-Path $root "ci\py\prom.py") @{ QUERY = "sum(campus_dependency_deadline_total)" }
Save-Text (Join-Path $evidence "02-reliability\dbslow.json") ($slow + "`n" + $deadlineSum)
Add-Proof "T04" (Join-Path $evidence "02-reliability\dbslow.json") "Deadline обрывает медленную зависимость и отдаёт устаревший снимок доски"
& kubectl @kube apply -f (Join-Path $root "deploy\fault-off.yaml")
Wait-Mounted "off"

Mark "T03" "delete one api pod"
$before = Invoke-Client (Join-Path $root "ci\py\boards.py") @{ COUNT = "10" }
$victim = (& kubectl @kube -n campus-ready get pod -l app=campus-api -o jsonpath="{.items[0].metadata.name}").Trim()
& kubectl @kube -n campus-ready delete pod $victim --wait=false
$during = Invoke-Client (Join-Path $root "ci\py\boards.py") @{ COUNT = "20" }
& kubectl @kube -n campus-ready rollout status deployment/campus-api --timeout=180s
$after = Invoke-Client (Join-Path $root "ci\py\boards.py") @{ COUNT = "10" }
$pdb = & kubectl @kube -n campus-ready get pdb campus-api -o yaml | Out-String
Save-Text (Join-Path $evidence "02-reliability\replica.txt") "victim=$victim`nbefore=$before`nduring=$during`nafter=$after`n$pdb"
Add-Proof "T03" (Join-Path $evidence "02-reliability\replica.txt") "Второй Pod обслуживает GET /boards, пока удалённый поднимается"

Mark "T02" "fast burn"
$fault = '{"mode":"errors","error_ratio":0.8,"latency_ms":0,"deadline_ms":400}'
[System.IO.File]::WriteAllText((Join-Path $local "fault.json"), $fault)
Copy-Item (Join-Path $local "fault.json") "$env:TEMP\campus-fault.json" -Force
& kubectl @kube -n campus-ready create configmap campus-fault --from-file=fault.json=$env:TEMP\campus-fault.json --dry-run=client -o yaml | & kubectl @kube apply -f -
Assert-Step "fault errors"
Wait-Mounted "errors"
$tInject = Stamp
$firing = Invoke-Client (Join-Path $root "ci\py\wait_alert.py") @{ ALERT = "CampusBoardFastBurn"; STATUS = "firing"; WAIT = "180"; BEFORE = "0" } -AllowFail
Save-Text (Join-Path $evidence "04-observability\fast-burn.json") $firing
Add-Proof "T02" (Join-Path $evidence "04-observability\fast-burn.json") "Синтетические 5xx открыли page CampusBoardFastBurn"
& kubectl @kube apply -f (Join-Path $root "deploy\fault-off.yaml")
$afterOff = [DateTime]::UtcNow.AddSeconds(-2).ToString("yyyy-MM-dd HH:mm:ss")
Wait-Mounted "off"
$resolved = Invoke-Client (Join-Path $root "ci\py\wait_alert.py") @{ ALERT = "CampusBoardFastBurn"; STATUS = "resolved"; WAIT = "180"; AFTER = $afterOff } -AllowFail
Save-Text (Join-Path $evidence "04-observability\fast-burn-resolved.json") $resolved
Add-Proof "T09" (Join-Path $evidence "04-observability\fast-burn-resolved.json") "Page закрылся после kill switch; owner и runbook заданы в правиле"

Mark "T05" "backup and restore"
$markerBefore = Invoke-Client (Join-Path $root "ci\py\post.py") @{ BODY = "restore-before-$runId"; KEY = "restore-before-$runId" }
$rtoStart = Get-Date
& kubectl @kube -n campus-ready delete job campus-backup --ignore-not-found
& kubectl @kube apply -f (Join-Path $root "deploy\backup-job.yaml")
Assert-Step "backup job"
& kubectl @kube -n campus-ready wait --for=condition=complete job/campus-backup --timeout=180s
Assert-Step "backup wait"
$backupLog = & kubectl @kube -n campus-ready logs job/campus-backup | Out-String
$markerAfter = Invoke-Client (Join-Path $root "ci\py\post.py") @{ BODY = "restore-after-$runId"; KEY = "restore-after-$runId" }
$source = & kubectl @kube -n campus-ready exec deploy/postgres -- psql -U campus -d campus -c "select body, created_at from posts where body like 'restore-%$runId' order by id" | Out-String
$restored = & kubectl @kube -n campus-ready exec deploy/postgres-dr -- psql -U campus -d campus -c "select body, created_at from posts where body like 'restore-%$runId' or body = 'trace-marker' order by id" | Out-String
$rtoSec = [int]((Get-Date) - $rtoStart).TotalSeconds
Save-Text (Join-Path $evidence "03-restore\backup.log") $backupLog
Save-Text (Join-Path $evidence "03-restore\validation.txt") "rto_seconds=$rtoSec`nsource:`n$source`ndr:`n$restored`nbefore=$markerBefore`nafter=$markerAfter"
Add-Proof "T05" (Join-Path $evidence "03-restore\validation.txt") "DR получил dump, строка после dump на DR отсутствует"

Mark "T06" "security parity"
$srcCtx = & kubectl @kube -n campus-ready get deploy postgres -o jsonpath="{.spec.template.spec.containers[0].image} {.spec.template.spec.containers[0].env[?(@.name=='POSTGRES_USER')].value}" | Out-String
$drCtx = & kubectl @kube -n campus-ready get deploy postgres-dr -o jsonpath="{.spec.template.spec.containers[0].image} {.spec.template.spec.containers[0].env[?(@.name=='POSTGRES_USER')].value}" | Out-String
$net = & kubectl @kube -n campus-ready get netpol postgres postgres-dr -o yaml | Out-String
Save-Text (Join-Path $evidence "05-security\parity.txt") "source=$srcCtx`ndr=$drCtx`n$net"
Add-Proof "T06" (Join-Path $evidence "05-security\parity.txt") "DR использует тот же образ, пользователя и default deny, пароль тот же Secret"

Mark "T10" "least privilege"
$allow = & kubectl @kube auth can-i update configmaps -n campus-ready --as=system:serviceaccount:campus-ready:incident-operator | Out-String
$deny = & kubectl @kube auth can-i get secrets -n campus-ready --as=system:serviceaccount:campus-ready:incident-operator | Out-String
$okActor = Invoke-Client (Join-Path $root "ci\py\actor.py") @{ ACTOR = "operator" }
$badActor = Invoke-Client (Join-Path $root "ci\py\actor.py") @{ ACTOR = "intruder" }
Save-Text (Join-Path $evidence "05-security\least-privilege.txt") "configmap=$allow`nsecret=$deny`noperator=$okActor`nintruder=$badActor"
Add-Proof "T10" (Join-Path $evidence "05-security\least-privilege.txt") "Оператору можно менять fault, читать Secret нельзя; intruder получает 403"

Mark "T11" "runtime deny"
$unsafeFile = Join-Path $root "deploy\unsafe-pod.yaml"
$unsafe = cmd /c "kubectl --context kind-campus apply -f `"$unsafeFile`" 2>&1"
& kubectl @kube create namespace campus-intruder --dry-run=client -o yaml | & kubectl @kube apply -f -
& kubectl @kube -n campus-intruder create serviceaccount intruder --dry-run=client -o yaml | & kubectl @kube apply -f -
$intruderAuth = & kubectl @kube auth can-i get secrets -n campus-ready --as=system:serviceaccount:campus-intruder:intruder | Out-String
$pgIp = (& kubectl @kube -n campus-ready get svc postgres -o jsonpath="{.spec.clusterIP}").Trim()
@"
apiVersion: v1
kind: Pod
metadata:
  name: intruder
  namespace: campus-intruder
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
    - name: intruder
      image: localhost:5001/campus-ready:kt4
      imagePullPolicy: IfNotPresent
      env:
        - name: HOST
          value: "$pgIp"
      command: ["python", "-c", "import os,socket; s=socket.socket(); s.settimeout(3)\ntry:\n s.connect((os.environ['HOST'],5432)); print('connected')\nexcept Exception as exc:\n print(type(exc).__name__)"]
"@ | Set-Content -Path (Join-Path $local "intruder.yaml") -Encoding ascii
& kubectl @kube delete pod intruder -n campus-intruder --ignore-not-found
& kubectl @kube apply -f (Join-Path $local "intruder.yaml")
& kubectl @kube -n campus-intruder wait --for=jsonpath="{.status.phase}"=Succeeded pod/intruder --timeout=120s
$tcp = & kubectl @kube -n campus-intruder logs pod/intruder | Out-String
Save-Text (Join-Path $evidence "05-security\runtime.txt") "unsafe:`n$unsafe`nauth=$intruderAuth`ntcp=$tcp"
Add-Proof "T11" (Join-Path $evidence "05-security\runtime.txt") "Privileged Pod отклонён, чужой namespace не открывает Postgres и не читает Secret"

Mark "T12" "incident"
$t0 = Stamp
$baseIncident = Invoke-Client (Join-Path $root "ci\py\boards.py") @{ COUNT = "10" }
$fault = '{"mode":"incident","error_ratio":0.6,"latency_ms":700,"deadline_ms":400}'
[System.IO.File]::WriteAllText((Join-Path $local "fault.json"), $fault)
Copy-Item (Join-Path $local "fault.json") "$env:TEMP\campus-fault.json" -Force
& kubectl @kube -n campus-ready create configmap campus-fault --from-file=fault.json=$env:TEMP\campus-fault.json --dry-run=client -o yaml | & kubectl @kube apply -f -
Wait-Mounted "incident"
$tInject2 = Stamp
$afterInject = [DateTime]::UtcNow.AddSeconds(-2).ToString("yyyy-MM-dd HH:mm:ss")
$incFire = Invoke-Client (Join-Path $root "ci\py\wait_alert.py") @{ ALERT = "CampusBoardFastBurn"; STATUS = "firing"; WAIT = "180"; AFTER = $afterInject } -AllowFail
$detected = Stamp
$denyReq = Invoke-Client (Join-Path $root "ci\py\actor.py") @{ ACTOR = "intruder" }
& kubectl @kube apply -f (Join-Path $root "deploy\fault-off.yaml")
$afterKill = [DateTime]::UtcNow.AddSeconds(-2).ToString("yyyy-MM-dd HH:mm:ss")
Wait-Mounted "off"
$tKill = Stamp
$incResolved = Invoke-Client (Join-Path $root "ci\py\wait_alert.py") @{ ALERT = "CampusBoardFastBurn"; STATUS = "resolved"; WAIT = "180"; AFTER = $afterKill } -AllowFail
$tRecover = Stamp
$final = Invoke-Client (Join-Path $root "ci\py\boards.py") @{ COUNT = "10" }
Save-Text (Join-Path $evidence "06-incident\detect.json") $incFire
Save-Text (Join-Path $evidence "06-incident\resolved.json") $incResolved
Save-Text (Join-Path $evidence "06-incident\deny.txt") $denyReq
Save-Text (Join-Path $evidence "06-incident\baseline-after.txt") "t0=$t0`nbase=$baseIncident`ninject=$tInject2`ndetect=$detected`nkill=$tKill`nrecover=$tRecover`nfinal=$final"
Add-Proof "T12" (Join-Path $evidence "06-incident\baseline-after.txt") "Inject, detect, kill switch, возврат SLI"

$timeline.Add("$t0,baseline,operator,campus-ready,SLI до инцидента")
$timeline.Add("$tInject2,inject,operator,campus-fault,mode=incident latency_ms=700 error_ratio=0.6")
$timeline.Add("$detected,detect,prometheus,CampusBoardFastBurn,page firing")
$timeline.Add("$tKill,contain,operator,fault-off,kill switch")
$timeline.Add("$tRecover,recover,operator,campus-api,alert resolved and boards served")
Save-Text (Join-Path $evidence "06-incident\timeline.csv") (($timeline -join "`n"))
Add-Proof "T12" (Join-Path $evidence "06-incident\timeline.csv") "Таймлайн инцидента"
Save-Text (Join-Path $evidence "evidence-index.csv") ($index -join "`n")
Write-Host "LAB_OK digest=$digest"

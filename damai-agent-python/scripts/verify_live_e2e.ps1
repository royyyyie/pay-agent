[CmdletBinding()]
param(
    [string]$AgentBaseUrl = 'http://127.0.0.1:9010',
    [string]$JavaBaseUrl = 'http://127.0.0.1:6086',
    [string]$AgentInternalKey = '',
    [string]$JavaLogPath = '',
    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 15
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Condition {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Resolve-JavaLogPath {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }

    $repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $candidates = @(
        (Join-Path $repositoryRoot 'logs\program-service\log.log'),
        (Join-Path $repositoryRoot 'damai-server\damai-program-service\logs\program-service\log.log')
    ) | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { Get-Item -LiteralPath $_ }

    $selected = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $selected) {
        throw '找不到 Java Program Service 日志；请通过 -JavaLogPath 指定 log.log。'
    }
    return $selected.FullName
}

$javaHealth = Invoke-RestMethod `
    -Method Get `
    -Uri "$($JavaBaseUrl.TrimEnd('/'))/actuator/health" `
    -TimeoutSec $TimeoutSeconds
Assert-Condition ($javaHealth.status -eq 'UP') 'Java Program Service 健康检查未达到 UP。'

$agentHealth = Invoke-RestMethod `
    -Method Get `
    -Uri "$($AgentBaseUrl.TrimEnd('/'))/health" `
    -TimeoutSec $TimeoutSeconds
Assert-Condition ($agentHealth.status -eq 'UP') 'Python Agent 健康检查未达到 UP。'

$requiredTools = @('search_programs', 'get_program_detail', 'list_ticket_categories')
foreach ($tool in $requiredTools) {
    Assert-Condition ($agentHealth.tools -contains $tool) "Python Agent 未注册工具：$tool"
}

$headers = @{}
if ($AgentInternalKey) {
    $headers['X-Agent-Internal-Key'] = $AgentInternalKey
}
$sessionKey = "phase0-e2e-$([guid]::NewGuid().ToString('N'))"
$body = @{
    message = '帮我查询周杰伦的演唱会'
    sessionKey = $sessionKey
} | ConvertTo-Json -Compress

$chat = Invoke-RestMethod `
    -Method Post `
    -Uri "$($AgentBaseUrl.TrimEnd('/'))/api/v1/chat" `
    -Headers $headers `
    -ContentType 'application/json' `
    -Body $body `
    -TimeoutSec $TimeoutSeconds

Assert-Condition ($chat.sessionKey -eq $sessionKey) 'Agent 返回了错误的 sessionKey。'
Assert-Condition ($chat.turnId -match '^turn-[0-9a-f-]{36}$') 'Agent 返回的 turnId 不合法。'
Assert-Condition ($chat.traceId -match '^[0-9a-f]{32}$') 'Agent 返回的 traceId 不合法。'
Assert-Condition ($chat.toolCalls -contains 'search_programs') 'Agent 未执行 search_programs。'
Assert-Condition (-not $chat.answer.StartsWith('查询失败：')) "Java Tool 调用失败：$($chat.answer)"

$resolvedJavaLogPath = Resolve-JavaLogPath $JavaLogPath
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$correlationText = "[trace=$($chat.traceId)] [turn=$($chat.turnId)]"
$traceLogFound = $false
do {
    $traceLogFound = [bool](Select-String `
        -LiteralPath $resolvedJavaLogPath `
        -SimpleMatch `
        -Pattern $correlationText `
        -Quiet)
    if (-not $traceLogFound) {
        Start-Sleep -Milliseconds 250
    }
} while (-not $traceLogFound -and [DateTime]::UtcNow -lt $deadline)

Assert-Condition $traceLogFound `
    "Java 日志中未找到 traceId=$($chat.traceId) 与 turnId=$($chat.turnId) 的关联记录。"

[pscustomobject]@{
    Status = 'PASS'
    JavaHealth = $javaHealth.status
    AgentHealth = $agentHealth.status
    SessionKey = $chat.sessionKey
    TurnId = $chat.turnId
    TraceId = $chat.traceId
    ToolCalls = ($chat.toolCalls -join ',')
    JavaLogPath = $resolvedJavaLogPath
}

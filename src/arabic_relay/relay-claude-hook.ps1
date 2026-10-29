param([Parameter(Mandatory=$true)][string]$RegistrationDirectory)
# Runs only as a Claude Code hook. No output: it must never add text to a conversation.
$ErrorActionPreference = 'Stop'
try {
    $eventData = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $sessionId = [string]$eventData.session_id
    if ($sessionId -notmatch '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$') { exit 0 }
    $recordFile = Join-Path $RegistrationDirectory ($sessionId + '.bin')
    if ($eventData.hook_event_name -eq 'SessionEnd') {
        if (Test-Path -LiteralPath $recordFile) {
            Add-Type -AssemblyName System.Security
            $previous = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($recordFile), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
            $registered = [Text.Encoding]::UTF8.GetString($previous) | ConvertFrom-Json
            if ($registered.socket -eq $env:CLAUDE_CODE_MESSAGING_SOCKET) { Remove-Item -LiteralPath $recordFile }
        }
        exit 0
    }
    $pipeAddress = $env:CLAUDE_CODE_MESSAGING_SOCKET
    $sessionToken = $env:CLAUDE_CODE_MESSAGING_TOKEN
    if (-not $pipeAddress -or -not $pipeAddress.StartsWith('\\.\pipe\') -or -not $sessionToken) { exit 0 }
    if (-not $eventData.transcript_path) { exit 0 }
    [void][IO.Directory]::CreateDirectory($RegistrationDirectory)
    $record = @{ sessionId=$sessionId; socket=$pipeAddress; token=$sessionToken; transcript=[string]$eventData.transcript_path; cwd=[string]$eventData.cwd }
    Add-Type -AssemblyName System.Security
    $encoded = [Text.Encoding]::UTF8.GetBytes(($record | ConvertTo-Json -Compress))
    $encrypted = [Security.Cryptography.ProtectedData]::Protect($encoded, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    $temporaryFile = $recordFile + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllBytes($temporaryFile, $encrypted)
    Move-Item -LiteralPath $temporaryFile -Destination $recordFile -Force
} catch { exit 0 }

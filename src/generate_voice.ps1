param([string]$LessonPath, [string]$OutputDir)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$lesson = Get-Content -Raw -LiteralPath $LessonPath | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'en-US' } | Select-Object -First 1
if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) }
$synth.Rate = -1
$synth.Volume = 100
for ($i = 0; $i -lt $lesson.scenes.Count; $i++) {
  $path = Join-Path $OutputDir ('voice_{0:D2}.wav' -f $i)
  $synth.SetOutputToWaveFile($path)
  $synth.Speak($lesson.scenes[$i].narration)
  $synth.SetOutputToNull()
}
$synth.Dispose()

param(
  [string]$InputDoc,
  [string]$PdfPath,
  [string]$TextPath,
  [switch]$UpdateAndSave,
  [string]$TaskbookPath,
  [string]$TaskbookText
)
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class TaskWordWindow {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
}
'@
$existingWordIds = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$wordApp = $null
$ownedInstance = $false
$openedDocument = $null
try {
  $wordApp = New-Object -ComObject Word.Application
  $newWordIds = @(Get-Process WINWORD -ErrorAction SilentlyContinue | Where-Object { $existingWordIds -notcontains $_.Id } | ForEach-Object { $_.Id })
  if (($newWordIds.Count -ne 1) -or ($wordApp.Documents.Count -ne 0)) {
    throw 'Word did not provide a separate empty instance. Existing user documents were not touched.'
  }
  $ownedInstance = $true
  $wordApp.Visible = $false
  $wordApp.DisplayAlerts = 0
  $wordApp.AutomationSecurity = 3
  $wordApp.Options.UpdateLinksAtOpen = $false
  if ($TaskbookPath) {
    $openedDocument = $wordApp.Documents.Open($TaskbookPath, $false, $true, $false)
    [System.IO.File]::WriteAllText($TaskbookText, $openedDocument.Content.Text, [System.Text.UTF8Encoding]::new($false))
    $openedDocument.Close(0)
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($openedDocument)
    $openedDocument = $null
    Write-Output "Taskbook extracted: $TaskbookText"
  }
  if ($InputDoc) {
    $openedDocument = $wordApp.Documents.Open($InputDoc, $false, (-not $UpdateAndSave.IsPresent), $false)
    if ($UpdateAndSave) {
      [void]$openedDocument.Fields.Update()
      foreach ($toc in $openedDocument.TablesOfContents) { $toc.Update() }
      $openedDocument.Repaginate()
      foreach ($toc in $openedDocument.TablesOfContents) { $toc.UpdatePageNumbers() }
      $openedDocument.Save()
    }
    $openedDocument.Repaginate()
    if ($TextPath) {
      [System.IO.File]::WriteAllText($TextPath, $openedDocument.Content.Text, [System.Text.UTF8Encoding]::new($false))
    }
    if ($PdfPath) { $openedDocument.ExportAsFixedFormat($PdfPath, 17) }
    Write-Output "Pages: $($openedDocument.ComputeStatistics(2))"
    Write-Output "PDF: $PdfPath"
    $openedDocument.Close(0)
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($openedDocument)
    $openedDocument = $null
  }
} finally {
  if ($openedDocument) { $openedDocument.Close(0); [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($openedDocument) }
  if ($wordApp) {
    if ($ownedInstance) { $labNoSave=0; $wordApp.Quit([ref]$labNoSave) }
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp)
  }
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}


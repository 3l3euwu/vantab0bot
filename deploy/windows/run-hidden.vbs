' ────────────────────────────────────────────────────────────────────────────
'  Vantab · start run-vantab.bat with no visible console window.
'  Called by the Scheduled Task created by manage-vantab.ps1.
' ────────────────────────────────────────────────────────────────────────────
Option Explicit

Dim fso, shell, here, root, launcher

Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)          ' …\deploy\windows
root = fso.GetParentFolderName(fso.GetParentFolderName(here))   ' repository root
launcher = here & "\run-vantab.bat"

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = root
shell.Run "cmd /c """ & launcher & """", 0, False

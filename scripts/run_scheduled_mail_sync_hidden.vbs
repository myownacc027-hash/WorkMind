Set Shell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")

ScriptDir = Fso.GetParentFolderName(WScript.ScriptFullName)
PowerShellExe = Shell.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
PowerShellScript = ScriptDir & "\run_scheduled_mail_sync.ps1"

Command = """" & PowerShellExe & """ -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & PowerShellScript & """"
ExitCode = Shell.Run(Command, 0, True)
WScript.Quit ExitCode

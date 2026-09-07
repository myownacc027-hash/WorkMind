Set shell = CreateObject("WScript.Shell")
scriptPath = WScript.Arguments(0)
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptPath & """", 0, True

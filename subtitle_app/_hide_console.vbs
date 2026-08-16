' Relaunch a .bat in a hidden console. The hidden cmd waits for the GUI
' process; when the program exits, that cmd exits too (no leftover window).
' Usage: wscript //nologo _hide_console.vbs <script.bat> --hidden
Option Explicit

If WScript.Arguments.Count < 1 Then
  WScript.Quit 1
End If

Dim sh, cmd, i, arg
Set sh = CreateObject("WScript.Shell")
cmd = "cmd.exe /d /c call"
For i = 0 To WScript.Arguments.Count - 1
  arg = WScript.Arguments(i)
  cmd = cmd & " """ & Replace(arg, """", """""") & """"
Next
' 0 = hidden; False = do not wait so the visible .bat can close immediately.
sh.Run cmd, 0, False

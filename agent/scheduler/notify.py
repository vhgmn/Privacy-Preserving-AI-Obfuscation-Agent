"""Windows toast notifications through PowerShell, without extra Python packages."""
import os
import subprocess

# Title and message travel in environment variables, never inside the script text,
# so nothing in them can be run as PowerShell code.
_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($env:DECOY_TOAST_TITLE)) > $null
$texts.Item(1).AppendChild($template.CreateTextNode($env:DECOY_TOAST_MESSAGE)) > $null
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
    [Windows.UI.Notifications.ToastNotification]::new($template))
"""


def toast(title: str, message: str) -> bool:
    env = {**os.environ, "DECOY_TOAST_TITLE": title[:100], "DECOY_TOAST_MESSAGE": message[:250]}
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
                                env=env, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0

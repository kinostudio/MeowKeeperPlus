$srcWeb = "C:\Users\user\Desktop\MeowKeeperPlus-Workspace\src\meowkeeper\app\web"
$dstWeb = "C:\ProgramData\MeowKeeperPlus\App\src\meowkeeper\app\web"
$files = @(
  "templates\_term_help.html",
  "templates\base.html",
  "templates\dashboard.html",
  "templates\live.html",
  "templates\events.html",
  "templates\_event_review.html",
  "templates\analytics.html",
  "templates\settings.html",
  "templates\diagnostics.html",
  "static\css\term-help.css",
  "static\css\monitor.css",
  "static\js\term-help.js"
)
foreach ($f in $files) {
  Copy-Item "$srcWeb\$f" "$dstWeb\$f" -Force
}

#!/bin/sh
# Builds docs/guide.html (the online user guide on GitHub Pages) from docs/GUIDE.md.
# Run it after editing GUIDE.md:  sh docs/build-guide.sh   (needs pandoc: brew install pandoc)
set -e
cd "$(dirname "$0")"
REPO=https://github.com/rtomkowski/work-dashboard/blob/main

# Links that point outside docs/ (../README.md and so on) go to the files on GitHub.
BODY=$(sed -E "s#\]\(\.\./([^)]*)\)#](${REPO}/\1)#g" GUIDE.md | pandoc -f gfm -t html5)

ICON="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect x='2' y='3' width='20' height='18' rx='4' fill='%235a54d6'/%3E%3Crect x='5' y='7' width='5' height='11' rx='1.5' fill='%23fff'/%3E%3Crect x='12' y='7' width='7' height='4' rx='1.5' fill='%23fff'/%3E%3Crect x='12' y='13' width='7' height='5' rx='1.5' fill='%23a9a5ff'/%3E%3C/svg%3E"

{
cat <<EOF
<!doctype html>
<!-- Work Dashboard. Copyright (c) 2026 Robert Tomkowski. MIT License. Generated from GUIDE.md by build-guide.sh: edit GUIDE.md, not this file. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Work Dashboard User Guide</title>
<meta name="description" content="Every view, dialog and action of Work Dashboard, one feature at a time, with a screenshot of each.">
<link rel="icon" href="${ICON}">
<link rel="stylesheet" href="topbar.css">
<link rel="stylesheet" href="site.css">
</head>
<body>
<header class="topbar"><div class="in">
  <a class="brand" href="./"><svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="3" width="20" height="18" rx="4" fill="#5a54d6"/><rect x="5" y="7" width="5" height="11" rx="1.5" fill="#fff"/><rect x="12" y="7" width="7" height="4" rx="1.5" fill="#fff"/><rect x="12" y="13" width="7" height="5" rx="1.5" fill="#a9a5ff"/></svg>Work Dashboard</a>
  <nav aria-label="Site">
    <a href="./">Home</a>
    <a href="guide.html" aria-current="page">Guide</a>
    <a href="anatomy/">Anatomy</a>
    <a href="./#install">Install</a>
    <a href="https://github.com/rtomkowski/work-dashboard">GitHub</a>
  </nav>
</div></header>
<main class="guide" id="top">
EOF
printf '%s\n' "$BODY"
cat <<EOF
</main>
<a class="totop" href="#top">↑ Top</a>
<footer>
  © 2026 Robert Tomkowski · <a href="${REPO}/LICENSE">MIT License</a> ·
  Developed with the assistance of Claude Code · <a href="${REPO}/docs/GUIDE.md">This guide on GitHub</a>
</footer>
</body>
</html>
EOF
} > guide.html
echo "Wrote $(pwd)/guide.html"

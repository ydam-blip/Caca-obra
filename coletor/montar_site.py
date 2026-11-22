#!/usr/bin/env python3
"""Gera index.html (versão site/PWA) a partir de app.html."""
import os
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = open(os.path.join(B, "app.html"), encoding="utf-8").read()
head = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1d2327">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icone.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icone-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
"""
i = app.index("<header")
html = head + app[:i] + "</head>\n<body>\n" + app[i:] + "\n</body>\n</html>\n"
open(os.path.join(B, "index.html"), "w", encoding="utf-8").write(html)
print("index.html gerado")

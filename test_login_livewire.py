"""Analizar la pagina de login de series.ly para encontrar bypass de Turnstile."""
import re
import requests
from urllib.parse import quote

PROXY = "https://mw-relay.israeldm93.workers.dev"

s = requests.Session()
s.headers.update({
    "User-Agent": ("Mozilla/5.0 (Linux; Android 12; SHIELD Android TV) "
                   "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"),
})

def pget(url):
    return s.get(f"{PROXY}/?u={quote(url, safe='')}", timeout=30)

r = pget("https://series.ly/ingresar")
html = r.text

# 1. Find Livewire component
print("=== Livewire Analysis ===")
lw_components = re.findall(r'wire:id="([^"]+)"', html)
print(f"  Components: {lw_components}")

lw_initials = re.findall(r'wire:initial-data="([^"]*)"', html)
for i, d in enumerate(lw_initials):
    print(f"  Initial data {i}: {d[:200]}...")

# Find wire:snapshot
snapshots = re.findall(r'wire:snapshot="([^"]*)"', html)
for i, s_data in enumerate(snapshots):
    # HTML-decode
    import html as htmlmod
    decoded = htmlmod.unescape(s_data)
    print(f"  Snapshot {i}: {decoded[:300]}...")

# 2. Find form details
print("\n=== Form Details ===")
forms = re.findall(r'<form[^>]*>(.*?)</form>', html, re.S | re.I)
print(f"  {len(forms)} forms found")
for i, form in enumerate(forms):
    action = re.search(r'action="([^"]*)"', form)
    method = re.search(r'method="([^"]*)"', form)
    wire_submit = re.search(r'wire:submit[^=]*="([^"]*)"', form)
    inputs = re.findall(r'<input[^>]*name="([^"]*)"[^>]*/?\s*>', form)
    print(f"\n  Form {i}:")
    print(f"    action: {action.group(1) if action else 'none'}")
    print(f"    method: {method.group(1) if method else 'none'}")
    print(f"    wire:submit: {wire_submit.group(1) if wire_submit else 'none'}")
    print(f"    inputs: {inputs}")

    # Check for turnstile in this form
    if "turnstile" in form.lower():
        print(f"    TURNSTILE: present in form")
        tk = re.search(r'cf-turnstile[^>]*data-sitekey="([^"]*)"', form)
        if tk:
            print(f"    Turnstile sitekey: {tk.group(1)}")
    else:
        print(f"    TURNSTILE: NOT in form")

# 3. Find wire:model bindings
print("\n=== Wire:model Bindings ===")
models = re.findall(r'wire:model[^=]*="([^"]*)"', html)
print(f"  Bindings: {models}")

# 4. Check for Livewire endpoint
print("\n=== Livewire Endpoint ===")
lw_endpoint = re.search(r'livewire["\']?\s*:\s*["\']?([^"\'}\s,]+)', html)
if lw_endpoint:
    print(f"  Endpoint: {lw_endpoint.group(1)}")

# 5. Check all script tags for login-related endpoints
print("\n=== Script Analysis ===")
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
for i, script in enumerate(scripts):
    if any(kw in script.lower() for kw in ['login', 'ingresar', 'livewire', 'auth', 'turnstile']):
        # Show first 300 chars
        clean = script.strip()[:400]
        print(f"  Script {i}: {clean}")
        print("  ---")

# 6. Turnstile details
print("\n=== Turnstile Config ===")
tk_divs = re.findall(r'<div[^>]*cf-turnstile[^>]*>', html)
for d in tk_divs:
    print(f"  {d}")

# Check if turnstile response field is a form input
tk_input = re.findall(r'name=["\'][^"\']*turnstile[^"\']*["\']', html, re.I)
print(f"  Turnstile input fields: {tk_input}")

# 7. Look for wire:submit to understand Livewire call
ws = re.findall(r'wire:submit[^>]*', html)
for w in ws:
    print(f"  wire:submit: {w}")

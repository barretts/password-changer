#!/usr/bin/env bash
set -uo pipefail

LENGTH="${1:-24}"
if (( LENGTH < 12 )); then LENGTH=12; fi

if command -v openssl &>/dev/null; then
  base="$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-"$LENGTH")"
else
  base="$(python3 -c "import secrets,string; print(secrets.token_urlsafe(48)[:${LENGTH}])")"
fi

SYMBOLS='!@#$%&*'
inject="$(python3 -c "
import random, string
sym = random.choice('${SYMBOLS}')
digit = random.choice(string.digits)
upper = random.choice(string.ascii_uppercase)
lower = random.choice(string.ascii_lowercase)
print(f'{upper}{lower}{sym}{digit}')
")"

result="${base:0:1}${inject:0:1}${base:2:1}${inject:1:1}${base:4:1}${inject:2:1}${base:6:1}${inject:3:1}${base:8}"
echo "${result:0:$LENGTH}"

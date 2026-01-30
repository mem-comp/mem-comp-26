#!/bin/bash

if [ "$EUID" -ne 0 ]; then
  echo "please run as root!"
  exit 1
fi

echo === configuring docker network

docker network create --internal infra
docker network create internet

INTERFACE="br-$(docker network inspect internet --format '{{.Id}}' | cut -c 1-12)"

if ! iptables -C DOCKER-USER -i "$INTERFACE" -d "10.0.0.0/8" -j REJECT 2>/dev/null; then
    echo adding ipv4 rule
    iptables -I DOCKER-USER -i "$INTERFACE" -d "10.0.0.0/8" -j REJECT
fi

if command -v ip6tables >/dev/null; then
    if ! ip6tables -C DOCKER-USER -i "$INTERFACE" -d "fc00::/7" -j REJECT 2>/dev/null; then
        echo adding ipv6 rule
        ip6tables -I DOCKER-USER -i "$INTERFACE" -d "fc00::/7" -j REJECT
    fi
fi

echo === starting llm service

pushd litellm > /dev/null

env_file=".example.env"
if [ -f ".bd.env" ]; then
    env_file=".bd.env"
fi
BUILDKIT_PROGRESS=plain docker compose --env-file $env_file up -d --build

popd > /dev/null

while ! curl -s -o /dev/null -m 2 -w "%{http_code}" http://127.0.0.1:4000/health/liveliness | grep -q "200"; do
    echo "waiting for llm service to be ready..."
    sleep 3
done

echo === creating test key

sleep 1
curl http://127.0.0.1:4001/test_key/reset

echo
echo use this key to test your agent. to reset the key \(and its quota\), run:
echo curl http://127.0.0.1:4001/test_key/reset

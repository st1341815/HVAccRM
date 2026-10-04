#!/bin/sh
cd /vol1/1000/Docker/Hvaccrm || exit 1
exec > /vol1/1000/Docker/Hvaccrm/build_result.log 2>&1
docker image tag hvaccrm:local hvaccrm:pre-0.14.16
docker compose build && docker compose up -d
echo "BUILD_EXIT=$?"

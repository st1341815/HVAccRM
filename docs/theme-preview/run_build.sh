#!/bin/sh
# 一次性 docker 构建 wrapper（部署完成即删）
# NAS 上没有 docker:cli 镜像，用 alpine + apk 装 cli 代跑 compose_build.sh
apk add --no-cache docker-cli docker-cli-compose >/dev/null 2>&1
sh /vol1/1000/Docker/Hvaccrm/compose_build.sh

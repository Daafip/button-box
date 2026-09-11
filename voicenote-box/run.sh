#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

# MQTT credentials come from the Supervisor's broker service rather than the
# add-on options, so the operator never types them twice. Each value is
# assigned separately: a failing bashio call must stop the script, not be
# swallowed by export's own exit status.
if bashio::services.available "mqtt"; then
  VOICENOTE_MQTT_HOST="$(bashio::services mqtt "host")"
  VOICENOTE_MQTT_PORT="$(bashio::services mqtt "port")"
  VOICENOTE_MQTT_USERNAME="$(bashio::services mqtt "username")"
  VOICENOTE_MQTT_PASSWORD="$(bashio::services mqtt "password")"
  export VOICENOTE_MQTT_HOST VOICENOTE_MQTT_PORT
  export VOICENOTE_MQTT_USERNAME VOICENOTE_MQTT_PASSWORD
else
  bashio::log.warning "No MQTT service; the box will run without Home Assistant entities."
fi

if ! bashio::config.has_value "api_token"; then
  bashio::log.warning "api_token is empty; the control API will refuse every command."
fi

exec python3 -m voicenote_box

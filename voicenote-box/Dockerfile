ARG BUILD_FROM
FROM ${BUILD_FROM}

# ffmpeg transcodes recordings to Opus on the way out and to WAV on the way
# in. paho-mqtt publishes the Home Assistant entities. Everything else the
# add-on needs is in the standard library.
RUN apk add --no-cache ffmpeg python3 py3-pip \
    && pip3 install --break-system-packages --no-cache-dir "paho-mqtt>=2.1,<3"

COPY voicenote_box /opt/voicenote_box/voicenote_box
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENV PYTHONPATH=/opt/voicenote_box \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["/run.sh"]

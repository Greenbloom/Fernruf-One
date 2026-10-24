"""ARI chan_websocket dial string (Pipecat requires slin on the media leg)."""

# d(both): send bridged party audio to the app (required for M2 callee → Pipecat input).
WS_MEDIA_ENDPOINT = "WebSocket/fernruf/c(slin16)f(json)d(both)"

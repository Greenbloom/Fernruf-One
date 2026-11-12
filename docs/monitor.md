# Local call monitor

`fernruf serve` listens on `127.0.0.1:8791` for a window on the same machine. Override the port with `FERNRUF_MONITOR_PORT`. The socket is loopback only. It does not follow `FERNRUF_BIND_OVERRIDE`. Set `FERNRUF_MONITOR_ENABLED=false` to leave it closed.

Auth is `FERNRUF_MONITOR_TOKEN` when that is set. Otherwise the monitor uses the static `FERNRUF_MCP_TOKEN`. An OAuth-only service has no monitor until one of those tokens is set.

On the Plasma machine:

```bash
uv sync --extra desktop
uv run --extra desktop fernruf monitor
```

The process sits in the tray. When a call starts, the window opens and comes to the front. Set `FERNRUF_MONITOR_AUTO_SHOW` to `false` to leave the window hidden until you open it from the tray. The values `0`, `no`, and `off` do the same. Leave the variable unset to keep the automatic window.

Minimize, maximize, and close sit at the top right. Close hides the window. The call keeps running, and `fernruf serve` keeps running. Maximize fills the screen with square corners. Restore brings the rounded window back.

The layout is `fernruf/desktop/MonitorPlaceholder.qml`. The language follows the system locale, and English is the fallback. Set `FERNRUF_UI_LANG` to `en` or `de` to override that.

Listening is off for each call until you turn it on. That choice is not saved. Volume and mute are saved in `~/.config/fernruf/listen.json` and affect only playback on this computer. The keypad does not send tones. Hang-up stays off. The window cannot speak into the call. Nothing from this socket leaves the machine.

## Text frames

Text frames are JSON with `"v": 1`.

- If the handshake has no `Authorization: Bearer` header, the client sends `{"type":"auth","token":"..."}`. The server replies `{"type":"auth_ok"}`.
- The client sends `{"type":"listen","enabled":true}`. Audio is not sent until this is true. The server replies with the same shape.
- Lifecycle messages are `{"type":"lifecycle","call_id","state","at","to_masked","outcome","summary"}`. `state` is `dialing`, `ringing`, `answered`, `ended`, then `finalized`. `to_masked` keeps the country code and the last four digits. `summary` is present only on `finalized`.
- Transcript messages are `{"type":"transcript","call_id","speaker","text","t_ms","final"}`. `speaker` is `agent` or `callee`. Current calls send `final: true`.

## Binary frames

Binary frames are little-endian `FR1`. The direction byte is 1 for the callee and 2 for the bot's speech. Then the frame carries the sample rate, the channel count, a timestamp, the call id, and s16le PCM. A subscriber that falls behind loses frames. The call does not wait.

The struct layout lives in `fernruf/monitor/protocol.py`.

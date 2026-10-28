from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fernruf.data_dir import open_append, prepare_parent


@dataclass
class TurnLatency:
    call_id: str
    turn: int
    llm_provider: str
    llm_model: str
    simulated: bool = False
    vad_end_ms: float | None = None
    stt_final_ms: float | None = None
    llm_first_token_ms: float | None = None
    llm_to_tts_first_audio_ms: float | None = None
    fish_ttfa_ms: float | None = None
    tts_first_audio_ms: float | None = None
    rtp_first_out_ms: float | None = None
    _marks: dict[str, float] = field(default_factory=dict, repr=False)

    def mark(self, name: str) -> None:
        self._marks[name] = time.monotonic()

    def delta_ms(self, start: str, end: str) -> float | None:
        if start not in self._marks or end not in self._marks:
            return None
        return (self._marks[end] - self._marks[start]) * 1000

    def finalize_deltas(self) -> None:
        if "user_turn_stop" in self._marks and "stt_final" in self._marks:
            self.stt_final_ms = self.delta_ms("user_turn_stop", "stt_final")
        elif "vad_end" in self._marks and "stt_final" in self._marks:
            self.stt_final_ms = self.delta_ms("vad_end", "stt_final")
        if "user_turn_stop" in self._marks and "llm_first_token" in self._marks:
            self.llm_first_token_ms = self.delta_ms("user_turn_stop", "llm_first_token")
        elif "vad_end" in self._marks and "llm_first_token" in self._marks:
            self.llm_first_token_ms = self.delta_ms("vad_end", "llm_first_token")
        if "llm_first_token" in self._marks and "fish_ttfa" in self._marks:
            self.llm_to_tts_first_audio_ms = self.delta_ms("llm_first_token", "fish_ttfa")
        elif "llm_first_token" in self._marks and "tts_first_audio" in self._marks:
            self.llm_to_tts_first_audio_ms = self.delta_ms("llm_first_token", "tts_first_audio")
        if "user_turn_stop" in self._marks and "fish_ttfa" in self._marks:
            self.fish_ttfa_ms = self.delta_ms("user_turn_stop", "fish_ttfa")
        elif "user_turn_stop" in self._marks and "tts_first_audio" in self._marks:
            self.fish_ttfa_ms = self.delta_ms("user_turn_stop", "tts_first_audio")
        if "user_turn_stop" in self._marks and "tts_first_audio" in self._marks:
            self.tts_first_audio_ms = self.delta_ms("user_turn_stop", "tts_first_audio")
        elif "vad_end" in self._marks and "tts_first_audio" in self._marks:
            self.tts_first_audio_ms = self.delta_ms("vad_end", "tts_first_audio")
        if "user_turn_stop" in self._marks and "rtp_first_out" in self._marks:
            self.rtp_first_out_ms = self.delta_ms("user_turn_stop", "rtp_first_out")
        elif "vad_end" in self._marks and "rtp_first_out" in self._marks:
            self.rtp_first_out_ms = self.delta_ms("vad_end", "rtp_first_out")

    def to_json(self) -> dict[str, Any]:
        self.finalize_deltas()
        d = asdict(self)
        d.pop("_marks", None)
        return d


class LatencyLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        prepare_parent(self.path)
        self._turns: list[TurnLatency] = []

    def start_turn(
        self, call_id: str, turn: int, *, provider: str, model: str
    ) -> TurnLatency:
        row = TurnLatency(
            call_id=call_id,
            turn=turn,
            llm_provider=provider,
            llm_model=model,
        )
        self._turns.append(row)
        return row

    def append(self, row: TurnLatency) -> None:
        row.finalize_deltas()
        with open_append(self.path) as fh:
            fh.write(json.dumps(row.to_json(), ensure_ascii=False) + "\n")

    @classmethod
    def median_report_from_jsonl(
        cls,
        path: Path,
        *,
        call_id: str | None = None,
        include_simulated: bool = False,
    ) -> dict[str, float | None]:
        log = cls(path)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if call_id is not None and data.get("call_id") != call_id:
                    continue
                row = TurnLatency(
                    call_id=str(data.get("call_id", "")),
                    turn=int(data.get("turn", 0)),
                    llm_provider=str(data.get("llm_provider", "")),
                    llm_model=str(data.get("llm_model", "")),
                    simulated=bool(data.get("simulated", False)),
                    vad_end_ms=data.get("vad_end_ms"),
                    stt_final_ms=data.get("stt_final_ms"),
                    llm_first_token_ms=data.get("llm_first_token_ms"),
                    llm_to_tts_first_audio_ms=data.get("llm_to_tts_first_audio_ms"),
                    fish_ttfa_ms=data.get("fish_ttfa_ms"),
                    tts_first_audio_ms=data.get("tts_first_audio_ms"),
                    rtp_first_out_ms=data.get("rtp_first_out_ms"),
                )
                log._turns.append(row)
        return log.median_report(include_simulated=include_simulated)

    def median_report(self, *, include_simulated: bool = False) -> dict[str, float | None]:
        turns = self._turns if include_simulated else [t for t in self._turns if not t.simulated]

        def med(key: str) -> float | None:
            vals = [
                getattr(t, key)
                for t in turns
                if getattr(t, key) is not None
            ]
            if not vals:
                return None
            return float(statistics.median(vals))

        fish = med("fish_ttfa_ms")
        tts = med("tts_first_audio_ms")
        stt = med("stt_final_ms")
        report: dict[str, float | None] = {
            "llm_first_token_ms_median": med("llm_first_token_ms"),
            "fish_ttfa_ms_median": fish,
            "rtp_first_out_ms_median": med("rtp_first_out_ms"),
            "turns": float(len(turns)),
        }
        if stt is not None:
            report["stt_final_ms_median"] = stt
        if tts is not None:
            report["tts_first_audio_ms_median"] = tts
        elif fish is not None:
            report["tts_first_audio_ms_median"] = fish
        return report
